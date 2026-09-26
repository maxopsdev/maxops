"""ASG old instance generation check."""
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


def check_asg_launch_template_old_generation(
    aws_adapter,
    min_generation: int = 6,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    autoscaling = aws_adapter.session.client("autoscaling", region_name=region)
    ec2 = aws_adapter.session.client("ec2", region_name=region)
    flagged: List[Dict[str, Any]] = []

    for asg in list_asgs(autoscaling):
        name = asg.get("AutoScalingGroupName")
        if not name:
            continue
        ids = asg_instance_ids(asg)
        if not ids:
            continue
        try:
            instances = describe_instances(ec2, ids)
            types: Set[str] = set()
            old_types: Set[str] = set()
            for inst in instances:
                itype = inst.get("InstanceType")
                if not itype:
                    continue
                types.add(itype)
                _family, gen, _grav = parse_instance_type_family_generation(itype)
                if gen is not None and gen < min_generation:
                    old_types.add(itype)
            if not old_types:
                continue
            flagged.append(
                {
                    "resource_id": name,
                    "resource_type": "asg",
                    "resource_name": name,
                    "region": region,
                    "state": "active",
                    "metadata": {
                        "instance_types_detected": sorted(types),
                        "old_generation_instance_types": sorted(old_types),
                        "min_generation": min_generation,
                        "recommended_action": "upgrade_instance_generation",
                        "check_reason": create_check_reason(
                            "version_outdated",
                            {
                                "resource": "asg",
                                "group_name": name,
                                "old_instance_types": sorted(old_types),
                                "min_generation": min_generation,
                            },
                        ),
                    },
                }
            )
        except Exception as exc:
            print(f"Error checking ASG generation for {name}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="asg_launch_template_old_generation",
        name="ASG Launch Template Old Generation",
        description="Identifies ASGs running older instance generations",
        resource_type="asg",
        check_function=check_asg_launch_template_old_generation,
        default_action="upgrade_instance_generation",
        parameters={
            "min_generation": 6,
            "region": None,
        },
    )
)

