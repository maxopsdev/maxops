"""Capture and sanitize SageMaker payloads behind the explicit AWS cost gate."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict


CURRENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests_generator.capture_gate import require_capture_gate
from tests_generator.payload_selection import select_check_scenarios
from tests_generator.sagemaker.sagemaker_resource_creation import (
    CONFIG_PATH,
    SageMakerPayloadResourceManager,
    _load_json,
)


CHECKS_PATH = CURRENT_DIR / "checks.json"
ACTIONS_PATH = CURRENT_DIR / "actions.json"
PAYLOAD_CHECK_MAP_PATH = REPO_ROOT / "tests_generator" / "payload_check_map.json"
PAYLOAD_ROOT = REPO_ROOT / "tests" / "payloads" / "sagemaker"


def _write_json(path: Path, payload: Any) -> None:
    """Write one sanitized payload with deterministic formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _json_safe(value: Any) -> Any:
    """Convert SDK timestamps and nested values into JSON-compatible values."""
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value


def _sanitize(value: Any, replacements: Dict[str, str]) -> Any:
    """Replace capture identifiers recursively while preserving AWS shape."""
    value = _json_safe(value)
    if isinstance(value, dict):
        return {key: _sanitize(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item, replacements) for item in value]
    if isinstance(value, str):
        result = value
        for actual, placeholder in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
            result = result.replace(actual, placeholder)
        return result
    return value


