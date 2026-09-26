"""
EMR Check - Not Using Spot in Task Capacity

Flags EMR clusters where task capacity is On-Demand only (no Spot configured for task fleets/groups).

Assumptions:
- Your adapter exposes either:
  - emr_instance_fleet resources with metadata including InstanceFleetType (TASK) and TargetSpotCapacity,
    OR
  - emr_instance_group resources with InstanceGroupType (TASK) and Market (ON_DEMAND/SPOT),
    OR
  - cluster metadata containing instance_fleets / instance_groups info.

This check tries both fleets and groups, best-effort.

Default action: "review" (enable Spot where appropriate).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason


def _get_cluster_id(c: Dict[str, Any]) -> Optional[str]:
    return c.get("resource_id") or c.get("id") or (c.get("metadata") or {}).get("Id")


def _get_state(c: Dict[str, Any]) -> str:
    md = c.get("metadata") or {}
    return (md.get("State") or md.get("state") or c.get("State") or c.get("state") or "").strip().upper()


def _get_instance_fleets(aws_adapter, cluster_id: str, region: Optional[str]) -> List[Dict[str, Any]]:
    for rt in ("emr_instance_fleet", "emr_instance_fleets"):
        try:
            fleets = aws_adapter.get_resources(rt, {"cluster_id": cluster_id}, region)
            if isinstance(fleets, list):
                return fleets
        except Exception:
            continue
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


def _get_instance_groups(aws_adapter, cluster_id: str, region: Optional[str]) -> List[Dict[str, Any]]:
    for rt in ("emr_instance_group", "emr_instance_groups"):
        try:
            groups = aws_adapter.get_resources(rt, {"cluster_id": cluster_id}, region)
            if isinstance(groups, list):
                return groups
        except Exception:
            continue
    fn = getattr(aws_adapter, "get_emr_instance_groups", None)
    if callable(fn):
        try:
            try:
                groups = fn(cluster_id, region=region)
            except TypeError:
                groups = fn(cluster_id, region)
            if isinstance(groups, list):
                return groups
        except Exception:
            return []
    return []


def _fleet_is_task(f: Dict[str, Any]) -> bool:
    md = f.get("metadata") or {}
    t = (f.get("InstanceFleetType") or f.get("instance_fleet_type") or md.get("InstanceFleetType") or md.get("instance_fleet_type") or "").upper()
    return t == "TASK"


def _group_is_task(g: Dict[str, Any]) -> bool:
    md = g.get("metadata") or {}
    t = (g.get("InstanceGroupType") or g.get("instance_group_type") or md.get("InstanceGroupType") or md.get("instance_group_type") or "").upper()
    return t == "TASK"


def _fleet_spot_capacity(f: Dict[str, Any]) -> int:
    md = f.get("metadata") or {}
    for k in ("TargetSpotCapacity", "target_spot_capacity", "targetSpotCapacity"):
        v = f.get(k) or md.get(k)
        if v is None:
            continue
        try:
            return int(v)
        except Exception:
            continue
    return 0


def _group_market(g: Dict[str, Any]) -> str:
    md = g.get("metadata") or {}
    return (g.get("Market") or g.get("market") or md.get("Market") or md.get("market") or "").upper()


def check_emr_not_using_spot_in_task(
    aws_adapter,
    include_states: Optional[List[str]] = None,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Flags clusters that have TASK capacity but no Spot configured for TASK.
    - For fleets: TASK fleet TargetSpotCapacity == 0
    - For groups: TASK group Market == ON_DEMAND
    """
    states = include_states or ["RUNNING", "WAITING"]
    clusters = aws_adapter.get_resources("emr_cluster", {"state": states}, region)

    flagged: List[Dict[str, Any]] = []

    for c in clusters:
        cluster_id = _get_cluster_id(c)
        if not cluster_id:
            continue

        try:
            state = _get_state(c)
            if state and state.upper() not in {s.upper() for s in states}:
                continue

            fleets = _get_instance_fleets(aws_adapter, cluster_id, region)
            task_fleets = [f for f in fleets if _fleet_is_task(f)]

            groups = _get_instance_groups(aws_adapter, cluster_id, region)
            task_groups = [g for g in groups if _group_is_task(g)]

            # If there is no task capacity at all, skip.
            if not task_fleets and not task_groups:
                continue

            # Determine if any task capacity uses Spot.
            has_spot_task = False

            # Fleets: spot if target spot capacity > 0
            for f in task_fleets:
                if _fleet_spot_capacity(f) > 0:
                    has_spot_task = True
                    break

            # Groups: spot if market == SPOT
            if not has_spot_task:
                for g in task_groups:
                    if _group_market(g) == "SPOT":
                        has_spot_task = True
                        break

            if not has_spot_task:
                md = c.setdefault("metadata", {})
                md["recommended_action"] = "review"
                md["recommended_actions"] = [
                    "enable_spot_for_task_capacity",
                    "diversify_instance_types",
                    "set_on_demand_baseline",
                ]
                md["task_fleet_count"] = len(task_fleets)
                md["task_group_count"] = len(task_groups)
                md["cluster_state"] = state or None
                md["check_reason"] = create_check_reason("purchase_strategy", {
                    "resource": "emr_cluster",
                    "cluster_id": cluster_id,
                    "issue": "task_capacity_not_using_spot",
                    "task_fleet_count": len(task_fleets),
                    "task_group_count": len(task_groups),
                    "state": state or None,
                })
                flagged.append(c)

        except Exception as e:
            print(f"Error checking EMR Spot usage for {cluster_id}: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="emr_not_using_spot_in_task",
    name="EMR Not Using Spot In Task Capacity",
    description="Identifies EMR clusters with task capacity that is On-Demand only (no Spot configured for TASK)",
    resource_type="emr_cluster",
    check_function=check_emr_not_using_spot_in_task,
    default_action="review",
    parameters={
        "include_states": ["RUNNING", "WAITING"],
        "region": None,
    },
))
