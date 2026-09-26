"""Capture DynamoDB baseline payloads and emit scenario fixtures for offline tests."""

from __future__ import annotations

import argparse
import json
import sys
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple

import boto3

CURRENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests_generator.dynamodb.dynamodb_resource_creation import (
    CONFIG_PATH,
    DynamoDBPayloadResourceManager,
    _load_json,
)
from tests_generator.capture_gate import require_capture_gate


CHECKS_PATH = Path(__file__).with_name("checks.json")
ACTIONS_PATH = Path(__file__).with_name("actions.json")
PAYLOAD_CHECK_MAP_PATH = REPO_ROOT / "tests_generator" / "payload_check_map.json"
PAYLOAD_ROOT = REPO_ROOT / "tests" / "payloads" / "dynamodb"
TABLE_METRICS = (
    "ConsumedReadCapacityUnits",
    "ConsumedWriteCapacityUnits",
    "ProvisionedReadCapacityUnits",
    "ProvisionedWriteCapacityUnits",
    "ReadThrottleEvents",
    "WriteThrottleEvents",
)
GSI_METRICS = ("ConsumedReadCapacityUnits", "ConsumedWriteCapacityUnits")
TABLE_METRIC_STATISTICS = {
    "ConsumedReadCapacityUnits": "Sum",
    "ConsumedWriteCapacityUnits": "Sum",
    "ProvisionedReadCapacityUnits": "Average",
    "ProvisionedWriteCapacityUnits": "Average",
    "ReadThrottleEvents": "Sum",
    "WriteThrottleEvents": "Sum",
}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except TypeError:
            return value
    return value


def _replace_strings(value: Any, reverse_map: Dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _replace_strings(item, reverse_map) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_strings(item, reverse_map) for item in value]
    if isinstance(value, str):
        rendered = value
        for actual, placeholder in sorted(reverse_map.items(), key=lambda item: len(item[0]), reverse=True):
            rendered = rendered.replace(actual, placeholder)
        return rendered
    return value


def _render_templates(value: Any, substitutions: Dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _render_templates(item, substitutions) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_templates(item, substitutions) for item in value]
    if isinstance(value, str):
        rendered = value
        for token, replacement in substitutions.items():
            rendered = rendered.replace(f"{{{{{token}}}}}", str(replacement))
        return rendered
    return value


def _sanitize_response(response: Dict[str, Any], reverse_map: Dict[str, str]) -> Dict[str, Any]:
    return _replace_strings(_json_safe(response), reverse_map)


def _table_metric_filename(alias: str, metric_name: str) -> str:
    return f"get_metric_statistics__{alias}__{metric_name.lower()}.json"


def _gsi_metric_filename(alias: str, index_name: str, metric_name: str) -> str:
    return f"get_metric_statistics__{alias}__{index_name}__{metric_name.lower()}.json"


def _parse_gsi_ref(ref: str) -> Tuple[str, str]:
    alias, index_name = ref.split(":", 1)
    return alias, index_name


def _validate_checks_contract(checks: Dict[str, Any]) -> None:
    forbidden_keys = {"inventory_overrides", "response_overrides", "describe_overrides", "tag_overrides"}
    for check_id, check_config in checks.items():
        for scenario_name, scenario_config in check_config.get("scenarios", {}).items():
            overlap = forbidden_keys.intersection(scenario_config.keys())
            if overlap:
                keys = ", ".join(sorted(overlap))
                raise ValueError(
                    f"DynamoDB payload scenario {check_id}/{scenario_name} uses forbidden structural override keys: {keys}"
                )


def _validate_actions_contract(actions: Dict[str, Any], state: Dict[str, Any]) -> None:
    required = {
        "scenario",
        "check_id",
        "description",
        "resource_ref",
        "parameters",
        "expected_status",
        "expected_message",
        "expected_calls",
    }
    resources = state.get("resources", {})
    for action_key, action_config in actions.items():
        missing = required.difference(action_config)
        if missing:
            raise ValueError(f"DynamoDB action {action_key} is missing fields: {', '.join(sorted(missing))}")
        if action_config["resource_ref"] not in resources:
            raise ValueError(
                f"DynamoDB action {action_key} references unknown resource {action_config['resource_ref']}"
            )
        if not action_config["expected_calls"]:
            raise ValueError(f"DynamoDB action {action_key} must declare at least one expected AWS call")