def _override_metric_response(response: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """Apply only the permitted narrow value overrides to a real response."""
    updated = deepcopy(response)
    for result in updated.get("MetricDataResults", []):
        label = str(result.get("Label") or result.get("Id") or "")
        for metric_name, override in overrides.items():
            if metric_name.casefold() not in label.casefold() and metric_name.casefold() not in str(result.get("Id") or "").casefold():
                continue
            if isinstance(override, dict):
                value = override.get("Average", override.get("value"))
            else:
                value = override
            if value is not None:
                result["Values"] = [float(value)]
    return updated


def _resource_names(state: Dict[str, Any], alias: str) -> list[str]:
    """Return one or more actual resource names for a state alias."""
    resource = state["resources"][alias]
    return resource.get("names") or [resource["name"]]


def _metric_payload(cloudwatch: Any, resource: Dict[str, Any], start: datetime, end: datetime) -> Dict[str, Any]:
    """Capture a real GetMetricData response for the resource kind."""
    kind = resource["kind"]
    if kind == "endpoint":
        dimensions = [
            {"Name": "EndpointName", "Value": resource["name"]},
            {"Name": "VariantName", "Value": "AllTraffic"},
        ]
        namespace = "AWS/SageMaker"
        metrics = ["Invocations", "InvocationsPerInstance", "ModelLatency"]
    elif kind == "notebook":
        dimensions = [{"Name": "NotebookInstanceName", "Value": resource["name"]}]
        namespace = "/aws/sagemaker/NotebookInstances"
        metrics = ["cpu_usage_active", "cpu_usage_user", "mem_used_percent", "nvidia_smi_utilization_gpu"]
    else:
        dimensions = [{"Name": "Host", "Value": f"{resource['names'][0]}/algo-1"}]
        namespace = "/aws/sagemaker/TrainingJobs"
        metrics = ["CPUUtilization", "MemoryUtilization", "GPUUtilization", "GPUMemoryUtilization"]
    queries = [
        {
            "Id": f"metric_{index}",
            "MetricStat": {"Metric": {"Namespace": namespace, "MetricName": metric, "Dimensions": dimensions}, "Period": 60, "Stat": "Average"},
            "ReturnData": True,
        }
        for index, metric in enumerate(metrics)
    ]
    return cloudwatch.get_metric_data(MetricDataQueries=queries, StartTime=start, EndTime=end, ScanBy="TimestampDescending")


def capture_payloads(
    *,
    apply: bool = False,
    config_path: Path = CONFIG_PATH,
    checks_path: Path = CHECKS_PATH,
    actions_path: Path = ACTIONS_PATH,
    all_checks: bool = False,
    all_actions: bool = False,
    checks_only: bool = False,
    actions_only: bool = False,
) -> Dict[str, Any]:
    """Capture missing mapped scenarios, action calls, and cleanup in ``finally``."""
    require_capture_gate(apply, resource_plan="SageMaker payload capture")
    manager = SageMakerPayloadResourceManager(config_path, apply=apply)
    config = manager.config
    state = _load_json(manager.state_path)
    checks = _load_json(checks_path)
    selected = {} if actions_only else select_check_scenarios(
        "sagemaker", checks, _load_json(PAYLOAD_CHECK_MAP_PATH), PAYLOAD_ROOT, regenerate_all=all_checks
    )
    actions = {} if checks_only else _load_json(actions_path)
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=config.get("capture_window_hours", 24))
    replacements = {actual: placeholder for placeholder, actual in state.get("placeholder_map", {}).items()}
    summary: Dict[str, Any] = {"checks": [], "actions": [], "cleanup": None}
    try:
        session = manager.session
        sagemaker = session.client("sagemaker", region_name=manager.region)
        cloudwatch = session.client("cloudwatch", region_name=manager.region)
        for check_id, check_config in selected.items():
            for scenario, scenario_config in check_config["scenarios"].items():
                aliases = check_config.get("resource_refs") or []
                alias = aliases[0]
                resource = state["resources"][alias]
                names = _resource_names(state, alias)
                root = PAYLOAD_ROOT / check_id / scenario
                requested = set(scenario_config.get("payload_files", []))
                if "list_notebook_instances.json" in requested:
                    _write_json(root / "list_notebook_instances.json", _sanitize(sagemaker.list_notebook_instances(), replacements))
                if "list_endpoints.json" in requested:
                    _write_json(root / "list_endpoints.json", _sanitize(sagemaker.list_endpoints(), replacements))
                if "list_training_jobs.json" in requested:
                    _write_json(root / "list_training_jobs.json", _sanitize(sagemaker.list_training_jobs(), replacements))
                name = names[0]
                if resource["kind"] == "notebook":
                    described = sagemaker.describe_notebook_instance(NotebookInstanceName=name)
                    if "describe_notebook_instance.json" in requested:
                        _write_json(root / "describe_notebook_instance.json", _sanitize(described, replacements))
                    lifecycle = described.get("NotebookInstanceLifecycleConfigName")
                    if lifecycle and "describe_notebook_instance_lifecycle_config.json" in requested:
                        payload = sagemaker.describe_notebook_instance_lifecycle_config(NotebookInstanceLifecycleConfigName=lifecycle)
                        _write_json(root / "describe_notebook_instance_lifecycle_config.json", _sanitize(payload, replacements))
                    if "list_metrics.json" in requested:
                        _write_json(root / "list_metrics.json", _sanitize(cloudwatch.list_metrics(Dimensions=[{"Name": "NotebookInstanceName", "Value": name}]), replacements))
                elif resource["kind"] == "endpoint":
                    described = sagemaker.describe_endpoint(EndpointName=name)
                    if "describe_endpoint.json" in requested:
                        _write_json(root / "describe_endpoint.json", _sanitize(described, replacements))
                    config_name = described["EndpointConfigName"]
                    if "describe_endpoint_config.json" in requested:
                        payload = sagemaker.describe_endpoint_config(EndpointConfigName=config_name)
                        _write_json(root / "describe_endpoint_config.json", _sanitize(payload, replacements))
                    if "describe_scaling_policies.json" in requested:
                        scaling = session.client("application-autoscaling", region_name=manager.region)
                        payload = scaling.describe_scaling_policies(ServiceNamespace="sagemaker", ResourceId=f"endpoint/{name}/variant/AllTraffic", ScalableDimension="sagemaker:variant:DesiredInstanceCount")
                        _write_json(root / "describe_scaling_policies.json", _sanitize(payload, replacements))
                else:
                    if "describe_training_job.json" in requested:
                        payload = sagemaker.describe_training_job(TrainingJobName=name)
                        _write_json(root / "describe_training_job.json", _sanitize(payload, replacements))
                if "get_metric_data.json" in requested:
                    payload = _metric_payload(cloudwatch, resource, start, end)
                    payload = _override_metric_response(payload, scenario_config.get("metric_overrides", {}))
                    _write_json(root / "get_metric_data.json", _sanitize(payload, replacements))
                _write_json(root / "capture_metadata.json", {
                    "check_id": check_id,
                    "scenario": scenario,
                    "description": scenario_config.get("description", check_config.get("description", "")),
                    "expected_matches": scenario_config.get("expected_matches", []),
                    "captured_at": end.isoformat(),
                    "capture_source": "aws",
                })
                summary["checks"].append(f"{check_id}/{scenario}")
        if actions:
            from tests_generator.action_capture import execute_recorded_action, write_action_fixture
            from app.checks.registry import check_registry

            account_id = config.get("account_id", "000000000000")
            for action_key, action_config in actions.items():
                if not all_actions and (PAYLOAD_ROOT / "actions" / action_key / action_config["scenario"] / "capture_metadata.json").is_file():
                    continue
                resource = state["resources"][action_config["resource_ref"]]
                resource_id = (resource.get("names") or [resource.get("name")])[0]
                response, calls = execute_recorded_action(session, action_key, action_config, account_id, manager.region, resource_id, check_registry.get_check(action_config["check_id"]))
                write_action_fixture(PAYLOAD_ROOT, action_key, action_config, response, calls, {"region": manager.region, "resource_id": resource_id}, lambda value: _sanitize(value, replacements), _write_json)
                summary["actions"].append(action_key)
    finally:
        summary["cleanup"] = manager.cleanup_resources(state)
    if summary["cleanup"]["errors"]:
        raise RuntimeError("SageMaker payload capture completed but cleanup failed")
    return summary


def main() -> None:
    """Print the resource plan and run capture only after both gates pass."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--checks-only", action="store_true")
    parser.add_argument("--actions-only", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--all-checks", action="store_true")
    parser.add_argument("--all-actions", action="store_true")
    args = parser.parse_args()
    config = _load_json(CONFIG_PATH)
    print(f"SageMaker capture plan: {config['resources']}")
    print(f"Estimated maximum hourly cost: ${config['estimated_hourly_cost_usd']:.2f}")
    result = capture_payloads(
        apply=args.apply,
        all_checks=args.all or args.all_checks,
        all_actions=args.all or args.all_actions,
        checks_only=args.checks_only,
        actions_only=args.actions_only,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
