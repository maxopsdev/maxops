"""Capture one EC2 baseline and synthesize minimal GetMetricData scenario payloads from it."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import boto3

CURRENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests_generator.ec2.ec2_resource_creation import CONFIG_PATH, EC2PayloadResourceManager, _load_json
from tests_generator.payload_selection import select_check_scenarios
from tests_generator.action_capture import action_payload_complete, execute_recorded_action, write_action_fixture
from tests_generator.capture_gate import require_capture_gate


CHECKS_PATH = Path(__file__).with_name("checks.json")
ACTIONS_PATH = Path(__file__).with_name("actions.json")
PAYLOAD_CHECK_MAP_PATH = REPO_ROOT / "tests_generator" / "payload_check_map.json"
PAYLOAD_ROOT = Path(__file__).resolve().parents[2] / "tests" / "payloads" / "ec2"
IDE_SAMPLE_ARGS = [
    "--config",
    str(CONFIG_PATH),
    "--checks",
    str(CHECKS_PATH),
]

EC2_METRIC_FILE = "get_metric_data_ec2.json"
MEMORY_METRIC_FILE = "get_metric_data_memory.json"
EC2_NAMESPACE = "AWS/EC2"
MEMORY_NAMESPACE = "CWAgent"
STAT_ORDER = ["Average", "Maximum", "p90", "p95", "p99"]
QUERY_PREFIX_MAP = {
    "CPUUtilization": "cpu",
    "NetworkIn": "network_in",
    "NetworkOut": "network_out",
    "mem_used_percent": "memory",
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
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value


def _replace_strings(value: Any, reverse_map: Dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _replace_strings(item, reverse_map) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_strings(item, reverse_map) for item in value]
    if isinstance(value, str):
        updated = value
        for actual, placeholder in sorted(reverse_map.items(), key=lambda item: len(item[0]), reverse=True):
            updated = updated.replace(actual, placeholder)
        return updated
    return value


def _render_templates(value: Any, substitutions: Dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _render_templates(item, substitutions) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_templates(item, substitutions) for item in value]
    if isinstance(value, str):
        if value.startswith("{{") and value.endswith("}}"):
            token = value[2:-2]
            if token in substitutions:
                return substitutions[token]
        rendered = value
        for token, replacement in substitutions.items():
            if isinstance(replacement, datetime):
                continue
            rendered = rendered.replace(f"{{{{{token}}}}}", str(replacement))
        return rendered
    return value


def _sanitize_response(response: Dict[str, Any], reverse_map: Dict[str, str]) -> Dict[str, Any]:
    return _replace_strings(_json_safe(response), reverse_map)


def _build_runtime_tokens(config: Dict[str, Any]) -> Dict[str, Any]:
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=config.get("capture_window_hours", 24))
    return {"START_TIME": start_time, "END_TIME": end_time}


def _ensure_instance_state(ec2_client, instance_id: str, desired_state: str) -> None:
    response = ec2_client.describe_instances(InstanceIds=[instance_id])
    reservations = response.get("Reservations", [])
    current_state = reservations[0]["Instances"][0]["State"]["Name"] if reservations else None
    if current_state == desired_state:
        return 
    if desired_state == "running":
        ec2_client.start_instances(InstanceIds=[instance_id])
        ec2_client.get_waiter("instance_running").wait(InstanceIds=[instance_id])
        return
    if desired_state == "stopped":
        ec2_client.stop_instances(InstanceIds=[instance_id])
        ec2_client.get_waiter("instance_stopped").wait(InstanceIds=[instance_id])
        return
    raise ValueError(f"Unsupported desired state: {desired_state}")


def _query_id(metric_name: str, stat_name: str) -> str:
    return f"{QUERY_PREFIX_MAP[metric_name]}_{stat_name.lower()}"


def _build_metric_data_query(metric_name: str, stat_name: str, instance_id: str, namespace: str) -> Dict[str, Any]:
    return {
        "Id": _query_id(metric_name, stat_name),
        "MetricStat": {
            "Metric": {
                "Namespace": namespace,
                "MetricName": metric_name,
                "Dimensions": [{"Name": "InstanceId", "Value": instance_id}],
            },
            "Period": 2592000,
            "Stat": stat_name,
        },
        "ReturnData": True,
    }


def _build_metric_data_queries(instance_id: str, namespace: str, metric_names: List[str]) -> List[Dict[str, Any]]:
    queries: List[Dict[str, Any]] = []
    for metric_name in metric_names:
        for stat_name in STAT_ORDER:
            queries.append(_build_metric_data_query(metric_name, stat_name, instance_id, namespace))
    return queries


def _capture_baselines(session, region: str, instance_id: str, substitutions: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    ec2_client = session.client("ec2", region_name=region)
    cloudwatch_client = session.client("cloudwatch", region_name=region)

    _ensure_instance_state(ec2_client, instance_id, "running")
    running_describe = ec2_client.describe_instances(InstanceIds=[instance_id])

    ec2_metric_data = cloudwatch_client.get_metric_data(
        MetricDataQueries=_build_metric_data_queries(
            instance_id,
            EC2_NAMESPACE,
            ["CPUUtilization", "NetworkIn", "NetworkOut"],
        ),
        StartTime=substitutions["START_TIME"],
        EndTime=substitutions["END_TIME"],
        ScanBy="TimestampDescending",
    )
    memory_metric_data = cloudwatch_client.get_metric_data(
        MetricDataQueries=_build_metric_data_queries(
            instance_id,
            MEMORY_NAMESPACE,
            ["mem_used_percent"],
        ),
        StartTime=substitutions["START_TIME"],
        EndTime=substitutions["END_TIME"],
        ScanBy="TimestampDescending",
    )

    _ensure_instance_state(ec2_client, instance_id, "stopped")
    stopped_describe = ec2_client.describe_instances(InstanceIds=[instance_id])

    return {
        "running_describe": running_describe,
        "stopped_describe": stopped_describe,
        EC2_METRIC_FILE: ec2_metric_data,
        MEMORY_METRIC_FILE: memory_metric_data,
    }


def _single_timestamp(end_time: datetime) -> datetime:
    return end_time.astimezone(timezone.utc).replace(hour=13, minute=55, second=0, microsecond=0)


def _build_metric_result(metric_name: str, stat_name: str, timestamp: datetime, value: float) -> Dict[str, Any]:
    return {
        "Id": _query_id(metric_name, stat_name),
        "Label": f"{metric_name} {stat_name}",
        "StatusCode": "Complete",
        "Timestamps": [timestamp],
        "Values": [float(value)],
        "Messages": [],
    }


def _apply_metric_data_override(
    baseline: Dict[str, Any],
    overrides: Dict[str, Any],
    end_time: datetime,
) -> Dict[str, Any]:
    updated = deepcopy(baseline)
    results = updated.setdefault("MetricDataResults", [])
    results_by_id = {item.get("Id"): item for item in results}
    default_timestamp = _single_timestamp(end_time)

    for metric_name, metric_override in overrides.items():
        is_empty = metric_override.get("empty", False)
        for stat_name in STAT_ORDER:
            query_id = _query_id(metric_name, stat_name)
            payload = results_by_id.get(query_id)
            if payload is None:
                payload = _build_metric_result(
                    metric_name,
                    stat_name,
                    default_timestamp,
                    metric_override.get(stat_name, 0.0),
                )
                results.append(payload)
                results_by_id[query_id] = payload

            payload.setdefault("Messages", [])
            payload["StatusCode"] = "Complete"
            payload.setdefault("Label", f"{metric_name} {stat_name}")

            if is_empty:
                payload["Values"] = []
                payload["Timestamps"] = []
                continue

            existing_timestamps = payload.get("Timestamps") or []
            first_timestamp = existing_timestamps[0] if existing_timestamps else default_timestamp
            first_value = metric_override.get(stat_name)
            if first_value is None:
                existing_values = payload.get("Values") or []
                first_value = existing_values[0] if existing_values else 0.0
            payload["Values"] = [float(first_value)]
            payload["Timestamps"] = [first_timestamp]

    return updated


def _build_metric_queries_for_metadata(instance_id: str, namespace: str, metric_names: List[str]) -> List[Dict[str, Any]]:
    return _json_safe(_build_metric_data_queries(instance_id, namespace, metric_names))


def _apply_instance_overrides(
    describe_payload: Dict[str, Any],
    overrides: Dict[str, Any],
) -> Dict[str, Any]:
    updated = deepcopy(describe_payload)
    for reservation in updated.get("Reservations", []):
        for instance in reservation.get("Instances", []):
            instance.update(deepcopy(overrides))
    return updated


def _finalize_cleanup(
    config_path: Path,
    state: Dict[str, Any],
    state_path: Path,
) -> Dict[str, Any]:
    manager = EC2PayloadResourceManager(config_path=config_path, apply=True)
    summary = manager.cleanup_resources(state=state)
    summary["state_file"] = str(state_path)
    return summary


def _prepare_action_resource_state(ec2_client, action_key: str, instance_id: str) -> None:
    desired_state_by_action = {
        "stop": "running",
        "migrate_to_graviton": "running",
        "terminate_with_snapshot": "running",
        "terminate_leave_volume": "running",
        "terminate": "stopped",
    }
    desired_state = desired_state_by_action.get(action_key)
    if desired_state:
        _ensure_instance_state(ec2_client, instance_id, desired_state)


def capture_payloads(
    config_path: Path,
    checks_path: Path,
    state_path: Path,
    payload_check_map_path: Path = PAYLOAD_CHECK_MAP_PATH,
    regenerate_all_checks: bool = False,
    capture_checks: bool = True,
    actions_path: Path = ACTIONS_PATH,
    capture_actions: bool = True,
    regenerate_all_actions: bool = False,
    apply: bool = False,
) -> None:
    require_capture_gate(apply)
    config = _load_json(config_path)
    checks = _load_json(checks_path)
    selected_checks = (
        select_check_scenarios(
            "ec2",
            checks,
            _load_json(payload_check_map_path),
            PAYLOAD_ROOT,
            regenerate_all=regenerate_all_checks,
        )
        if capture_checks
        else {}
    )
    actions = _load_json(actions_path) if capture_actions else {}
    selected_actions = {
        key for key, value in actions.items()
        if value.get("capture_enabled", True)
        and (regenerate_all_actions or not action_payload_complete(PAYLOAD_ROOT, key, value))
    }
    state = _load_json(state_path)
    generation_error: Exception | None = None
    cleanup_summary: Dict[str, Any] | None = None

    try:
        runtime_tokens = _build_runtime_tokens(config)
        profile = config.get("profile")
        region = state.get("region") or config["region"]
        session = boto3.Session(profile_name=profile, region_name=region) if profile else boto3.Session(region_name=region)

        substitutions: Dict[str, Any] = {}
        substitutions.update(config.get("placeholder_values", {}))
        substitutions.update(runtime_tokens)
        for placeholder, actual in state.get("placeholder_map", {}).items():
            for key, configured_placeholder in config.get("placeholder_values", {}).items():
                if configured_placeholder == placeholder:
                    substitutions[key] = actual
                    break

        reverse_map = {actual: placeholder for placeholder, actual in state.get("placeholder_map", {}).items()}
        instance_id = substitutions["INSTANCE_ID"]
        baselines = _capture_baselines(session, region, instance_id, substitutions) if selected_checks else {}

        for check_id, check_config in selected_checks.items():
            for scenario_name, scenario_config in check_config.get("scenarios", {}).items():
                scenario_dir = PAYLOAD_ROOT / check_id / scenario_name
                base_capture = scenario_config["base_capture"]

                if base_capture == "running":
                    describe_payload = deepcopy(baselines["running_describe"])
                elif base_capture == "stopped":
                    describe_payload = deepcopy(baselines["stopped_describe"])
                elif base_capture == "empty_stopped":
                    describe_payload = {"Reservations": []}
                else:
                    raise ValueError(f"Unsupported base_capture: {base_capture}")

                describe_payload = _apply_instance_overrides(
                    describe_payload,
                    scenario_config.get("instance_overrides", {}),
                )
                _write_json(scenario_dir / "describe_instances.json", _sanitize_response(describe_payload, reverse_map))

                capture_metadata = {
                    "check_id": check_id,
                    "scenario": scenario_name,
                    "description": scenario_config["description"],
                    "base_capture": base_capture,
                    "instance_overrides": scenario_config.get("instance_overrides", {}),
                    "expected_matches": _replace_strings(
                        _json_safe(_render_templates(scenario_config.get("expected_matches", []), substitutions)),
                        reverse_map,
                    ),
                    "check_parameters": _replace_strings(
                        _json_safe(_render_templates(scenario_config.get("check_parameters", {}), substitutions)),
                        reverse_map,
                    ),
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "calls": [
                        {
                            "service": "ec2",
                            "operation": "describe_instances",
                            "response_file": "describe_instances.json",
                            "kwargs": {
                                "Filters": [
                                    {
                                        "Name": "instance-state-name",
                                        "Values": [base_capture if base_capture != "empty_stopped" else "stopped"],
                                    }
                                ]
                            },
                        }
                    ],
                }

                metric_overrides = scenario_config.get("metric_overrides", {})
                if EC2_METRIC_FILE in scenario_config.get("payload_files", []):
                    ec2_payload = _apply_metric_data_override(
                        deepcopy(baselines[EC2_METRIC_FILE]),
                        metric_overrides.get("ec2", {}),
                        substitutions["END_TIME"],
                    )
                    _write_json(scenario_dir / EC2_METRIC_FILE, _sanitize_response(ec2_payload, reverse_map))
                    capture_metadata["calls"].append(
                        {
                            "service": "cloudwatch",
                            "operation": "get_metric_data",
                            "response_file": EC2_METRIC_FILE,
                            "kwargs": {
                                "MetricDataQueries": _build_metric_queries_for_metadata(
                                    reverse_map.get(instance_id, instance_id),
                                    EC2_NAMESPACE,
                                    ["CPUUtilization", "NetworkIn", "NetworkOut"],
                                ),
                                "StartTime": _json_safe(substitutions["START_TIME"]),
                                "EndTime": _json_safe(substitutions["END_TIME"]),
                                "ScanBy": "TimestampDescending",
                            },
                            "synthetic_override": metric_overrides.get("ec2", {}),
                        }
                    )

                if MEMORY_METRIC_FILE in scenario_config.get("payload_files", []):
                    memory_payload = _apply_metric_data_override(
                        deepcopy(baselines[MEMORY_METRIC_FILE]),
                        metric_overrides.get("memory", {}),
                        substitutions["END_TIME"],
                    )
                    _write_json(scenario_dir / MEMORY_METRIC_FILE, _sanitize_response(memory_payload, reverse_map))
                    capture_metadata["calls"].append(
                        {
                            "service": "cloudwatch",
                            "operation": "get_metric_data",
                            "response_file": MEMORY_METRIC_FILE,
                            "kwargs": {
                                "MetricDataQueries": _build_metric_queries_for_metadata(
                                    reverse_map.get(instance_id, instance_id),
                                    MEMORY_NAMESPACE,
                                    ["mem_used_percent"],
                                ),
                                "StartTime": _json_safe(substitutions["START_TIME"]),
                                "EndTime": _json_safe(substitutions["END_TIME"]),
                                "ScanBy": "TimestampDescending",
                            },
                            "synthetic_override": metric_overrides.get("memory", {}),
                        }
                    )

                _write_json(scenario_dir / "capture_metadata.json", capture_metadata)

        if selected_actions:
            from app.checks.registry import check_registry

            ec2 = session.client("ec2", region_name=region)
            for action_key, action_config in actions.items():
                if action_key not in selected_actions:
                    continue
                resource = state["resources"][action_config["resource_ref"]]
                resource_id = resource.get("instance_id") or resource.get("allocation_id")
                if resource.get("instance_id"):
                    _prepare_action_resource_state(ec2, action_key, resource_id)
                response, calls = execute_recorded_action(
                    session,
                    action_key,
                    action_config,
                    config["placeholder_values"]["ACCOUNT_ID"],
                    region,
                    resource_id,
                    check_registry.get_check(action_config["check_id"]),
                )
                if action_key == "terminate_with_snapshot":
                    for snapshot_id in response.get("details", {}).get("snapshots", []):
                        if snapshot_id:
                            state.setdefault("snapshots", []).append(
                                {
                                    "resource_alias": action_config["resource_ref"],
                                    "snapshot_id": snapshot_id,
                                }
                            )
                            reverse_map[snapshot_id] = config["placeholder_values"][
                                "TERMINATION_SNAPSHOT_ID"
                            ]
                    _write_json(state_path, state)
                resource_placeholder = (
                    config["placeholder_values"][resource["instance_id_placeholder_key"]]
                    if resource.get("instance_id")
                    else config["placeholder_values"]["ELASTIC_IP_ALLOCATION_ID"]
                )
                write_action_fixture(
                    PAYLOAD_ROOT,
                    action_key,
                    action_config,
                    response,
                    calls,
                    {
                        "check_id": action_config["check_id"],
                        "resource_ref": action_config["resource_ref"],
                        "resource_id": resource_placeholder,
                        "account_id": config["placeholder_values"]["ACCOUNT_ID"],
                        "region": region,
                        "parameters": action_config["parameters"],
                    },
                    lambda value: _sanitize_response(value, reverse_map),
                    _write_json,
                )
                if action_key == "stop":
                    ec2.get_waiter("instance_stopped").wait(InstanceIds=[resource_id])
    except Exception as exc:
        generation_error = exc
    finally:
        cleanup_summary = _finalize_cleanup(config_path, state, state_path)
        print(json.dumps(cleanup_summary, indent=2, sort_keys=True))

    if generation_error is not None:
        raise generation_error
    if cleanup_summary["errors"]:
        raise RuntimeError("EC2 payload generation completed but cleanup failed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture one EC2 baseline and synthesize GetMetricData scenario payloads.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to resource_config.json")
    parser.add_argument("--checks", type=Path, default=CHECKS_PATH, help="Path to checks.json")
    parser.add_argument("--payload-check-map", type=Path, default=PAYLOAD_CHECK_MAP_PATH)
    parser.add_argument("--all-checks", action="store_true", help="Regenerate all mapped EC2 check scenarios")
    parser.add_argument("--actions", type=Path, default=ACTIONS_PATH)
    parser.add_argument("--checks-only", action="store_true")
    parser.add_argument("--actions-only", action="store_true")
    parser.add_argument("--all-actions", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--state", type=Path, default=None, help="Path to the capture state emitted by ec2_resource_creation.py")
    args = parser.parse_args()
    require_capture_gate(args.apply)

    state_path = args.state or Path(_load_json(args.config)["state_file"])
    state_path = state_path if state_path.is_absolute() else REPO_ROOT / state_path
    capture_payloads(
        args.config,
        args.checks,
        state_path,
        payload_check_map_path=args.payload_check_map,
        regenerate_all_checks=args.all or args.all_checks,
        capture_checks=not args.actions_only,
        actions_path=args.actions,
        capture_actions=not args.checks_only,
        regenerate_all_actions=args.all or args.all_actions,
        apply=args.apply,
    )


if __name__ == "__main__":
    main()