def _mapped_dynamodb_scenarios(
    checks: Dict[str, Any],
    payload_check_map: Dict[str, Any],
) -> Dict[str, List[str]]:
    mapped: Dict[str, List[str]] = {}
    for check_id, entry in payload_check_map.items():
        if entry.get("service") != "dynamodb":
            continue
        if check_id not in checks:
            raise ValueError(f"payload_check_map.json references unknown DynamoDB check {check_id}")
        scenario_names = entry.get("positive_scenarios", []) + entry.get("negative_scenarios", [])
        if not scenario_names:
            raise ValueError(f"payload_check_map.json has no scenarios for DynamoDB check {check_id}")
        known_scenarios = checks[check_id].get("scenarios", {})
        for scenario_name in scenario_names:
            if scenario_name not in known_scenarios:
                raise ValueError(
                    f"payload_check_map.json references unknown DynamoDB scenario {check_id}/{scenario_name}"
                )
        unmapped_scenarios = set(known_scenarios).difference(scenario_names)
        if unmapped_scenarios:
            raise ValueError(
                f"payload_check_map.json does not cover DynamoDB scenarios for {check_id}: "
                + ", ".join(sorted(unmapped_scenarios))
            )
        mapped[check_id] = list(dict.fromkeys(scenario_names))

    missing_checks = set(checks).difference(mapped)
    if missing_checks:
        raise ValueError(
            "payload_check_map.json does not cover DynamoDB checks: "
            + ", ".join(sorted(missing_checks))
        )
    return mapped


def _scenario_payload_complete(
    check_id: str,
    scenario_name: str,
    scenario_config: Dict[str, Any],
    payload_root: Path = PAYLOAD_ROOT,
) -> bool:
    scenario_dir = payload_root / check_id / scenario_name
    return all(
        (scenario_dir / filename).is_file()
        for filename in scenario_config.get("payload_files", [])
    )


def _select_check_scenarios(
    checks: Dict[str, Any],
    payload_check_map: Dict[str, Any],
    regenerate_all: bool = False,
    payload_root: Path = PAYLOAD_ROOT,
) -> Dict[str, Dict[str, Any]]:
    mapped = _mapped_dynamodb_scenarios(checks, payload_check_map)
    selected: Dict[str, Dict[str, Any]] = {}
    for check_id, scenario_names in mapped.items():
        scenarios = checks[check_id]["scenarios"]
        selected_scenarios = {
            scenario_name: scenarios[scenario_name]
            for scenario_name in scenario_names
            if regenerate_all
            or not _scenario_payload_complete(
                check_id,
                scenario_name,
                scenarios[scenario_name],
                payload_root,
            )
        }
        if selected_scenarios:
            selected[check_id] = {
                **checks[check_id],
                "scenarios": selected_scenarios,
            }
    return selected


def _action_payload_complete(
    action_key: str,
    action_config: Dict[str, Any],
    payload_root: Path = PAYLOAD_ROOT,
) -> bool:
    scenario_dir = payload_root / "actions" / action_key / action_config["scenario"]
    metadata_path = scenario_dir / "capture_metadata.json"
    if not metadata_path.is_file():
        return False
    try:
        metadata = _load_json(metadata_path)
    except (OSError, ValueError):
        return False
    calls = metadata.get("calls", [])
    expected_calls = action_config.get("expected_calls", [])
    if metadata.get("action_key") != action_key or metadata.get("scenario") != action_config["scenario"]:
        return False
    if [
        (call.get("service"), call.get("operation"))
        for call in calls
    ] != [
        (call.get("service"), call.get("operation"))
        for call in expected_calls
    ]:
        return False
    return bool(calls) and all(
        (scenario_dir / call.get("response_file", "")).is_file()
        for call in calls
    )


class _RecordingClient:
    def __init__(self, service: str, client: Any, calls: List[Dict[str, Any]]):
        self._service = service
        self._client = client
        self._calls = calls

    def __getattr__(self, operation: str):
        target = getattr(self._client, operation)

        def invoke(**kwargs: Any) -> Dict[str, Any]:
            response = target(**kwargs)
            self._calls.append(
                {
                    "service": self._service,
                    "operation": operation,
                    "kwargs": deepcopy(kwargs),
                    "response": deepcopy(response),
                }
            )
            return response

        return invoke


