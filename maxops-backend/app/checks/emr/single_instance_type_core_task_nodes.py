"""
EMR Check - Single Instance Type in Instance Fleet

Flags EMR instance fleets (CORE or TASK) that specify only one instance type.

Why it matters:
- Fleets with a single instance type are less resilient to capacity/Spot interruptions
  and often cost more than a diversified fleet.

Assumptions:
- Your adapter exposes instance fleets via resource type "emr_instance_fleet"
  with metadata including:
    - InstanceFleetType (CORE/TASK)
    - InstanceTypeSpecifications / InstanceTypeConfigs (list)
- If not, implement a fleet fetch method in the adapter.

This check returns fleet resources (resource_type="emr_instance_fleet") so remediation
can be specific to that fleet.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason


def _get_fleet_id(f: Dict[str, Any]) -> Optional[str]:
    return f.get("resource_id") or f.get("id") or (f.get("metadata") or {}).get("Id")


def _get_cluster_id(f: Dict[str, Any]) -> Optional[str]:
    md = f.get("metadata") or {}
    return f.get("cluster_id") or md.get("cluster_id") or md.get("ClusterId") or f.get("ClusterId")


def _fleet_type(f: Dict[str, Any]) -> str:
    md = f.get("metadata") or {}
    return (f.get("InstanceFleetType") or f.get("instance_fleet_type") or md.get("InstanceFleetType") or md.get("instance_fleet_type") or "").upper()


def _instance_type_configs(f: Dict[str, Any]) -> List[Dict[str, Any]]:
    md = f.get("metadata") or {}
    # Common keys across APIs/adapters
    for k in ("InstanceTypeSpecifications", "InstanceTypeConfigs", "instance_type_configs", "instanceTypeConfigs"):
        v = f.get(k) or md.get(k)
        if isinstance(v, list):
            return [i for i in v if isinstance(i, dict)]
    return []


def check_emr_single_instance_type_in_fleet(
    aws_adapter,
    include_fleet_types: Optional[List[str]] = None,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Flags fleets with exactly one instance type config.

    Returns:
        List of fleet resources flagged.
    """
    fleet_types = [t.upper() for t in (include_fleet_types or ["CORE", "TASK"])]

    fleets = aws_adapter.get_resources("emr_instance_fleet", {}, region)

    flagged: List[Dict[str, Any]] = []

    for f in fleets:
        fleet_id = _get_fleet_id(f)
        if not fleet_id:
            continue

        try:
            ft = _fleet_type(f)
            if ft and ft not in set(fleet_types):
                continue

            configs = _instance_type_configs(f)
            if len(configs) == 1:
                # Try to extract instance type for convenience
                it = configs[0].get("InstanceType") or configs[0].get("instanceType")

                md = f.setdefault("metadata", {})
                md["recommended_action"] = "review"
                md["recommended_actions"] = [
                    "add_more_instance_types",
                    "diversify_for_spot",
                    "improve_capacity_resilience",
                ]
                md["fleet_type"] = ft or None
                md["instance_type_count"] = 1
                md["single_instance_type"] = it
                md["cluster_id"] = _get_cluster_id(f)

                md["check_reason"] = create_check_reason("configuration_risk", {
                    "resource": "emr_instance_fleet",
                    "fleet_id": fleet_id,
                    "cluster_id": _get_cluster_id(f),
                    "fleet_type": ft or None,
                    "instance_type_count": 1,
                    "single_instance_type": it,
                })

                flagged.append(f)

        except Exception as e:
            print(f"Error checking EMR instance fleet {fleet_id} for diversification: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="emr_single_instance_type_in_fleet",
    name="EMR Single Instance Type In Fleet",
    description="Identifies EMR instance fleets (CORE/TASK) configured with only one instance type",
    resource_type="emr_instance_fleet",
    check_function=check_emr_single_instance_type_in_fleet,
    default_action="review",
    parameters={
        "include_fleet_types": ["CORE", "TASK"],
        "region": None,
    },
))
