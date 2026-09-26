"""ASG non-Graviton migration candidates check."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.asg._common import (
    asg_instance_ids,
    describe_instances,
    list_asgs,
    parse_instance_type_family_generation,
)


def check_asg_non_graviton_candidates(
    aws_adapter,
    allowed_families: Optional[List[str]] = None,
    min_instances: int = 1,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    autoscaling = aws_adapter.session.client("autoscaling", region_name=region)
    ec2 = aws_adapter.session.client("ec2", region_name=region)
    families = {f.lower() for f in (allowed_families or ["m", "c", "r", "t"])}

    flagged: List[Dict[str, Any]] = []
    for asg in list_asgs(autoscaling):
        name = asg.get("AutoScalingGroupName")
        if not name:
            continue
        ids = asg_instance_ids(asg)
        if len(ids) < min_instances:
            continue
        try:
            instances = describe_instances(ec2, ids)
            types: Set[str] = set()
            any_graviton = False
            family_ok = False
            for inst in instances:
                itype = inst.get("InstanceType")
                if not itype:
                    continue
                types.add(itype)
                family, _gen, graviton = parse_instance_type_family_generation(itype)
                if family in families:
                    family_ok = True
                if graviton:
                    any_graviton = True
            if not types or any_graviton or not family_ok:
                continue

            flagged.append(
                {
                    "resource_id": name,
                    "resource_type": "asg",
                    "resource_name": name,
                    "region": region,
                    "state": "active",
                    "metadata": {
                        "instance_types": sorted(types),
                        "allowed_families": sorted(families),
                        "recommended_action": "migrate_to_graviton_candidates",
                        "check_reason": create_check_reason(
                            "unused",
                            {"reason": "ASG runs non-Graviton types in Graviton-capable families"},
                        ),
                    },
                }
            )
        except Exception as exc:
            print(f"Error checking ASG Graviton candidates for {name}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="asg_non_graviton_candidates",
        name="ASG Non-Graviton Candidates",
        description="Identifies ASGs likely suitable for Graviton migration",
        resource_type="asg",
        check_function=check_asg_non_graviton_candidates,
        default_action="migrate_to_graviton_candidates",
        parameters={
            "allowed_families": ["m", "c", "r", "t"],
            "min_instances": 1,
            "region": None,
        },
    )
)

