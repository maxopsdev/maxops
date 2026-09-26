"""Capture VPC check and action payloads from isolated real AWS resources."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import boto3

CURRENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests_generator.action_capture import action_payload_complete, execute_recorded_action, write_action_fixture
from tests_generator.capture_gate import require_capture_gate
from tests_generator.payload_selection import select_check_scenarios
from tests_generator.vpc.vpc_resource_creation import CONFIG_PATH, VPCPayloadResourceManager, _load_json, _write_json

CHECKS_PATH = CURRENT_DIR / "checks.json"
ACTIONS_PATH = CURRENT_DIR / "actions.json"
PAYLOAD_CHECK_MAP_PATH = REPO_ROOT / "tests_generator" / "payload_check_map.json"
PAYLOAD_ROOT = REPO_ROOT / "tests" / "payloads" / "vpc"


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
        select_check_scenarios("vpc", checks, _load_json(payload_check_map_path), PAYLOAD_ROOT, regenerate_all_checks)
        if capture_checks else {}
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
        ec2 = session.client("ec2", region_name=region)
        substitutions = dict(config["placeholder_values"])
        reverse_map = {actual: placeholder for placeholder, actual in state.get("placeholder_map", {}).items()}
        for resource in state["resources"].values():
            substitutions[resource["placeholder_key"]] = resource["resource_id"]
        for placeholder, actual in state.get("placeholder_map", {}).items():
            for key, configured in config["placeholder_values"].items():
                if configured == placeholder:
                    substitutions[key] = actual

        if selected_checks:
            all_vpcs = ec2.describe_vpcs()
            all_endpoints = ec2.describe_vpc_endpoints()
            all_flow_logs = ec2.describe_flow_logs()
            for check_id, check_config in selected_checks.items():
                for scenario_name, scenario in check_config["scenarios"].items():
                    refs = scenario["resource_refs"]
                    vpc_ids = {state["resources"][alias]["resource_id"] for alias in refs}
                    scenario_dir = PAYLOAD_ROOT / check_id / scenario_name
                    vpcs = dict(all_vpcs)
                    vpcs["Vpcs"] = [item for item in all_vpcs.get("Vpcs", []) if item.get("VpcId") in vpc_ids]
                    endpoints = dict(all_endpoints)
                    endpoints["VpcEndpoints"] = [item for item in all_endpoints.get("VpcEndpoints", []) if item.get("VpcId") in vpc_ids]
                    flow_logs = dict(all_flow_logs)
                    flow_logs["FlowLogs"] = [item for item in all_flow_logs.get("FlowLogs", []) if item.get("ResourceId") in vpc_ids]
                    calls = []
                    for filename, service, operation, payload in (
                        ("describe_vpcs.json", "ec2", "describe_vpcs", vpcs),
                        ("describe_vpc_endpoints.json", "ec2", "describe_vpc_endpoints", endpoints),
                        ("describe_flow_logs.json", "ec2", "describe_flow_logs", flow_logs),
                    ):
                        if filename in scenario["payload_files"]:
                            _write_json(scenario_dir / filename, _replace(_json_safe(payload), reverse_map))
                            calls.append({"service": service, "operation": operation, "response_file": filename})
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
                endpoint_id = response.get("details", {}).get("vpc_endpoint_id")
                if endpoint_id:
                    state.setdefault("vpc_endpoints", []).append(endpoint_id)
                    reverse_map[endpoint_id] = f"vpce-PLACEHOLDER-{len(state['vpc_endpoints'])}"
                    _write_json(state_path, state)
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
        cleanup = VPCPayloadResourceManager(config_path, apply=True).cleanup_resources(state)
        print(json.dumps(cleanup, indent=2, sort_keys=True))
    if error:
        raise error
    if cleanup["errors"]:
        raise RuntimeError("VPC payload generation completed but cleanup failed.")


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
        args.config, args.checks, state_path, args.payload_check_map,
        args.all or args.all_checks, not args.actions_only, args.actions,
        not args.checks_only, args.all or args.all_actions,
        args.apply,
    )


if __name__ == "__main__":
    main()
