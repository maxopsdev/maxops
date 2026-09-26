"""ASG on-demand heavy mix check."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.asg._common import asg_instance_ids, describe_instances, list_asgs, split_instance_lifecycle


def check_asg_on_demand_heavy_mix(
    aws_adapter,
    min_on_demand_ratio: float = 0.8,
    min_instances: int = 2,
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
        if len(ids) < min_instances:
            continue
        try:
            instances = describe_instances(ec2, ids)
            on_demand, spot = split_instance_lifecycle(instances)
            total = on_demand + spot
            if total < min_instances:
                continue
            ratio = on_demand / max(1, total)
            if ratio < min_on_demand_ratio:
                continue

            flagged.append(
                {
                    "resource_id": name,
                    "resource_type": "asg",
                    "resource_name": name,
                    "region": region,
                    "state": "active",
                    "metadata": {
                        "on_demand_instances": on_demand,
                        "spot_instances": spot,
                        "on_demand_ratio": round(ratio, 3),
                        "min_on_demand_ratio": min_on_demand_ratio,
                        "recommended_action": "increase_spot_mix_safely",
                        "check_reason": create_check_reason(
                            "underutilized",
                            {"on_demand_ratio": round(ratio, 3), "threshold": min_on_demand_ratio},
                        ),
                    },
                }
            )
        except Exception as exc:
            print(f"Error checking ASG on-demand mix for {name}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="asg_on_demand_heavy_mix",
        name="ASG On-Demand Heavy Mix",
        description="Identifies ASGs with high On-Demand ratio where Spot mix may reduce cost",
        resource_type="asg",
        check_function=check_asg_on_demand_heavy_mix,
        default_action="increase_spot_mix_safely",
        parameters={
            "min_on_demand_ratio": 0.8,
            "min_instances": 2,
            "region": None,
        },
    )
)