class _RecordingSession:
    def __init__(self, session: Any, calls: List[Dict[str, Any]]):
        self._session = session
        self._calls = calls
        self._clients: Dict[Tuple[str, str | None], _RecordingClient] = {}

    def client(self, service_name: str, region_name: str | None = None, endpoint_url: str | None = None):
        key = (service_name, region_name)
        if key not in self._clients:
            client = self._session.client(service_name, region_name=region_name, endpoint_url=endpoint_url)
            self._clients[key] = _RecordingClient(service_name, client, self._calls)
        return self._clients[key]


def _model_dict(value: Any) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    raise TypeError(f"Unsupported action response type: {type(value)!r}")


def _wait_for_table_active(client: Any, table_name: str) -> None:
    for _ in range(60):
        table = client.describe_table(TableName=table_name).get("Table", {})
        if table.get("TableStatus") == "ACTIVE":
            return
        time.sleep(10)
    raise TimeoutError(f"Timed out waiting for DynamoDB table {table_name} to become ACTIVE")


def _wait_for_gsi_deleted(client: Any, table_name: str, index_name: str) -> None:
    for _ in range(60):
        table = client.describe_table(TableName=table_name).get("Table", {})
        indexes = table.get("GlobalSecondaryIndexes", [])
        if table.get("TableStatus") == "ACTIVE" and all(
            index.get("IndexName") != index_name
            for index in indexes
        ):
            return
        time.sleep(10)
    raise TimeoutError(f"Timed out waiting for DynamoDB GSI {table_name}/{index_name} to be deleted")


def _track_action_artifacts(
    action_key: str,
    resource_ref: str,
    table_name: str,
    calls: List[Dict[str, Any]],
    state: Dict[str, Any],
) -> None:
    if action_key == "enable_autoscaling":
        known_policies = {
            (policy["policy_name"], policy["resource_id"], policy["scalable_dimension"])
            for policy in state.setdefault("autoscaling_policies", [])
        }
        known_targets = {
            (target["resource_id"], target["scalable_dimension"])
            for target in state.setdefault("autoscaling_targets", [])
        }
        for call in calls:
            if call["service"] != "application-autoscaling":
                continue
            if call["operation"] == "register_scalable_target":
                key = (call["kwargs"]["ResourceId"], call["kwargs"]["ScalableDimension"])
                if key not in known_targets:
                    state["autoscaling_targets"].append(
                        {
                            "resource_alias": resource_ref,
                            "resource_id": key[0],
                            "scalable_dimension": key[1],
                        }
                    )
                    known_targets.add(key)
            elif call["operation"] == "put_scaling_policy":
                policy_key = (
                    call["kwargs"]["PolicyName"],
                    call["kwargs"]["ResourceId"],
                    call["kwargs"]["ScalableDimension"],
                )
                if policy_key not in known_policies:
                    state["autoscaling_policies"].append(
                        {
                            "resource_alias": resource_ref,
                            "policy_name": policy_key[0],
                            "resource_id": policy_key[1],
                            "scalable_dimension": policy_key[2],
                        }
                    )
                    known_policies.add(policy_key)

    if action_key == "backup_and_delete_table":
        known_backups = {
            backup["backup_arn"]
            for backup in state.setdefault("backups", [])
        }
        for call in calls:
            if call["service"] != "dynamodb" or call["operation"] != "create_backup":
                continue
            backup_arn = call["response"].get("BackupDetails", {}).get("BackupArn")
            if backup_arn and backup_arn not in known_backups:
                state["backups"].append(
                    {
                        "resource_alias": resource_ref,
                        "backup_arn": backup_arn,
                    }
                )
                known_backups.add(backup_arn)


