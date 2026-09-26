"""Capture S3 baseline payloads and emit scenario fixtures for offline tests."""

from __future__ import annotations

import argparse
import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List

import boto3
from botocore.exceptions import ClientError

CURRENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests_generator.s3.s3_resource_creation import CONFIG_PATH, S3PayloadResourceManager, _load_json
from tests_generator.payload_selection import select_check_scenarios
from tests_generator.action_capture import action_payload_complete, execute_recorded_action, write_action_fixture
from tests_generator.capture_gate import require_capture_gate


CHECKS_PATH = Path(__file__).with_name("checks.json")
ACTIONS_PATH = Path(__file__).with_name("actions.json")
PAYLOAD_CHECK_MAP_PATH = REPO_ROOT / "tests_generator" / "payload_check_map.json"
PAYLOAD_ROOT = REPO_ROOT / "tests" / "payloads" / "s3"
BUCKET_OPERATIONS = (
    "get_bucket_location",
    "get_bucket_tagging",
    "get_bucket_lifecycle_configuration",
    "get_bucket_versioning",
    "get_bucket_logging",
    "get_bucket_replication",
    "list_bucket_inventory_configurations",
)


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
        return re.sub(r"(maxops-[a-z0-9-]+)-\d{9,}", r"\1-{{ANY_INT}}", rendered)
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


def _capture_bucket_operation(s3_client, operation: str, bucket_name: str) -> Dict[str, Any]:
    try:
        if operation == "get_bucket_location":
            return s3_client.get_bucket_location(Bucket=bucket_name)
        if operation == "get_bucket_tagging":
            return s3_client.get_bucket_tagging(Bucket=bucket_name)
        if operation == "get_bucket_lifecycle_configuration":
            return s3_client.get_bucket_lifecycle_configuration(Bucket=bucket_name)
        if operation == "get_bucket_versioning":
            return s3_client.get_bucket_versioning(Bucket=bucket_name)
        if operation == "get_bucket_logging":
            return s3_client.get_bucket_logging(Bucket=bucket_name)
        if operation == "get_bucket_replication":
            return s3_client.get_bucket_replication(Bucket=bucket_name)
        if operation == "list_bucket_inventory_configurations":
            return s3_client.list_bucket_inventory_configurations(Bucket=bucket_name)
    except ClientError:
        pass

    if operation == "get_bucket_location":
        return {"LocationConstraint": None}
    if operation == "get_bucket_tagging":
        return {"TagSet": []}
    return {}


