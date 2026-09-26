"""Capture ElastiCache baseline payloads and emit scenario fixtures for offline tests."""

from __future__ import annotations

import argparse
import json
import sys
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import boto3

CURRENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests_generator.elasticache.elasticache_resource_creation import (
    CONFIG_PATH,
    ElastiCachePayloadResourceManager,
    _load_json,
)
from tests_generator.payload_selection import select_check_scenarios
from tests_generator.action_capture import action_payload_complete, execute_recorded_action, write_action_fixture
from tests_generator.capture_gate import require_capture_gate


CHECKS_PATH = Path(__file__).with_name("checks.json")
ACTIONS_PATH = Path(__file__).with_name("actions.json")
PAYLOAD_CHECK_MAP_PATH = REPO_ROOT / "tests_generator" / "payload_check_map.json"
PAYLOAD_ROOT = REPO_ROOT / "tests" / "payloads" / "elasticache"
METRICS = ("CurrItems", "KeyCount")


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


def _metric_filename(alias: str, metric_name: str) -> str:
    return f"get_metric_statistics__{alias}__{metric_name.lower()}.json"


def _validate_checks_contract(checks: Dict[str, Any]) -> None:
    forbidden_keys = {"inventory_overrides", "response_overrides", "describe_overrides", "tag_overrides"}
    for check_id, check_config in checks.items():
        for scenario_name, scenario_config in check_config.get("scenarios", {}).items():
            overlap = forbidden_keys.intersection(scenario_config.keys())
            if overlap:
                keys = ", ".join(sorted(overlap))
                raise ValueError(
                    f"ElastiCache payload scenario {check_id}/{scenario_name} uses forbidden structural override keys: {keys}"
                )


