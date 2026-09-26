"""Create ElastiCache resources used for payload capture."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError
from tests_generator.capture_gate import require_capture_gate


CONFIG_PATH = Path(__file__).with_name("resource_config.json")
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


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


class ElastiCachePayloadResourceManager:
    """Create and clean up ElastiCache resources used for payload capture."""

    def __init__(self, config_path: Optional[Path] = None, *, apply: bool = False):
        require_capture_gate(apply)
        self.config_path = config_path or CONFIG_PATH
        self.config = _load_json(self.config_path)
        self.state_path = self.config_path.parents[2] / self.config["state_file"]
        profile = self.config.get("profile")
        self.region = self.config["region"]
        if profile:
            self.session = boto3.Session(profile_name=profile, region_name=self.region)
        else:
            self.session = boto3.Session(region_name=self.region)
        self.elasticache = self.session.client("elasticache", region_name=self.region)
        self.sts = self.session.client("sts")
        self.timestamp = int(time.time())
        self.account_id = self.sts.get_caller_identity()["Account"]

    def _identifier(self, placeholder_value: str, max_length: int) -> str:
        # Include a short hash token so long placeholder names that share a prefix
        # still generate distinct AWS identifiers after truncation.
        hash_token = hashlib.sha1(placeholder_value.encode("utf-8")).hexdigest()[:6]
        suffix = f"{hash_token}-{self.timestamp}"
        max_prefix_length = max_length - len(suffix) - 1
        trimmed = placeholder_value.lower()[:max_prefix_length].rstrip("-")
        return f"{trimmed}-{suffix}"

    def _describe_cluster(self, cluster_id: str) -> Dict[str, Any]:
        response = self.elasticache.describe_cache_clusters(CacheClusterId=cluster_id, ShowCacheNodeInfo=True)
        clusters = response.get("CacheClusters", [])
        if not clusters:
            raise RuntimeError(f"Unable to find created ElastiCache cluster {cluster_id}")
        return clusters[0]

    def _describe_replication_group(self, replication_group_id: str) -> Dict[str, Any]:
        response = self.elasticache.describe_replication_groups(ReplicationGroupId=replication_group_id)
        groups = response.get("ReplicationGroups", [])
        if not groups:
            raise RuntimeError(f"Unable to find created replication group {replication_group_id}")
        return groups[0]

    def create_resources(self) -> Dict[str, Any]:
        placeholders = dict(self.config.get("placeholder_values", {}))
        placeholders["REGION"] = self.region
        placeholders["ACCOUNT_ID"] = self.account_id

        placeholder_map: Dict[str, str] = {
            placeholders["ACCOUNT_ID"]: self.account_id,
        }
        state: Dict[str, Any] = {
            "service": "elasticache",
            "region": self.region,
            "account_id": self.account_id,
            "created_at_epoch": self.timestamp,
            "resources": {},
            "placeholder_map": placeholder_map,
        }

        try:
            cluster_waiter = self.elasticache.get_waiter("cache_cluster_available")
            rg_waiter = self.elasticache.get_waiter("replication_group_available")
            pending_clusters = []
            pending_replication_groups = []

            for alias, definition in self.config.get("cache_cluster_definitions", {}).items():
                placeholder_key = definition["identifier_placeholder_key"]
                placeholder_value = placeholders[placeholder_key]
                actual_identifier = self._identifier(placeholder_value, 50)
                tags = _render_templates(definition.get("tags", {}), {**placeholders, placeholder_key: actual_identifier})
                self.elasticache.create_cache_cluster(
                    CacheClusterId=actual_identifier,
                    CacheNodeType=definition["cache_node_type"],
                    Engine=definition["engine"],
                    NumCacheNodes=definition["num_cache_nodes"],
                    Tags=[{"Key": key, "Value": value} for key, value in tags.items()],
                )
                state["resources"][alias] = {
                    "resource_type": "elasticache_cluster",
                    "cluster_id": actual_identifier,
                    "placeholder_key": placeholder_key,
                    "placeholder_value": placeholder_value,
                    "cache_node_type": definition["cache_node_type"],
                    "engine": definition["engine"],
                    "engine_version": None,
                }
                placeholder_map[placeholder_value] = actual_identifier
                pending_clusters.append((alias, actual_identifier))

            for alias, definition in self.config.get("replication_group_definitions", {}).items():
                placeholder_key = definition["identifier_placeholder_key"]
                placeholder_value = placeholders[placeholder_key]
                actual_identifier = self._identifier(placeholder_value, 40)
                tags = _render_templates(definition.get("tags", {}), {**placeholders, placeholder_key: actual_identifier})
                self.elasticache.create_replication_group(
                    ReplicationGroupId=actual_identifier,
                    ReplicationGroupDescription="MaxOps payload capture replication group.",
                    CacheNodeType=definition["cache_node_type"],
                    Engine=definition["engine"],
                    EngineVersion=definition["engine_version"],
                    NumCacheClusters=definition["num_cache_clusters"],
                    AutomaticFailoverEnabled=definition["automatic_failover_enabled"],
                    Tags=[{"Key": key, "Value": value} for key, value in tags.items()],
                )
                state["resources"][alias] = {
                    "resource_type": "elasticache_replication_group",
                    "replication_group_id": actual_identifier,
                    "placeholder_key": placeholder_key,
                    "placeholder_value": placeholder_value,
                    "cache_node_type": definition["cache_node_type"],
                    "engine": definition["engine"],
                    "engine_version": None,
                    "member_clusters": [],
                }
                placeholder_map[placeholder_value] = actual_identifier
                pending_replication_groups.append((alias, actual_identifier))

            for alias, actual_identifier in pending_clusters:
                cluster_waiter.wait(CacheClusterId=actual_identifier, WaiterConfig={"Delay": 30, "MaxAttempts": 40})
                cluster = self._describe_cluster(actual_identifier)
                state["resources"][alias]["engine_version"] = cluster.get("EngineVersion")

            for alias, actual_identifier in pending_replication_groups:
                rg_waiter.wait(ReplicationGroupId=actual_identifier, WaiterConfig={"Delay": 30, "MaxAttempts": 40})
                rg = self._describe_replication_group(actual_identifier)
                state["resources"][alias]["engine_version"] = rg.get("EngineVersion")
                state["resources"][alias]["member_clusters"] = rg.get("MemberClusters", [])
        except Exception as exc:
            try:
                self.cleanup_resources(state=state)
            except Exception as cleanup_exc:
                raise RuntimeError(
                    "ElastiCache payload capture resource creation failed and rollback also failed: "
                    f"{cleanup_exc}"
                ) from exc
            raise RuntimeError(
                "ElastiCache payload capture resource creation failed; partial resources were rolled back."
            ) from exc

        _write_json(self.state_path, state)
        return state

    def cleanup_resources(self, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = state or _load_json(self.state_path)
        resources = state.get("resources", {})
        targeted_resources = []
        errors = []

        for alias, resource in resources.items():
            if resource["resource_type"] == "elasticache_cluster":
                targeted_resources.append(
                    {
                        "resource_type": "elasticache_cluster",
                        "resource_alias": alias,
                        "resource_name": resource["cluster_id"],
                    }
                )
            else:
                targeted_resources.append(
                    {
                        "resource_type": "elasticache_replication_group",
                        "resource_alias": alias,
                        "resource_name": resource["replication_group_id"],
                    }
                )

        for alias, resource in resources.items():
            if resource["resource_type"] == "elasticache_replication_group":
                resource_name = resource["replication_group_id"]
                try:
                    self.elasticache.delete_replication_group(
                        ReplicationGroupId=resource_name,
                        RetainPrimaryCluster=False,
                    )
                except ClientError as exc:
                    error_code = exc.response.get("Error", {}).get("Code")
                    if error_code not in {
                        "ReplicationGroupNotFoundFault",
                        "InvalidReplicationGroupState",
                    }:
                        errors.append(
                            {
                                "resource_type": "elasticache_replication_group",
                                "resource_alias": alias,
                                "resource_name": resource_name,
                                "operation": "delete_replication_group",
                                "message": str(exc),
                            }
                        )
            else:
                resource_name = resource["cluster_id"]
                try:
                    self.elasticache.delete_cache_cluster(CacheClusterId=resource_name)
                except ClientError as exc:
                    error_code = exc.response.get("Error", {}).get("Code")
                    if error_code not in {
                        "CacheClusterNotFound",
                        "InvalidCacheClusterState",
                    }:
                        errors.append(
                            {
                                "resource_type": "elasticache_cluster",
                                "resource_alias": alias,
                                "resource_name": resource_name,
                                "operation": "delete_cache_cluster",
                                "message": str(exc),
                            }
                        )

        for alias, resource in resources.items():
            if resource["resource_type"] == "elasticache_replication_group":
                resource_name = resource["replication_group_id"]
                waiter_name = "replication_group_deleted"
                kwargs = {"ReplicationGroupId": resource_name, "WaiterConfig": {"Delay": 30, "MaxAttempts": 40}}
            else:
                resource_name = resource["cluster_id"]
                waiter_name = "cache_cluster_deleted"
                kwargs = {"CacheClusterId": resource_name, "WaiterConfig": {"Delay": 30, "MaxAttempts": 40}}
            if any(error["resource_name"] == resource_name for error in errors):
                continue
            try:
                waiter = self.elasticache.get_waiter(waiter_name)
                waiter.wait(**kwargs)
            except Exception as exc:
                errors.append(
                    {
                        "resource_type": resource["resource_type"],
                        "resource_alias": alias,
                        "resource_name": resource_name,
                        "operation": "wait_for_deletion",
                        "message": str(exc),
                    }
                )

        failed_resources = {(error["resource_type"], error["resource_name"]) for error in errors}
        targeted_count = len(targeted_resources)
        failed_count = len(failed_resources)
        return {
            "service": "elasticache",
            "cleanup_attempted": True,
            "cleanup_status": "success" if not errors else "partial_failure",
            "state_file": str(self.state_path),
            "resource_counts": {
                "targeted": targeted_count,
                "succeeded": targeted_count - failed_count,
                "failed": failed_count,
            },
            "resources_targeted": targeted_resources,
            "errors": errors,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or clean up ElastiCache payload-capture resources.")
    parser.add_argument(
        "command",
        nargs="?",
        default="create",
        choices=["create", "cleanup"],
        help="Operation to perform (default: create).",
    )
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to resource_config.json")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    require_capture_gate(args.apply)
    manager = ElastiCachePayloadResourceManager(config_path=args.config, apply=args.apply)
    if args.command == "create":
        print(json.dumps(manager.create_resources(), indent=2, sort_keys=True))
        return
    summary = manager.cleanup_resources()
    print(json.dumps(summary, indent=2, sort_keys=True))
    if summary["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
