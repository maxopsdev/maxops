import argparse
import ast
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from app.config import settings
from app.adapters.aws.adapter import AWSAdapter
from app.checks.registry import check_registry
import app.checks  # noqa: F401


def _coerce_scalar(value: str) -> Any:
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _render_value(value: Any, variables: Dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _render_value(val, variables) for key, val in value.items()}
    if isinstance(value, list):
        return [_render_value(item, variables) for item in value]
    if isinstance(value, str):
        rendered = value.format(**variables) if "{" in value else value
        return _coerce_scalar(rendered)
    return value


def _parse_create_output(stdout: str, output_type: str) -> Dict[str, Any]:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("Create script did not produce output.")
    last_line = lines[-1]
    if output_type == "json":
        return json.loads(last_line)
    if output_type == "python_dict":
        return ast.literal_eval(last_line)
    raise RuntimeError(f"Unsupported create_output type: {output_type}")


def _run_script(script_path: str, args: List[str], label: str) -> Tuple[int, str, str]:
    cmd = [sys.executable, script_path, *args]
    print(f"[RUN] {label}: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if stdout:
        print(f"[OUT] {label}:\n{stdout}")
    if stderr:
        print(f"[ERR] {label}:\n{stderr}")
    return result.returncode, stdout, stderr


def _get_account_id(adapter: AWSAdapter, region: str) -> str:
    try:
        sts = adapter.session.client("sts", region_name=region)
        return sts.get_caller_identity().get("Account", "unknown")
    except Exception:
        return "unknown"


def _select_resource_id(
    selector: Dict[str, Any],
    created_output: Dict[str, Any],
    check_results: Dict[str, List[Dict[str, Any]]],
    default_check_id: str,
) -> Optional[str]:
    if "resource_id" in selector:
        return selector["resource_id"]

    source = selector.get("source")
    if source == "created":
        key = selector.get("key")
        if not key:
            return None
        return created_output.get(key)
    if source == "check_results":
        check_id = selector.get("check_id", default_check_id)
        resources = check_results.get(check_id, [])
        if not resources:
            return None
        pick = selector.get("pick", "first")
        if pick == "first":
            return resources[0].get("resource_id")
        if pick == "last":
            return resources[-1].get("resource_id")
        if isinstance(pick, int) and 0 <= pick < len(resources):
            return resources[pick].get("resource_id")
    return None


def _execute_action(
    adapter: AWSAdapter,
    check_id: str,
    action_key: str,
    account_id: str,
    region: str,
    resource_id: str,
    parameters: Optional[Dict[str, Any]] = None,
) -> None:
    from app.actions import action_registry  # local import for optional deps
    from app.actions.base import ActionExecutionContext
    from app.models.action_execution import ActionExecution
    from app.schemas.check import CheckActionRequest
    import app.actions  # noqa: F401

    payload = CheckActionRequest(
        action=action_key,
        account_id=account_id,
        region=region,
        resource_id=resource_id,
        parameters=parameters or {},
    )
    action_execution = ActionExecution(
        check_id=check_id,
        resource_id=resource_id,
        action=payload.action,
        status="running",
        account_id=account_id,
        region=region,
        parameters_json=payload.parameters,
    )
    context = ActionExecutionContext(
        check_id=check_id,
        action_key=action_key,
        payload=payload,
        check=check_registry.get_check(check_id),
        aws_adapter=adapter,
        db=None,
        action_execution=action_execution,
    )
    response = action_registry.execute(context)
    if response is None:
        raise RuntimeError(f"Action '{action_key}' not handled for check '{check_id}'.")
    print(f"[ACTION] {check_id}:{action_key} -> {response.status}: {response.message}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resource-type", required=True)
    parser.add_argument("--config", default="tests/test_orchestration.yaml")
    parser.add_argument("--profile", default="maxops")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--prefix", default="maxops_test")
    parser.add_argument("--wait-seconds", type=int, default=None)
    parser.add_argument("--skip-actions", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--var", action="append", default=[])
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}

    resource_types = config.get("resource_types", {})
    resource_config = resource_types.get(args.resource_type)
    if not resource_config:
        raise RuntimeError(f"Resource type '{args.resource_type}' not found in {args.config}.")

    variables: Dict[str, Any] = dict(config.get("vars", {}))
    variables.update({
        "profile": args.profile,
        "region": args.region,
        "prefix": args.prefix,
    })
    for pair in args.var:
        if "=" not in pair:
            raise RuntimeError(f"Invalid --var format: {pair} (expected key=value).")
        key, value = pair.split("=", 1)
        variables[key] = _coerce_scalar(value)

    settings.aws_profile = args.profile
    settings.aws_region = args.region

    create_script = resource_config.get("create_script")
    created_output: Dict[str, Any] = {}
    if create_script:
        create_args = _render_value(resource_config.get("create_args", []), variables)
        print("[STEP] Creating test resources...")
        code, stdout, _ = _run_script(create_script, create_args, "create")
        if code != 0:
            raise RuntimeError("Create script failed.")
        output_type = resource_config.get("create_output", "python_dict")
        created_output = _parse_create_output(stdout, output_type)
        print(f"[STEP] Create output: {created_output}")

    wait_seconds = args.wait_seconds
    if wait_seconds is None:
        wait_seconds = resource_config.get("wait_seconds", 0)
    if wait_seconds and wait_seconds > 0:
        print(f"[STEP] Waiting {wait_seconds} seconds for metrics to populate...")
        time.sleep(wait_seconds)

    adapter = AWSAdapter(default_region=args.region)
    account_id = _get_account_id(adapter, args.region)

    checks = resource_config.get("checks", [])
    check_results: Dict[str, List[Dict[str, Any]]] = {}
    executed_checks: List[Dict[str, Any]] = []
    executed_actions: List[Dict[str, Any]] = []
    for check in checks:
        check_id = check.get("id")
        if not check_id:
            raise RuntimeError("Check entry missing id.")
        parameters = _render_value(check.get("parameters", {}), variables)
        print(f"[STEP] Running check {check_id} with params {parameters}...")
        resources = check_registry.execute_check(check_id, adapter, parameters=parameters)
        check_results[check_id] = resources
        resource_ids = [r.get("resource_id") for r in resources]
        print(f"[RESULT] {check_id}: {len(resources)} resources -> {resource_ids}")
        executed_checks.append(
            {
                "check_id": check_id,
                "resources_found": len(resources),
                "resource_ids": resource_ids,
            }
        )

    actions = resource_config.get("actions", [])
    if actions and not args.skip_actions:
        print("[STEP] Executing actions...")
        for action in actions:
            check_id = action.get("check_id")
            action_key = action.get("action_key")
            if not check_id or not action_key:
                raise RuntimeError("Action entry missing check_id or action_key.")
            selector = _render_value(action.get("resource_selector", {}), variables)
            resource_id = _select_resource_id(selector, created_output, check_results, check_id)
            if not resource_id and selector.get("fallback") == "first":
                resources = check_results.get(check_id, [])
                if resources:
                    resource_id = resources[0].get("resource_id")
            if not resource_id:
                print(f"[WARN] No resource found for action {check_id}:{action_key}. Skipping.")
                continue
            action_params = _render_value(action.get("parameters", {}), variables)
            print(f"[STEP] Action {action_key} on {resource_id} for check {check_id}...")
            _execute_action(
                adapter,
                check_id,
                action_key,
                account_id,
                args.region,
                resource_id,
                parameters=action_params,
            )
            executed_actions.append(
                {
                    "check_id": check_id,
                    "action_key": action_key,
                    "resource_id": resource_id,
                }
            )
    elif actions:
        print("[STEP] Actions skipped by --skip-actions.")

    if args.cleanup and resource_config.get("destroy_script"):
        destroy_script = resource_config["destroy_script"]
        destroy_args = _render_value(resource_config.get("destroy_args", []), variables)
        print("[STEP] Cleaning up resources...")
        code, _, _ = _run_script(destroy_script, destroy_args, "cleanup")
        if code != 0:
            raise RuntimeError("Cleanup script failed.")

    print("[SUMMARY] Checks executed:")
    if executed_checks:
        for item in executed_checks:
            print(f"- {item['check_id']}: {item['resources_found']} resources ({item['resource_ids']})")
    else:
        print("- None")

    print("[SUMMARY] Actions executed:")
    if executed_actions:
        for item in executed_actions:
            print(f"- {item['check_id']} -> {item['action_key']} on {item['resource_id']}")
    else:
        print("- None")


if __name__ == "__main__":
    main()
