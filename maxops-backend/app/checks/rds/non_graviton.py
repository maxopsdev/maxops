"""
RDS Check - Non-Graviton Instance Class

Flags RDS DB instances that are NOT using Graviton-based instance classes
(e.g., db.m6g.large, db.r6g.xlarge, db.t4g.micro, db.m7g.2xlarge).

Heuristic:
- Parse DBInstanceClass like "db.m6g.large" -> family = "m6g"
- If the family segment contains a "g" family marker (e.g., m6g, r6gd, t4g, x2gd),
  treat it as Graviton.
- Skip instances where DBInstanceClass is missing (avoid false positives).

Assumptions:
- aws_adapter.get_resources("rds_instance", {}, region) returns DB instance resources with:
    - resource_id or id
    - metadata.DBInstanceClass (or db_instance_class)
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason, estimate_rds_monthly_cost


_GRAVITON_FAMILY_RE = re.compile(r"^[a-z0-9]+g[a-z0-9]*$")  # m6g, r6gd, t4g, x2gd, m7g, etc.


def _md(r: Dict[str, Any]) -> Dict[str, Any]:
    return r.get("metadata") or {}


def _get_instance_id(r: Dict[str, Any]) -> Optional[str]:
    return r.get("resource_id") or r.get("id") or _md(r).get("DBInstanceIdentifier") or _md(r).get("db_instance_identifier")


def _db_instance_class(r: Dict[str, Any]) -> Optional[str]:
    md = _md(r)
    v = (
        md.get("DBInstanceClass")
        or md.get("db_instance_class")
        or md.get("dbInstanceClass")
        or r.get("DBInstanceClass")
        or r.get("db_instance_class")
    )
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _is_graviton_instance_class(instance_class: str) -> bool:
    """
    Returns True if instance_class looks like a Graviton-based RDS class.

    Examples:
      - db.m6g.large -> True
      - db.r6gd.xlarge -> True
      - db.t4g.micro -> True
      - db.m5.large -> False
    """
    parts = instance_class.strip().lower().split(".")
    # Expected: ["db", "<family>", "<size>"] (sometimes size has multiple segments, but family is parts[1])
    if len(parts) < 3:
        return False
    family = parts[1].strip()
    return bool(_GRAVITON_FAMILY_RE.match(family))


def _get_graviton_equivalent(instance_class: str) -> Optional[str]:
    """
    Convert a non-Graviton instance class to its Graviton equivalent.
    
    Examples:
      - db.m5.large -> db.m6g.large
      - db.t3.medium -> db.t4g.medium
      - db.r5.xlarge -> db.r6g.xlarge
      - db.m6g.large -> None (already Graviton)
    
    Returns:
        Graviton equivalent instance class, or None if conversion not possible
    """
    parts = instance_class.strip().lower().split(".")
    if len(parts) < 3:
        return None
    
    family = parts[1].strip()
    size = ".".join(parts[2:])  # Handle multi-segment sizes like "2xlarge"
    
    # Mapping: non-Graviton family -> Graviton family
    family_mapping = {
        'm5': 'm6g',
        'm4': 'm6g',  # m4 -> m6g (closest equivalent)
        't3': 't4g',
        't2': 't4g',  # t2 -> t4g (closest equivalent)
        'r5': 'r6g',
        'r4': 'r6g',  # r4 -> r6g (closest equivalent)
        'x1': 'x2gd',  # x1 -> x2gd
    }
    
    graviton_family = family_mapping.get(family)
    if not graviton_family:
        return None
    
    return f"db.{graviton_family}.{size}"


def check_rds_non_graviton_instance_class(
    aws_adapter,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Identify RDS instances not using Graviton instance classes.
    """
    instances = aws_adapter.get_resources("rds_instance", {}, region)

    flagged: List[Dict[str, Any]] = []
    for inst in instances:
        inst_id = _get_instance_id(inst)
        if not inst_id:
            continue

        try:
            cls = _db_instance_class(inst)
            if not cls:
                # Unknown / missing class -> skip to avoid false positives
                continue

            if not _is_graviton_instance_class(cls):
                md = inst.setdefault("metadata", {})
                md["recommended_action"] = "rds_migrate_graviton"
                md["recommended_actions"] = [
                    "rds_migrate_graviton",
                ]
                md["db_instance_class"] = cls
                
                # Calculate potential savings from migrating to Graviton
                current_monthly_cost = estimate_rds_monthly_cost(cls)
                graviton_equivalent = _get_graviton_equivalent(cls)
                
                if graviton_equivalent:
                    graviton_monthly_cost = estimate_rds_monthly_cost(graviton_equivalent)
                    # Graviton instances are typically 10-20% cheaper
                    # If pricing not available, estimate 15% savings
                    if graviton_monthly_cost < current_monthly_cost:
                        potential_savings = current_monthly_cost - graviton_monthly_cost
                    else:
                        # Fallback: estimate 15% savings if pricing not in dictionary
                        potential_savings = current_monthly_cost * 0.15
                    
                    md["potential_savings_yearly"] = round(potential_savings * 12, 2)
                    md["recommended_instance_class"] = graviton_equivalent
                    md["current_monthly_cost"] = round(current_monthly_cost, 2)
                    md["graviton_monthly_cost"] = round(graviton_monthly_cost, 2)
                else:
                    # If we can't determine Graviton equivalent, estimate 15% savings
                    potential_savings = current_monthly_cost * 0.15
                    md["potential_savings_yearly"] = round(potential_savings * 12, 2)
                    md["current_monthly_cost"] = round(current_monthly_cost, 2)
                
                md["check_reason"] = create_check_reason("cost_efficiency", {
                    "resource": "rds_instance",
                    "db_instance_id": inst_id,
                    "issue": "non_graviton_instance_class",
                    "db_instance_class": cls,
                })
                flagged.append(inst)

        except Exception as e:
            print(f"Error checking RDS instance class for {inst_id}: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="rds_non_graviton_instance_class",
    name="RDS Non-Graviton Instance Class",
    description="Identifies RDS DB instances that are not using Graviton-based instance classes",
    resource_type="rds_instance",
    check_function=check_rds_non_graviton_instance_class,
    default_action="rds_migrate_graviton",
    parameters={
        "region": None,
    },
))