def _capture_baselines(session, region: str, state: Dict[str, Any], start_time: datetime, end_time: datetime) -> Dict[str, Dict[str, Any]]:
    elasticache_client = session.client("elasticache", region_name=region)
    cloudwatch_client = session.client("cloudwatch", region_name=region)
    baselines: Dict[str, Dict[str, Any]] = {
        "describe_cache_clusters": elasticache_client.describe_cache_clusters(ShowCacheNodeInfo=True),
        "describe_replication_groups": elasticache_client.describe_replication_groups(),
    }
    for alias, resource in state.get("resources", {}).items():
        if resource["resource_type"] == "elasticache_cluster":
            resource_id = resource["cluster_id"]
            dimensions = [{"Name": "CacheClusterId", "Value": resource_id}]
        else:
            resource_id = resource["replication_group_id"]
            dimensions = [{"Name": "ReplicationGroupId", "Value": resource_id}]
        for metric_name in METRICS:
            baselines[_metric_filename(alias, metric_name)] = cloudwatch_client.get_metric_statistics(
                Namespace="AWS/ElastiCache",
                MetricName=metric_name,
                Dimensions=dimensions,
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
    manager = ElastiCachePayloadResourceManager(config_path=config_path, apply=True)
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
            "elasticache", checks, _load_json(payload_check_map_path), PAYLOAD_ROOT, regenerate_all_checks
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
            substitutions[resource["placeholder_key"]] = resource.get("cluster_id") or resource.get("replication_group_id")

        reverse_map = {actual: placeholder for placeholder, actual in state.get("placeholder_map", {}).items()}
        if state.get("account_id") and config["placeholder_values"].get("ACCOUNT_ID"):
            reverse_map[state["account_id"]] = config["placeholder_values"]["ACCOUNT_ID"]
        baselines = _capture_baselines(session, region, state, start_time, end_time) if selected_checks else {}

        for check_id, check_config in selected_checks.items():
            for scenario_name, scenario_config in check_config.get("scenarios", {}).items():
                scenario_dir = PAYLOAD_ROOT / check_id / scenario_name
                capture_resources = scenario_config.get("capture_resources", [])
                selected_cluster_ids = {
                    state["resources"][alias]["cluster_id"]
                    for alias in capture_resources
                    if state["resources"][alias]["resource_type"] == "elasticache_cluster"
                }
                selected_rg_ids = {
                    state["resources"][alias]["replication_group_id"]
                    for alias in capture_resources
                    if state["resources"][alias]["resource_type"] == "elasticache_replication_group"
                }
                selected_member_cluster_ids = {
                    cluster_id
                    for alias in capture_resources
                    if state["resources"][alias]["resource_type"] == "elasticache_replication_group"
                    for cluster_id in state["resources"][alias].get("member_clusters", [])
                }
                resource_placeholders = {
                    alias: config["placeholder_values"][state["resources"][alias]["placeholder_key"]]
                    for alias in capture_resources
                }
                resource_types = {
                    alias: state["resources"][alias]["resource_type"]
                    for alias in capture_resources
                }

                calls: List[Dict[str, Any]] = []
                if "describe_cache_clusters.json" in scenario_config.get("payload_files", []):
                    payload = deepcopy(baselines["describe_cache_clusters"])
                    payload["CacheClusters"] = [
                        cluster
                        for cluster in payload.get("CacheClusters", [])
                        if cluster.get("CacheClusterId")
                        in selected_cluster_ids | selected_member_cluster_ids
                    ]
                    _write_json(scenario_dir / "describe_cache_clusters.json", _sanitize_response(payload, reverse_map))
                    calls.append(
                        {
                            "service": "elasticache",
                            "operation": "describe_cache_clusters",
                            "response_file": "describe_cache_clusters.json",
                        }
                    )

                if "describe_replication_groups.json" in scenario_config.get("payload_files", []):
                    payload = deepcopy(baselines["describe_replication_groups"])
                    payload["ReplicationGroups"] = [
                        group
                        for group in payload.get("ReplicationGroups", [])
                        if group.get("ReplicationGroupId") in selected_rg_ids
                    ]
                    _write_json(scenario_dir / "describe_replication_groups.json", _sanitize_response(payload, reverse_map))
                    calls.append(
                        {
                            "service": "elasticache",
                            "operation": "describe_replication_groups",
                            "response_file": "describe_replication_groups.json",
                        }
                    )

                metric_overrides = scenario_config.get("metric_overrides", {})
                for alias in capture_resources:
                    resource = state["resources"][alias]
                    resource_id = resource.get("cluster_id") or resource.get("replication_group_id")
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
                                "resource_alias": alias,
                                "resource_id": resource_placeholders[alias],
                                "resource_type": resource["resource_type"],
                                "metric_name": metric_name,
                                "response_file": filename,
                            }
                        )

                capture_metadata = {
                    "check_id": check_id,
                    "scenario": scenario_name,
                    "description": scenario_config["description"],
                    "resource_placeholders": resource_placeholders,
                    "resource_types": resource_types,
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

            elasticache = session.client("elasticache", region_name=region)
            for action_key, action_config in actions.items():
                resource = state["resources"][action_config["resource_ref"]]
                resource_id = resource.get("cluster_id") or resource.get("replication_group_id")
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
                if action_key in {"elasticache_downsize", "elasticache_migrate_graviton"}:
                    for _ in range(120):
                        cluster = elasticache.describe_cache_clusters(
                            CacheClusterId=resource_id,
                            ShowCacheNodeInfo=True,
                        ).get("CacheClusters", [{}])[0]
                        if cluster.get("CacheClusterStatus") == "available":
                            break
                        time.sleep(15)
                    else:
                        raise RuntimeError(f"ElastiCache cluster {resource_id} did not become available")
                if action_key == "elasticache_upgrade_valkey":
                    for _ in range(120):
                        group = elasticache.describe_replication_groups(
                            ReplicationGroupId=resource_id,
                        ).get("ReplicationGroups", [{}])[0]
                        if group.get("Status") == "available":
                            break
                        time.sleep(15)
                    else:
                        raise RuntimeError(
                            f"ElastiCache replication group {resource_id} did not become available"
                        )
    except Exception as exc:
        generation_error = exc
    finally:
        cleanup_summary = _finalize_cleanup(config_path, state, state_path)
        print(json.dumps(cleanup_summary, indent=2, sort_keys=True))

    if generation_error is not None:
        raise generation_error
    if cleanup_summary["errors"]:
        raise RuntimeError("ElastiCache payload generation completed but cleanup failed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture ElastiCache payloads for offline tests.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to resource_config.json")
    parser.add_argument("--checks", type=Path, default=CHECKS_PATH, help="Path to checks.json")
    parser.add_argument("--payload-check-map", type=Path, default=PAYLOAD_CHECK_MAP_PATH)
    parser.add_argument("--all-checks", action="store_true", help="Regenerate all mapped ElastiCache check scenarios")
    parser.add_argument("--actions", type=Path, default=ACTIONS_PATH)
    parser.add_argument("--checks-only", action="store_true")
    parser.add_argument("--actions-only", action="store_true")
    parser.add_argument("--all-actions", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--state", type=Path, default=None, help="Path to the capture state emitted by elasticache_resource_creation.py")
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
