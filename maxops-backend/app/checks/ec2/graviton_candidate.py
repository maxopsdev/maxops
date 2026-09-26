"""EC2 non-Graviton migration candidates."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason, estimate_ec2_monthly_cost
from app.utils.ec2_instance_types import (
    is_graviton_instance_type,
    recommended_graviton_instance_type,
    split_instance_type,
)
from app.checks.registry import CheckMetadata, check_registry


def check_ec2_graviton_candidate(
    aws_adapter,
    allowed_families: Optional[List[str]] = None,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Identify running EC2 instances that have a known Graviton equivalent."""
    families = {family.lower() for family in (allowed_families or ["m", "c", "r", "t"])}
    instances = aws_adapter.get_resources("ec2", {"state": "running"}, region)
    flagged: List[Dict[str, Any]] = []

    for instance in instances:
        metadata = instance.setdefault("metadata", {})
        instance_type = metadata.get("instance_type") or instance.get("instance_type", "")
        metadata.setdefault("instance_type", instance_type)
        if not instance_type or is_graviton_instance_type(instance_type):
            continue

        family, _ = split_instance_type(instance_type)
        family_prefix = family[:1].lower()
        if family_prefix not in families:
            continue

        target_instance_type = recommended_graviton_instance_type(instance_type)
        if not target_instance_type:
            continue

        monthly_cost = estimate_ec2_monthly_cost(instance_type)
        metadata["recommended_action"] = "migrate_to_graviton"
        metadata["recommended_actions"] = ["migrate_to_graviton"]
        metadata["recommended_instance_type"] = target_instance_type
        metadata["allowed_family_prefixes"] = sorted(families)
        metadata["potential_savings_yearly"] = round(monthly_cost * 0.16 * 12, 2)
        metadata["check_reason"] = create_check_reason(
            "cost_efficiency",
            {
                "resource": "ec2",
                "instance_type": instance_type,
                "issue": "non_graviton_instance_family",
            },
        )
        flagged.append(instance)

    return flagged


check_registry.register(
    CheckMetadata(
        check_id="ec2_graviton_candidate",
        name="EC2 Graviton Migration Candidates",
        description="Identifies running EC2 instances with a known Graviton equivalent",
        resource_type="ec2",
        check_function=check_ec2_graviton_candidate,
        default_action="migrate_to_graviton",
        parameters={
            "allowed_families": ["m", "c", "r", "t"],
            "region": None,
        },
    )
)
