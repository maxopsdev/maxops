"""Capture CloudWatch check and action payloads from real AWS resources."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

import boto3

CURRENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests_generator.action_capture import action_payload_complete, execute_recorded_action, write_action_fixture
from tests_generator.capture_gate import require_capture_gate
from tests_generator.cloudwatch.cloudwatch_resource_creation import CONFIG_PATH, CloudWatchPayloadResourceManager, _load_json
from tests_generator.payload_selection import select_check_scenarios

CHECKS_PATH = CURRENT_DIR / "checks.json"
ACTIONS_PATH = CURRENT_DIR / "actions.json"
PAYLOAD_CHECK_MAP_PATH = REPO_ROOT / "tests_generator" / "payload_check_map.json"
PAYLOAD_ROOT = REPO_ROOT / "tests" / "payloads" / "cloudwatch"


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value


def _replace(value: Any, replacements: Dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _replace(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace(item, replacements) for item in value]
    if isinstance(value, str):
        for actual, placeholder in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
            value = value.replace(actual, placeholder)
    return value


def _render(value: Any, substitutions: Dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _render(item, substitutions) for key, item in value.items()}
    if isinstance(value, list):
        return [_render(item, substitutions) for item in value]
    if isinstance(value, str):
        for token, replacement in substitutions.items():
            value = value.replace(f"{{{{{token}}}}}", replacement)
    return value


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
    actions = _load_json(actions_path) if capture_actions else {}
    selected_checks = (
        select_check_scenarios(
            "cloudwatch", checks, _load_json(payload_check_map_path), PAYLOAD_ROOT, regenerate_all_checks
        )
        if capture_checks
        else {}
    )
    selected_actions = {
        key for key, value in actions.items()
        if regenerate_all_actions or not action_payload_complete(PAYLOAD_ROOT, key, value)
    }
    state = _load_json(state_path)
    error = None
    cleanup = None
    try:
        profile = config.get("profile")
        region = state.get("region") or config["region"]
        session = boto3.Session(profile_name=profile, region_name=region) if profile else boto3.Session(region_name=region)
        cloudwatch = session.client("cloudwatch", region_name=region)
        logs = session.client("logs", region_name=region)
        substitutions = dict(config["placeholder_values"])
        reverse_map = {actual: placeholder for placeholder, actual in state.get("placeholder_map", {}).items()}
        for resource in state["resources"].values():
            substitutions[resource["placeholder_key"]] = resource["resource_id"]

        if selected_checks:
            alarm_baseline = cloudwatch.describe_alarms()
            log_baseline = logs.describe_log_groups()
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=7)
            histories = {
                alias: cloudwatch.describe_alarm_history(
                    AlarmName=resource["resource_id"],
                    StartDate=start,
                    EndDate=end,
                    MaxRecords=100,
                )
                for alias, resource in state["resources"].items()
                if resource["kind"] == "alarm"
            }
            for check_id, check_config in selected_checks.items():
                for scenario_name, scenario in check_config["scenarios"].items():
                    refs = scenario["resource_refs"]
                    names = {state["resources"][alias]["resource_id"] for alias in refs}
                    scenario_dir = PAYLOAD_ROOT / check_id / scenario_name
                    calls = []
                    if "describe_log_groups.json" in scenario["payload_files"]:
                        payload = deepcopy(log_baseline)
                        payload["logGroups"] = [
                            item for item in payload.get("logGroups", [])
                            if item.get("logGroupName") in names
                        ]
                        _write_json(scenario_dir / "describe_log_groups.json", _replace(_json_safe(payload), reverse_map))
                        calls.append({"service": "logs", "operation": "describe_log_groups", "response_file": "describe_log_groups.json"})
                    if "describe_alarms.json" in scenario["payload_files"]:
                        payload = deepcopy(alarm_baseline)
                        payload["MetricAlarms"] = [
                            item for item in payload.get("MetricAlarms", [])
                            if item.get("AlarmName") in names
                        ]
                        payload["CompositeAlarms"] = [
                            item for item in payload.get("CompositeAlarms", [])
                            if item.get("AlarmName") in names
                        ]
                        _write_json(scenario_dir / "describe_alarms.json", _replace(_json_safe(payload), reverse_map))
                        calls.append({"service": "cloudwatch", "operation": "describe_alarms", "response_file": "describe_alarms.json"})
                    for alias in refs:
                        filename = f"describe_alarm_history__{alias}.json"
                        if filename in scenario["payload_files"]:
                            _write_json(scenario_dir / filename, _replace(_json_safe(histories[alias]), reverse_map))
                            calls.append({"service": "cloudwatch", "operation": "describe_alarm_history", "resource_alias": alias, "response_file": filename})
                    _write_json(
                        scenario_dir / "capture_metadata.json",
                        {
                            "check_id": check_id,
                            "scenario": scenario_name,
                            "description": scenario["description"],
                            "expected_matches": _replace(_render(scenario["expected_matches"], substitutions), reverse_map),
                            "check_parameters": _replace(_render(scenario["check_parameters"], substitutions), reverse_map),
                            "resource_placeholders": {
                                alias: config["placeholder_values"][state["resources"][alias]["placeholder_key"]]
                                for alias in refs
                            },
                            "calls": calls,
                        },
                    )

        if selected_actions:
            from app.checks.registry import check_registry

            for action_key, action_config in actions.items():
                resource = state["resources"][action_config["resource_ref"]]
                runtime_config = _render(action_config, substitutions)
                response, calls = execute_recorded_action(
                    session,
                    action_key,
                    runtime_config,
                    state["account_id"],
                    region,
                    resource["resource_id"],
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
                            "parameters": _replace(runtime_config["parameters"], reverse_map),
                        },
                        lambda value: _replace(_json_safe(value), reverse_map),
                        _write_json,
                    )
    except Exception as exc:
        error = exc
    finally:
        cleanup = CloudWatchPayloadResourceManager(config_path, apply=True).cleanup_resources(state)
        print(json.dumps(cleanup, indent=2, sort_keys=True))
    if error:
        raise error
    if cleanup["errors"]:
        raise RuntimeError("CloudWatch payload generation completed but cleanup failed.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--checks", type=Path, default=CHECKS_PATH)
    parser.add_argument("--actions", type=Path, default=ACTIONS_PATH)
    parser.add_argument("--payload-check-map", type=Path, default=PAYLOAD_CHECK_MAP_PATH)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--checks-only", action="store_true")
    parser.add_argument("--actions-only", action="store_true")
    parser.add_argument("--all-checks", action="store_true")
    parser.add_argument("--all-actions", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    require_capture_gate(args.apply)
    state_path = args.state or Path(_load_json(args.config)["state_file"])
    state_path = state_path if state_path.is_absolute() else REPO_ROOT / state_path
    capture_payloads(
        args.config,
        args.checks,
        state_path,
        args.payload_check_map,
        args.all or args.all_checks,
        not args.actions_only,
        args.actions,
        not args.checks_only,
        args.all or args.all_actions,
        apply=args.apply,
    )


if __name__ == "__main__":
    main()