def _capture_baselines(
    session,
    region: str,
    state: Dict[str, Any],
    start_time: datetime,
    end_time: datetime,
) -> Dict[str, Dict[str, Any]]:
    dynamodb_client = session.client("dynamodb", region_name=region)
    cloudwatch_client = session.client("cloudwatch", region_name=region)
    baselines: Dict[str, Dict[str, Any]] = {
        "list_tables": dynamodb_client.list_tables(),
    }
    for alias, resource in state.get("resources", {}).items():
        table_name = resource["table_name"]
        table_arn = resource["table_arn"]
        baselines[f"describe_table__{alias}.json"] = dynamodb_client.describe_table(TableName=table_name)
        baselines[f"list_tags_of_resource__{alias}.json"] = dynamodb_client.list_tags_of_resource(ResourceArn=table_arn)
        for metric_name in TABLE_METRICS:
            statistic = TABLE_METRIC_STATISTICS[metric_name]
            baselines[_table_metric_filename(alias, metric_name)] = cloudwatch_client.get_metric_statistics(
                Namespace="AWS/DynamoDB",
                MetricName=metric_name,
                Dimensions=[{"Name": "TableName", "Value": table_name}],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,
                Statistics=[statistic],
            )
        for gsi in resource.get("global_secondary_indexes", []):
            index_name = gsi["index_name"]
            for metric_name in GSI_METRICS:
                baselines[_gsi_metric_filename(alias, index_name, metric_name)] = cloudwatch_client.get_metric_statistics(
                    Namespace="AWS/DynamoDB",
                    MetricName=metric_name,
                    Dimensions=[
                        {"Name": "TableName", "Value": table_name},
                        {"Name": "GlobalSecondaryIndexName", "Value": index_name},
                    ],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=86400,
                    Statistics=["Sum"],
                )
    return baselines


def _apply_metric_override(
    baseline: Dict[str, Any],
    override: Dict[str, Any],
    timestamp: datetime,
    default_statistic: str = "Average",
) -> Dict[str, Any]:
    payload = deepcopy(baseline)
    if override.get("empty"):
        payload["Datapoints"] = []
        return payload
    values = override.get("Values")
    if values is not None:
        statistic = override.get("Statistic") or default_statistic
        payload["Datapoints"] = [
            {
                statistic: float(value),
                "Timestamp": timestamp - timedelta(hours=index),
                "Unit": override.get("Unit", "Count"),
            }
            for index, value in enumerate(values)
        ]
        return payload
    datapoint: Dict[str, Any] = {
        "Timestamp": timestamp,
        "Unit": override.get("Unit", "Count"),
    }
    if "Average" in override:
        datapoint["Average"] = float(override["Average"])
    if "Sum" in override:
        datapoint["Sum"] = float(override["Sum"])
    if len(datapoint) == 2:
        return payload
    payload["Datapoints"] = [datapoint]
    return payload


def _finalize_cleanup(config_path: Path, state: Dict[str, Any], state_path: Path) -> Dict[str, Any]:
    manager = DynamoDBPayloadResourceManager(config_path=config_path, apply=True)
    summary = manager.cleanup_resources(state=state)
    summary["state_file"] = str(state_path)
    return summary


