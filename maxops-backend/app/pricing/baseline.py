"""Baseline monthly pricing for full inventory resources.

These estimators are intentionally independent of check findings. Scans use
them to persist a current monthly cost for every imported resource before
policies calculate potential savings.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from app.checks.base import (
    estimate_dynamodb_ondemand_monthly_cost,
    estimate_dynamodb_provisioned_monthly_cost,
    estimate_ebs_monthly_cost,
    estimate_ec2_monthly_cost,
    estimate_rds_monthly_cost,
)


ELASTICACHE_MONTHLY_COSTS = {
    "cache.t4g.micro": 12.0,
    "cache.t4g.small": 24.0,
    "cache.t4g.medium": 48.0,
    "cache.m6g.large": 104.0,
    "cache.m6g.xlarge": 208.0,
    "cache.m6g.2xlarge": 416.0,
    "cache.m7g.large": 112.0,
    "cache.m7g.xlarge": 224.0,
    "cache.r6g.large": 130.0,
    "cache.r6g.xlarge": 260.0,
    "cache.r7g.large": 138.0,
    "cache.r7g.xlarge": 276.0,
    "cache.t3.micro": 14.0,
    "cache.t3.small": 28.0,
    "cache.t3.medium": 56.0,
    "cache.m5.large": 122.0,
    "cache.m5.xlarge": 244.0,
    "cache.r5.large": 152.0,
    "cache.r5.xlarge": 304.0,
}


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_float(metadata: Dict[str, Any], keys: Iterable[str]) -> Optional[float]:
    for key in keys:
        parsed = _as_float(metadata.get(key))
        if parsed is not None:
            return parsed
    return None


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def current_monthly_cost_from_metadata(metadata: Dict[str, Any]) -> Optional[float]:
    return _first_float(
        metadata,
        (
            "monthly_cost_estimate",
            "estimated_monthly_cost",
            "monthly_cost",
            "current_monthly_cost",
        ),
    )


def estimate_ec2_baseline_monthly_cost(resource: Dict[str, Any]) -> float:
    metadata = resource.get("metadata") or {}
    current = current_monthly_cost_from_metadata(metadata)
    if current is not None:
        return current
    instance_type = _first_text(
        resource.get("instance_type"),
        metadata.get("instance_type"),
        metadata.get("InstanceType"),
    )
    return estimate_ec2_monthly_cost(instance_type or "unknown")


def estimate_rds_baseline_monthly_cost(resource: Dict[str, Any]) -> float:
    metadata = resource.get("metadata") or {}
    current = current_monthly_cost_from_metadata(metadata)
    if current is not None:
        return current
    instance_class = _first_text(
        resource.get("db_instance_class"),
        metadata.get("db_instance_class"),
        metadata.get("instance_class"),
        metadata.get("DBInstanceClass"),
    )
    return estimate_rds_monthly_cost(instance_class or "unknown")


def estimate_ebs_baseline_monthly_cost(resource: Dict[str, Any]) -> float:
    metadata = resource.get("metadata") or {}
    current = current_monthly_cost_from_metadata(metadata)
    if current is not None and current > 0:
        return current
    size_gb = _as_float(
        metadata.get("size")
        or metadata.get("Size")
        or metadata.get("size_gb")
        or resource.get("size_gb")
    )
    volume_type = _first_text(
        metadata.get("volume_type"),
        metadata.get("VolumeType"),
        metadata.get("volumeType"),
        resource.get("volume_type"),
        "gp2",
    )
    return estimate_ebs_monthly_cost(int(size_gb or 0), (volume_type or "gp2").lower())


def estimate_dynamodb_baseline_monthly_cost(resource: Dict[str, Any]) -> float:
    metadata = resource.get("metadata") or {}
    current = current_monthly_cost_from_metadata(metadata)
    if current is not None:
        return current

    read_capacity_units = (
        _as_float(
            metadata.get("read_capacity_units") or metadata.get("ReadCapacityUnits")
        )
        or 0.0
    )
    write_capacity_units = (
        _as_float(
            metadata.get("write_capacity_units") or metadata.get("WriteCapacityUnits")
        )
        or 0.0
    )
    billing_mode = _first_text(
        metadata.get("BillingMode"),
        metadata.get("billing_mode"),
        resource.get("billing_mode"),
    )
    normalized_billing_mode = (billing_mode or "").lower()
    if normalized_billing_mode == "pay_per_request":
        avg_consumed_rcu = _as_float(metadata.get("avg_consumed_rcu")) or 0.0
        avg_consumed_wcu = _as_float(metadata.get("avg_consumed_wcu")) or 0.0
        return estimate_dynamodb_ondemand_monthly_cost(
            avg_consumed_rcu, avg_consumed_wcu
        )
    if (
        normalized_billing_mode == "provisioned"
        or read_capacity_units > 0
        or write_capacity_units > 0
    ):
        return estimate_dynamodb_provisioned_monthly_cost(
            read_capacity_units, write_capacity_units
        )
    return 0.0


def estimate_elasticache_baseline_monthly_cost(resource: Dict[str, Any]) -> float:
    metadata = resource.get("metadata") or {}
    current = current_monthly_cost_from_metadata(metadata)
    if current is not None:
        return current
    node_type = _first_text(
        metadata.get("cache_node_type"),
        metadata.get("CacheNodeType"),
        resource.get("cache_node_type"),
    )
    member_clusters = metadata.get("member_cluster_ids") or metadata.get(
        "MemberClusters"
    )
    replicas = metadata.get("replicas_per_node_group") or metadata.get(
        "ReplicasPerNodeGroup"
    )
    node_groups = _as_float(
        metadata.get("num_node_groups") or metadata.get("NumNodeGroups")
    )
    if isinstance(member_clusters, list) and member_clusters:
        node_count = float(len(member_clusters))
    elif isinstance(replicas, list) and replicas:
        node_count = float(len(replicas)) + sum(
            _as_float(value) or 0.0 for value in replicas
        )
    elif node_groups is not None and replicas is not None:
        node_count = node_groups * (1.0 + (_as_float(replicas) or 0.0))
    else:
        node_count = (
            _as_float(
                metadata.get("node_count")
                or metadata.get("num_cache_nodes")
                or metadata.get("NumCacheNodes")
                or resource.get("node_count")
            )
            or 1.0
        )
    return ELASTICACHE_MONTHLY_COSTS.get(node_type or "", 75.0) * node_count


def estimate_baseline_monthly_cost(
    resource_type: str, resource: Dict[str, Any]
) -> float:
    normalized_type = (resource_type or resource.get("resource_type") or "").lower()
    if normalized_type in {"ec2", "ec2_instance"}:
        return estimate_ec2_baseline_monthly_cost(resource)
    if normalized_type in {"rds", "rds_instance"}:
        return estimate_rds_baseline_monthly_cost(resource)
    if normalized_type in {"ebs", "ebs_volume"}:
        return estimate_ebs_baseline_monthly_cost(resource)
    if normalized_type in {"dynamodb", "dynamodb_table", "dynamodb_gsi"}:
        return estimate_dynamodb_baseline_monthly_cost(resource)
    if normalized_type in {
        "elasticache",
        "elasticache_cluster",
        "elasticache_replication_group",
    }:
        return estimate_elasticache_baseline_monthly_cost(resource)
    return current_monthly_cost_from_metadata(resource.get("metadata") or {}) or 0.0
