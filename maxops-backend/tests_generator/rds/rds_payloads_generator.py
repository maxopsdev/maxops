"""Capture RDS baseline payloads and emit scenario fixtures for offline tests."""

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

from tests_generator.rds.rds_resource_creation import CONFIG_PATH, RDSPayloadResourceManager, _load_json
from tests_generator.payload_selection import select_check_scenarios
from tests_generator.action_capture import (
    action_payload_complete,
    execute_recorded_action,
    write_action_fixture,
)
from tests_generator.capture_gate import require_capture_gate


CHECKS_PATH = Path(__file__).with_name("checks.json")
ACTIONS_PATH = Path(__file__).with_name("actions.json")
PAYLOAD_CHECK_MAP_PATH = REPO_ROOT / "tests_generator" / "payload_check_map.json"
PAYLOAD_ROOT = REPO_ROOT / "tests" / "payloads" / "rds"
METRICS = ("CPUUtilization", "DatabaseConnections", "ReadIOPS", "WriteIOPS")


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


def _deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = _deep_merge(merged.get(key), value)
        return merged
    return deepcopy(override)


def _validate_checks_contract(checks: Dict[str, Any]) -> None:
    """RDS payloads may shape metric payloads only; structural overrides are forbidden."""
    forbidden_keys = {"inventory_overrides", "response_overrides", "describe_overrides", "tag_overrides"}
    for check_id, check_config in checks.items():
        for scenario_name, scenario_config in check_config.get("scenarios", {}).items():
            overlap = forbidden_keys.intersection(scenario_config.keys())
            if overlap:
                keys = ", ".join(sorted(overlap))
                raise ValueError(
                    f"RDS payload scenario {check_id}/{scenario_name} uses forbidden structural override keys: {keys}"
                )


def _metric_filename(alias: str, metric_name: str) -> str:
    return f"get_metric_statistics__{alias}__{metric_name.lower()}.json"


def _capture_baselines(session, region: str, state: Dict[str, Any], start_time: datetime, end_time: datetime) -> Dict[str, Dict[str, Any]]:
    rds_client = session.client("rds", region_name=region)
    cloudwatch_client = session.client("cloudwatch", region_name=region)
    describe_response = rds_client.describe_db_instances()

    baselines: Dict[str, Dict[str, Any]] = {
        "describe_db_instances": describe_response,
    }
    for alias, resource in state.get("resources", {}).items():
        arn = resource["db_instance_arn"]
        identifier = resource["db_instance_identifier"]
        baselines[f"list_tags_for_resource__{alias}.json"] = rds_client.list_tags_for_resource(ResourceName=arn)
        for metric_name in METRICS:
            baselines[_metric_filename(alias, metric_name)] = cloudwatch_client.get_metric_statistics(
                Namespace="AWS/RDS",
                MetricName=metric_name,
                Dimensions=[{"Name": "DBInstanceIdentifier", "Value": identifier}],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,
                Statistics=["Average"],
            )
    return baselines


def _apply_metric_override(baseline: Dict[str, Any], override: Dict[str, Any], timestamp: datetime) -> Dict[str, Any]:
    payload = deepcopy(baseline)
    if override.get("empty"):
        payload["Datapoints"] = []
        return payload
    average = override.get("Average")
    if average is None:
        return payload
    payload["Datapoints"] = [
        {
            "Average": float(average),
            "Timestamp": timestamp,
            "Unit": override.get("Unit", "Count"),
        }
    ]
    return payload


