"""
EMR Check - No Instance Fleet Enabled (Uses Instance Groups)

Flags EMR clusters that are NOT using Instance Fleets (i.e., instance collection type is INSTANCE_GROUP
or instance fleets count == 0).

Why this matters:
- Instance Fleets can improve flexibility (Spot diversification) and efficiency.
- You typically can't "turn on" instance fleets for an existing cluster; you migrate by creating a new
  cluster configuration. So default action is "review/migrate".

Assumptions:
- aws_adapter.get_resources("emr_cluster", {...}, region) returns clusters with metadata containing one of:
  - InstanceCollectionType: "INSTANCE_FLEET" or "INSTANCE_GROUP"
  - instance_fleets: list
- If not present, we try to fetch instance fleets via:
  - aws_adapter.get_resources("emr_instance_fleet", {"cluster_id": <id>}, region) OR
  - aws_adapter.get_emr_instance_fleets(cluster_id, region=...)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason


def _get_cluster_id(cluster: Dict[str, Any]) -> Optional[str]:
    return cluster.get("resource_id") or cluster.get("id") or (cluster.get("metadata") or {}).get("Id")


def _get_instance_collection_type(cluster: Dict[str, Any]) -> str:
    md = cluster.get("metadata") or {}
    return (
        md.get("InstanceCollectionType")
        or md.get("instance_collection_type")
        or md.get("instanceCollectionType")
        or cluster.get("InstanceCollectionType")
        or cluster.get("instance_collection_type")
        or ""
    )


def _get_cluster_state(cluster: Dict[str, Any]) -> str:
    md = cluster.get("metadata") or {}
    return (
        md.get("State")
        or md.get("state")
        or cluster.get("State")
        or cluster.get("state")
        or ""
    )


def _try_get_instance_fleets(aws_adapter, cluster_id: str, region: Optional[str]) -> List[Dict[str, Any]]:
    # Resource-type approach
    for rt in ("emr_instance_fleet", "emr_instance_fleets"):
        try:
            fleets = aws_adapter.get_resources(rt, {"cluster_id": cluster_id}, region)
            if isinstance(fleets, list):
                return fleets
        except Exception:
            continue

    # Method approach
    fn = getattr(aws_adapter, "get_emr_instance_fleets", None)
    if callable(fn):
        try:
            try:
                fleets = fn(cluster_id, region=region)
            except TypeError:
                fleets = fn(cluster_id, region)
            if isinstance(fleets, list):
                return fleets
        except Exception:
            return []

    return []


def _fleet_count_from_cluster_metadata(cluster: Dict[str, Any]) -> Optional[int]:
    md = cluster.get("metadata") or {}
    fleets = md.get("instance_fleets") or md.get("InstanceFleets") or md.get("instanceFleets")
    if isinstance(fleets, list):
        return len(fleets)
    return None


def check_emr_no_instance_fleet_enabled(
    aws_adapter,
    include_states: Optional[List[str]] = None,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Flags clusters that are not using instance fleets.

    Args:
        aws_adapter: AWS adapter with credentials
        include_states: cluster states to evaluate (default: RUNNING, WAITING)
        region: optional region filter

    Returns:
        List of clusters flagged as not using instance fleets.
    """
    states = include_states or ["RUNNING", "WAITING"]
    clusters = aws_adapter.get_resources("emr_cluster", {"state": states}, region)

    flagged: List[Dict[str, Any]] = []

    for c in clusters:
        cluster_id = _get_cluster_id(c)
        if not cluster_id:
            continue

        try:
            state = _get_cluster_state(c)
            if state and state.upper() not in {s.upper() for s in states}:
                continue

            ict = _get_instance_collection_type(c).upper().strip()

            # Fast path: explicit type present
            if ict == "INSTANCE_FLEET":
                continue

            # If explicit type missing/unknown, infer from instance fleets list or API lookup
            fleet_count = _fleet_count_from_cluster_metadata(c)
            if fleet_count is None:
                fleets = _try_get_instance_fleets(aws_adapter, cluster_id, region)
                fleet_count = len(fleets)

            uses_fleets = (fleet_count or 0) > 0
            if uses_fleets:
                # Even if InstanceCollectionType missing, fleets exist -> treat as enabled
                continue

            md = c.setdefault("metadata", {})
            md["recommended_action"] = "review"
            md["recommended_actions"] = [
                "migrate_to_instance_fleets",
                "diversify_spot_capacity",
                "improve_cost_resilience",
            ]
            md["cluster_state"] = state or None
            md["instance_collection_type"] = ict or "unknown"
            md["instance_fleet_count"] = int(fleet_count or 0)

            md["check_reason"] = create_check_reason("missing_feature", {
                "resource": "emr_cluster",
                "cluster_id": cluster_id,
                "missing": "instance_fleets",
                "instance_collection_type": ict or "unknown",
                "instance_fleet_count": int(fleet_count or 0),
                "state": state or None,
            })

            flagged.append(c)

        except Exception as e:
            print(f"Error checking EMR cluster {cluster_id} for instance fleets: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="emr_no_instance_fleet_enabled",
    name="EMR No Instance Fleet Enabled",
    description="Identifies EMR clusters that are not using Instance Fleets (likely using Instance Groups)",
    resource_type="emr_cluster",
    check_function=check_emr_no_instance_fleet_enabled,
    default_action="review",
    parameters={
        "include_states": ["RUNNING", "WAITING"],
        "region": None,
    },
))