def _capture_baselines(session, region: str, state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    s3_client = session.client("s3", region_name=region)
    expected_names = {resource["bucket_name"] for resource in state.get("resources", {}).values()}
    list_response = s3_client.list_buckets()
    filtered_buckets = [bucket for bucket in list_response.get("Buckets", []) if bucket.get("Name") in expected_names]

    baselines: Dict[str, Dict[str, Any]] = {
        "list_buckets": {"Buckets": filtered_buckets, "Owner": list_response.get("Owner", {})},
    }
    for alias, resource in state.get("resources", {}).items():
        bucket_name = resource["bucket_name"]
        for operation in BUCKET_OPERATIONS:
            baselines[f"{operation}__{alias}.json"] = _capture_bucket_operation(s3_client, operation, bucket_name)
    return baselines


def _finalize_cleanup(
    config_path: Path,
    state: Dict[str, Any],
    state_path: Path,
) -> Dict[str, Any]:
    manager = S3PayloadResourceManager(config_path=config_path, apply=True)
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
    selected_checks = (
        select_check_scenarios(
            "s3", checks, _load_json(payload_check_map_path), PAYLOAD_ROOT, regenerate_all_checks
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
        profile = config.get("profile")
        region = state.get("region") or config["region"]
        session = boto3.Session(profile_name=profile, region_name=region) if profile else boto3.Session(region_name=region)

        substitutions: Dict[str, Any] = dict(config.get("placeholder_values", {}))
        substitutions["REGION"] = region
        substitutions["ACCOUNT_ID"] = state.get("account_id", substitutions.get("ACCOUNT_ID"))
        for alias, resource in state.get("resources", {}).items():
            placeholder_key = resource["placeholder_key"]
            substitutions[placeholder_key] = resource["bucket_name"]

        reverse_map = {actual: placeholder for placeholder, actual in state.get("placeholder_map", {}).items()}
        if state.get("account_id") and config["placeholder_values"].get("ACCOUNT_ID"):
            reverse_map[state["account_id"]] = config["placeholder_values"]["ACCOUNT_ID"]
        baselines = _capture_baselines(session, region, state) if selected_checks else {}

        for check_id, check_config in selected_checks.items():
            for scenario_name, scenario_config in check_config.get("scenarios", {}).items():
                scenario_dir = PAYLOAD_ROOT / check_id / scenario_name
                capture_buckets = scenario_config.get("capture_buckets", [])
                bucket_placeholders = {
                    alias: config["placeholder_values"][state["resources"][alias]["placeholder_key"]]
                    for alias in capture_buckets
                }

                list_payload = deepcopy(baselines["list_buckets"])
                list_payload["Buckets"] = [
                    bucket for bucket in list_payload.get("Buckets", [])
                    if bucket.get("Name") in {state["resources"][alias]["bucket_name"] for alias in capture_buckets}
                ]
                list_payload = _deep_merge(list_payload, scenario_config.get("response_overrides", {}).get("list_buckets.json", {}))
                _write_json(scenario_dir / "list_buckets.json", _sanitize_response(list_payload, reverse_map))

                calls: List[Dict[str, Any]] = [
                    {
                        "service": "s3",
                        "operation": "list_buckets",
                        "response_file": "list_buckets.json",
                    }
                ]

                for alias in capture_buckets:
                    for operation in BUCKET_OPERATIONS:
                        filename = f"{operation}__{alias}.json"
                        if filename not in scenario_config.get("payload_files", []):
                            continue
                        payload = deepcopy(baselines[filename])
                        payload = _deep_merge(payload, scenario_config.get("response_overrides", {}).get(filename, {}))
                        _write_json(scenario_dir / filename, _sanitize_response(payload, reverse_map))
                        calls.append(
                            {
                                "service": "s3",
                                "operation": operation,
                                "bucket_alias": alias,
                                "bucket_name": bucket_placeholders[alias],
                                "response_file": filename,
                            }
                        )

                capture_metadata = {
                    "check_id": check_id,
                    "scenario": scenario_name,
                    "description": scenario_config["description"],
                    "bucket_placeholders": bucket_placeholders,
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
                response, calls = execute_recorded_action(
                    session,
                    action_key,
                    action_config,
                    state["account_id"],
                    region,
                    resource["bucket_name"],
                    check_registry.get_check(action_config["check_id"]),
                )
                expected_message = action_config.get("expected_message")
                expected_prefix = action_config.get("expected_message_prefix")
                if response["status"] != action_config["expected_status"]:
                    raise RuntimeError(f"S3 action {action_key} returned unexpected status")
                if expected_message and response["message"] != expected_message:
                    raise RuntimeError(f"S3 action {action_key} returned unexpected message")
                if expected_prefix and not response["message"].startswith(expected_prefix):
                    raise RuntimeError(f"S3 action {action_key} returned unexpected message")
                if action_key in selected_actions:
                    fixture_config = {
                        **action_config,
                        "expected_message": response["message"],
                    }
                    write_action_fixture(
                        PAYLOAD_ROOT,
                        action_key,
                        fixture_config,
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
        raise RuntimeError("S3 payload generation completed but cleanup failed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture S3 payloads for offline tests.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to resource_config.json")
    parser.add_argument("--checks", type=Path, default=CHECKS_PATH, help="Path to checks.json")
    parser.add_argument("--payload-check-map", type=Path, default=PAYLOAD_CHECK_MAP_PATH)
    parser.add_argument("--all-checks", action="store_true", help="Regenerate all mapped S3 check scenarios")
    parser.add_argument("--actions", type=Path, default=ACTIONS_PATH)
    parser.add_argument("--checks-only", action="store_true")
    parser.add_argument("--actions-only", action="store_true")
    parser.add_argument("--all-actions", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--state", type=Path, default=None, help="Path to the capture state emitted by s3_resource_creation.py")
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