def _finalize_cleanup(config_path: Path, state: Dict[str, Any], state_path: Path) -> Dict[str, Any]:
    manager = RDSPayloadResourceManager(config_path=config_path, apply=True)
    summary = manager.cleanup_resources(state=state)
    summary["state_file"] = str(state_path)
    return summary


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
    _validate_checks_contract(checks)
    selected_checks = (
        select_check_scenarios(
            "rds", checks, _load_json(payload_check_map_path), PAYLOAD_ROOT, regenerate_all_checks
        )
        if capture_checks
        else {}
    )
    actions = _load_json(actions_path) if capture_actions else {}
    selected_actions = {
        key for key, value in actions.items()
        if regenerate_all_actions or not action_payload_complete(PAYLOAD_ROOT, key, value)
    }
    state = _load_json(state_path)
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
        for alias, resource in state.get("resources", {}).items():
            substitutions[resource["placeholder_key"]] = resource["db_instance_identifier"]

        reverse_map = {actual: placeholder for placeholder, actual in state.get("placeholder_map", {}).items()}
        if state.get("account_id") and config["placeholder_values"].get("ACCOUNT_ID"):
            reverse_map[state["account_id"]] = config["placeholder_values"]["ACCOUNT_ID"]
        baselines = _capture_baselines(session, region, state, start_time, end_time) if selected_checks else {}

        for check_id, check_config in selected_checks.items():
            for scenario_name, scenario_config in check_config.get("scenarios", {}).items():
                scenario_dir = PAYLOAD_ROOT / check_id / scenario_name
                capture_instances = scenario_config.get("capture_instances", [])
                selected_identifiers = {state["resources"][alias]["db_instance_identifier"] for alias in capture_instances}
                selected_placeholders = {
                    alias: config["placeholder_values"][state["resources"][alias]["placeholder_key"]]
                    for alias in capture_instances
                }

                describe_payload = deepcopy(baselines["describe_db_instances"])
                describe_payload["DBInstances"] = [
                    instance for instance in describe_payload.get("DBInstances", [])
                    if instance.get("DBInstanceIdentifier") in selected_identifiers
                ]
                _write_json(scenario_dir / "describe_db_instances.json", _sanitize_response(describe_payload, reverse_map))

                calls: List[Dict[str, Any]] = [
                    {
                        "service": "rds",
                        "operation": "describe_db_instances",
                        "response_file": "describe_db_instances.json",
                    }
                ]

                metric_overrides = scenario_config.get("metric_overrides", {})
                for alias in capture_instances:
                    tags_filename = f"list_tags_for_resource__{alias}.json"
                    if tags_filename in scenario_config.get("payload_files", []):
                        tags_payload = deepcopy(baselines[tags_filename])
                        _write_json(scenario_dir / tags_filename, _sanitize_response(tags_payload, reverse_map))
                        calls.append(
                            {
                                "service": "rds",
                                "operation": "list_tags_for_resource",
                                "db_alias": alias,
                                "db_instance_identifier": selected_placeholders[alias],
                                "response_file": tags_filename,
                            }
                        )

                    for metric_name in METRICS:
                        filename = _metric_filename(alias, metric_name)
                        if filename not in scenario_config.get("payload_files", []):
                            continue
                        payload = _apply_metric_override(
                            baselines[filename],
                            metric_overrides.get(alias, {}).get(metric_name, {}),
                            end_time,
                        )
                        _write_json(scenario_dir / filename, _sanitize_response(payload, reverse_map))
                        calls.append(
                            {
                                "service": "cloudwatch",
                                "operation": "get_metric_statistics",
                                "db_alias": alias,
                                "db_instance_identifier": selected_placeholders[alias],
                                "metric_name": metric_name,
                                "response_file": filename,
                            }
                        )

                capture_metadata = {
                    "check_id": check_id,
                    "scenario": scenario_name,
                    "description": scenario_config["description"],
                    "db_placeholders": selected_placeholders,
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

        if selected_actions:
            from app.checks.registry import check_registry

            for action_key, action_config in actions.items():
                resource = state["resources"][action_config["resource_ref"]]
                resource_id = resource["db_instance_identifier"]
                response, calls = execute_recorded_action(
                    session,
                    action_key,
                    action_config,
                    state["account_id"],
                    region,
                    resource_id,
                    check_registry.get_check(action_config["check_id"]),
                )
                if action_key in selected_actions:
                    write_action_fixture(
                        PAYLOAD_ROOT,
                        action_key,
                        action_config,
                        response,
                        calls,
                        {
                            "check_id": action_config["check_id"],
                            "resource_ref": action_config["resource_ref"],
                            "resource_id": config["placeholder_values"][resource["placeholder_key"]],
                            "account_id": config["placeholder_values"]["ACCOUNT_ID"],
                            "region": region,
                            "parameters": action_config["parameters"],
                        },
                        lambda value: _sanitize_response(value, reverse_map),
                        _write_json,
                    )
    except Exception as exc:
        generation_error = exc
    finally:
        cleanup_summary = _finalize_cleanup(config_path, state, state_path)
        print(json.dumps(cleanup_summary, indent=2, sort_keys=True))

    if generation_error is not None:
        raise generation_error
    if cleanup_summary["errors"]:
        raise RuntimeError("RDS payload generation completed but cleanup failed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture RDS payloads for offline tests.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to resource_config.json")
    parser.add_argument("--checks", type=Path, default=CHECKS_PATH, help="Path to checks.json")
    parser.add_argument("--payload-check-map", type=Path, default=PAYLOAD_CHECK_MAP_PATH)
    parser.add_argument("--all-checks", action="store_true", help="Regenerate all mapped RDS check scenarios")
    parser.add_argument("--actions", type=Path, default=ACTIONS_PATH)
    parser.add_argument("--checks-only", action="store_true")
    parser.add_argument("--actions-only", action="store_true")
    parser.add_argument("--all-actions", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--state", type=Path, default=None, help="Path to the capture state emitted by rds_resource_creation.py")
    args = parser.parse_args()
    require_capture_gate(args.apply)

    state_path = args.state or Path(_load_json(args.config)["state_file"])
    state_path = state_path if state_path.is_absolute() else REPO_ROOT / state_path
    capture_payloads(
        args.config, args.checks, state_path,
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