def _capture_action_payloads(
    session: Any,
    region: str,
    config: Dict[str, Any],
    state: Dict[str, Any],
    state_path: Path,
    actions: Dict[str, Any],
    reverse_map: Dict[str, str],
    write_action_keys: set[str],
) -> None:
    from app.actions import action_registry
    from app.actions.base import ActionExecutionContext
    from app.schemas.check import CheckActionRequest

    raw_dynamodb = session.client("dynamodb", region_name=region)

    for action_key, action_config in actions.items():
        resource_ref = action_config["resource_ref"]
        resource = state["resources"][resource_ref]
        table_name = resource["table_name"]
        gsi_name = _render_templates(
            action_config.get("gsi_name", ""),
            config.get("placeholder_values", {}),
        )
        actual_resource_id = f"{table_name}/{gsi_name}" if gsi_name else table_name
        placeholder_table = config["placeholder_values"][resource["placeholder_key"]]
        placeholder_resource_id = f"{placeholder_table}/{gsi_name}" if gsi_name else placeholder_table
        calls: List[Dict[str, Any]] = []
        recording_session = _RecordingSession(session, calls)
        payload = CheckActionRequest(
            action=action_key,
            account_id=state["account_id"],
            region=region,
            resource_id=actual_resource_id,
            parameters=deepcopy(action_config.get("parameters", {})),
        )
        context = ActionExecutionContext(
            check_id=action_config["check_id"],
            action_key=action_key,
            payload=payload,
            check=None,
            aws_adapter=SimpleNamespace(session=recording_session),
            db=None,
            action_execution=SimpleNamespace(status="running"),
        )
        try:
            response = action_registry.execute(context)
        except Exception:
            _track_action_artifacts(action_key, resource_ref, table_name, calls, state)
            _write_json(state_path, state)
            raise
        if response is None:
            raise RuntimeError(f"DynamoDB action {action_key} is not registered")
        _track_action_artifacts(action_key, resource_ref, table_name, calls, state)
        _write_json(state_path, state)
        if response.status != action_config["expected_status"] or response.message != action_config["expected_message"]:
            raise RuntimeError(
                f"DynamoDB action {action_key} response mismatch: "
                f"expected ({action_config['expected_status']!r}, {action_config['expected_message']!r}), "
                f"got ({response.status!r}, {response.message!r})"
            )

        expected_calls = [
            (item["service"], item["operation"])
            for item in action_config["expected_calls"]
        ]
        actual_calls = [(item["service"], item["operation"]) for item in calls]
        if actual_calls != expected_calls:
            raise RuntimeError(
                f"DynamoDB action {action_key} call mismatch: expected {expected_calls}, got {actual_calls}"
            )

        if action_key in write_action_keys:
            scenario_dir = PAYLOAD_ROOT / "actions" / action_key / action_config["scenario"]
            metadata_calls = []
            for index, call in enumerate(calls, start=1):
                response_file = f"{index:02d}_{call['service'].replace('-', '_')}__{call['operation']}.json"
                _write_json(
                    scenario_dir / response_file,
                    _sanitize_response(call["response"], reverse_map),
                )
                metadata_calls.append(
                    {
                        "service": call["service"],
                        "operation": call["operation"],
                        "kwargs": _replace_strings(_json_safe(call["kwargs"]), reverse_map),
                        "response_file": response_file,
                    }
                )

            response_payload = _model_dict(response)
            capture_metadata = {
                "action_key": action_key,
                "scenario": action_config["scenario"],
                "capture_source": "aws",
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "check_id": action_config["check_id"],
                "description": action_config["description"],
                "resource_ref": resource_ref,
                "resource_id": placeholder_resource_id,
                "account_id": config["placeholder_values"]["ACCOUNT_ID"],
                "region": region,
                "parameters": deepcopy(action_config.get("parameters", {})),
                "expected_status": action_config["expected_status"],
                "expected_message": action_config["expected_message"],
                "handler_response": _replace_strings(_json_safe(response_payload), reverse_map),
                "calls": metadata_calls,
            }
            _write_json(scenario_dir / "capture_metadata.json", capture_metadata)

        if action_key in {"use_provisioned_capacity", "switch_to_on_demand_billing", "reduce_rcu", "reduce_wcu"}:
            _wait_for_table_active(raw_dynamodb, table_name)
        elif action_key == "delete_gsi":
            _wait_for_gsi_deleted(raw_dynamodb, table_name, gsi_name)


def capture_payloads(
    config_path: Path,
    checks_path: Path,
    state_path: Path,
    actions_path: Path = ACTIONS_PATH,
    payload_check_map_path: Path = PAYLOAD_CHECK_MAP_PATH,
    capture_checks: bool = True,
    capture_actions: bool = True,
    regenerate_all_checks: bool = False,
    regenerate_all_actions: bool = False,
    apply: bool = False,
) -> None:
    require_capture_gate(apply)
    config = _load_json(config_path)
    checks = _load_json(checks_path)
    _validate_checks_contract(checks)
    payload_check_map = _load_json(payload_check_map_path)
    selected_checks = (
        _select_check_scenarios(
            checks,
            payload_check_map,
            regenerate_all=regenerate_all_checks,
            payload_root=PAYLOAD_ROOT,
        )
        if capture_checks
        else {}
    )
    state = _load_json(state_path)
    actions = _load_json(actions_path) if capture_actions else {}
    if capture_actions:
        _validate_actions_contract(actions, state)
    selected_action_keys = {
        action_key
        for action_key, action_config in actions.items()
        if regenerate_all_actions
        or not _action_payload_complete(action_key, action_config, PAYLOAD_ROOT)
    }
    print(
        json.dumps(
            {
                "selection_mode": {
                    "checks": "all" if regenerate_all_checks else "missing",
                    "actions": "all" if regenerate_all_actions else "missing",
                },
                "selected_checks": {
                    check_id: list(check_config["scenarios"])
                    for check_id, check_config in selected_checks.items()
                },
                "selected_actions": sorted(selected_action_keys),
            },
            indent=2,
            sort_keys=True,
        )
    )
    generation_error: Exception | None = None
    cleanup_summary: Dict[str, Any] | None = None

    try:
        capture_window_days = config.get("capture_window_days", 7)
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=capture_window_days)
        profile = config.get("profile")
        region = state.get("region") or config["region"]
        session = boto3.Session(profile_name=profile, region_name=region) if profile else boto3.Session(region_name=region)

        substitutions: Dict[str, Any] = dict(config.get("placeholder_values", {}))
        substitutions["REGION"] = region
        substitutions["ACCOUNT_ID"] = state.get("account_id", substitutions.get("ACCOUNT_ID"))
        reverse_map = {actual: placeholder for placeholder, actual in state.get("placeholder_map", {}).items()}
        if state.get("account_id") and config["placeholder_values"].get("ACCOUNT_ID"):
            reverse_map[state["account_id"]] = config["placeholder_values"]["ACCOUNT_ID"]
        for alias, resource in state.get("resources", {}).items():
            placeholder_name = config["placeholder_values"][resource["placeholder_key"]]
            substitutions[resource["placeholder_key"]] = resource["table_name"]
            reverse_map[resource["table_name"]] = placeholder_name
            if resource.get("table_arn"):
                reverse_map[resource["table_arn"]] = resource["table_arn"].replace(resource["table_name"], placeholder_name).replace(
                    state.get("account_id", ""),
                    config["placeholder_values"].get("ACCOUNT_ID", ""),
                )

        check_resource_aliases = {
            alias
            for check_config in selected_checks.values()
            for scenario_config in check_config.get("scenarios", {}).values()
            for alias in scenario_config.get("capture_tables", [])
        }
        baseline_state = {
            **state,
            "resources": {
                alias: resource
                for alias, resource in state.get("resources", {}).items()
                if alias in check_resource_aliases
            },
        }
        baselines = (
            _capture_baselines(session, region, baseline_state, start_time, end_time)
            if selected_checks
            else {}
        )

        for check_id, check_config in selected_checks.items():
            for scenario_name, scenario_config in check_config.get("scenarios", {}).items():
                scenario_dir = PAYLOAD_ROOT / check_id / scenario_name
                capture_tables = scenario_config.get("capture_tables", [])
                capture_gsis = scenario_config.get("capture_gsis", [])
                selected_table_names = {state["resources"][alias]["table_name"] for alias in capture_tables}
                table_placeholders = {
                    alias: config["placeholder_values"][state["resources"][alias]["placeholder_key"]]
                    for alias in capture_tables
                }
                gsi_placeholders = {
                    ref: f"{table_placeholders[_parse_gsi_ref(ref)[0]]}/{_parse_gsi_ref(ref)[1]}"
                    for ref in capture_gsis
                }

                calls: List[Dict[str, Any]] = []
                payload_files = scenario_config.get("payload_files", [])
                if "list_tables.json" in payload_files:
                    list_tables_payload = deepcopy(baselines["list_tables"])
                    list_tables_payload["TableNames"] = [
                        table_name
                        for table_name in list_tables_payload.get("TableNames", [])
                        if table_name in selected_table_names
                    ]
                    _write_json(scenario_dir / "list_tables.json", _sanitize_response(list_tables_payload, reverse_map))
                    calls.append(
                        {
                            "service": "dynamodb",
                            "operation": "list_tables",
                            "response_file": "list_tables.json",
                        }
                    )

                metric_overrides = scenario_config.get("metric_overrides", {})
                for alias in capture_tables:
                    resource = state["resources"][alias]
                    describe_filename = f"describe_table__{alias}.json"
                    if describe_filename in payload_files:
                        _write_json(
                            scenario_dir / describe_filename,
                            _sanitize_response(deepcopy(baselines[describe_filename]), reverse_map),
                        )
                        calls.append(
                            {
                                "service": "dynamodb",
                                "operation": "describe_table",
                                "table_alias": alias,
                                "table_name": table_placeholders[alias],
                                "response_file": describe_filename,
                            }
                        )

                    tags_filename = f"list_tags_of_resource__{alias}.json"
                    if tags_filename in payload_files:
                        _write_json(
                            scenario_dir / tags_filename,
                            _sanitize_response(deepcopy(baselines[tags_filename]), reverse_map),
                        )
                        calls.append(
                            {
                                "service": "dynamodb",
                                "operation": "list_tags_of_resource",
                                "table_alias": alias,
                                "table_name": table_placeholders[alias],
                                "response_file": tags_filename,
                            }
                        )

                    for metric_name in TABLE_METRICS:
                        filename = _table_metric_filename(alias, metric_name)
                        if filename not in payload_files:
                            continue
                        payload = _apply_metric_override(
                            baselines[filename],
                            metric_overrides.get(alias, {}).get(metric_name, {}),
                            end_time,
                            TABLE_METRIC_STATISTICS[metric_name],
                        )
                        _write_json(scenario_dir / filename, _sanitize_response(payload, reverse_map))
                        calls.append(
                            {
                                "service": "cloudwatch",
                                "operation": "get_metric_statistics",
                                "table_alias": alias,
                                "table_name": table_placeholders[alias],
                                "metric_name": metric_name,
                                "response_file": filename,
                            }
                        )

                for gsi_ref in capture_gsis:
                    alias, index_name = _parse_gsi_ref(gsi_ref)
                    for metric_name in GSI_METRICS:
                        filename = _gsi_metric_filename(alias, index_name, metric_name)
                        if filename not in payload_files:
                            continue
                        payload = _apply_metric_override(
                            baselines[filename],
                            metric_overrides.get(gsi_ref, {}).get(metric_name, {}),
                            end_time,
                            "Sum",
                        )
                        _write_json(scenario_dir / filename, _sanitize_response(payload, reverse_map))
                        calls.append(
                            {
                                "service": "cloudwatch",
                                "operation": "get_metric_statistics",
                                "gsi_ref": gsi_ref,
                                "gsi_id": gsi_placeholders[gsi_ref],
                                "metric_name": metric_name,
                                "response_file": filename,
                            }
                        )

                capture_metadata = {
                    "check_id": check_id,
                    "scenario": scenario_name,
                    "description": scenario_config["description"],
                    "table_placeholders": table_placeholders,
                    "gsi_placeholders": gsi_placeholders,
                    "expected_matches": _replace_strings(
                        _json_safe(_render_templates(scenario_config.get("expected_matches", []), substitutions)),
                        reverse_map,
                    ),
                    "check_parameters": _replace_strings(
                        _json_safe(_render_templates(scenario_config.get("check_parameters", {}), substitutions)),
                        reverse_map,
                    ),
                    "calls": calls,
                }
                _write_json(scenario_dir / "capture_metadata.json", capture_metadata)

        if capture_actions and selected_action_keys:
            _capture_action_payloads(
                session,
                region,
                config,
                state,
                state_path,
                actions,
                reverse_map,
                selected_action_keys,
            )
    except Exception as exc:
        generation_error = exc
    finally:
        cleanup_summary = _finalize_cleanup(config_path, state, state_path)
        print(json.dumps(cleanup_summary, indent=2, sort_keys=True))

    if generation_error is not None:
        raise generation_error
    if cleanup_summary["errors"]:
        raise RuntimeError("DynamoDB payload generation completed but cleanup failed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture DynamoDB payloads for offline tests.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to resource_config.json")
    parser.add_argument("--checks", type=Path, default=CHECKS_PATH, help="Path to checks.json")
    parser.add_argument("--actions", type=Path, default=ACTIONS_PATH, help="Path to actions.json")
    parser.add_argument(
        "--payload-check-map",
        type=Path,
        default=PAYLOAD_CHECK_MAP_PATH,
        help="Path to payload_check_map.json",
    )
    parser.add_argument("--state", type=Path, default=None, help="Path to the capture state emitted by dynamodb_resource_creation.py")
    phase_group = parser.add_mutually_exclusive_group()
    phase_group.add_argument("--checks-only", action="store_true", help="Capture check payloads without executing actions")
    phase_group.add_argument("--actions-only", action="store_true", help="Capture action payloads without regenerating checks")
    parser.add_argument(
        "--all-checks",
        action="store_true",
        help="Regenerate every mapped DynamoDB check scenario instead of only missing or incomplete scenarios",
    )
    parser.add_argument(
        "--all-actions",
        action="store_true",
        help="Regenerate every DynamoDB action fixture instead of only missing or incomplete actions",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Regenerate all mapped DynamoDB check scenarios and all action fixtures",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    require_capture_gate(args.apply)

    state_path = args.state or Path(_load_json(args.config)["state_file"])
    state_path = state_path if state_path.is_absolute() else REPO_ROOT / state_path
    capture_payloads(
        args.config,
        args.checks,
        state_path,
        actions_path=args.actions,
        payload_check_map_path=args.payload_check_map,
        capture_checks=not args.actions_only,
        capture_actions=not args.checks_only,
        regenerate_all_checks=args.all or args.all_checks,
        regenerate_all_actions=args.all or args.all_actions,
        apply=args.apply,
    )


if __name__ == "__main__":
    main()
