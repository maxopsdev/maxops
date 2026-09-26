"""ASG warm pool oversized check."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.asg._common import list_asgs


def check_asg_warm_pool_oversized(
    aws_adapter,
    max_warm_to_inservice_ratio: float = 1.0,
    min_warm_instances: int = 2,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    autoscaling = aws_adapter.session.client("autoscaling", region_name=region)
    flagged: List[Dict[str, Any]] = []

    for asg in list_asgs(autoscaling):
        name = asg.get("AutoScalingGroupName")
        if not name:
            continue
        try:
            warm = autoscaling.describe_warm_pool(
                AutoScalingGroupName=name,
                MaxRecords=100,
            )
            warm_instances = warm.get("Instances", [])
            warm_count = len(warm_instances)
            if warm_count < min_warm_instances:
                continue

            inservice_count = 0
            for inst in asg.get("Instances", []) or []:
                if str(inst.get("LifecycleState") or "").lower() == "inservice":
                    inservice_count += 1
            if inservice_count <= 0:
                inservice_count = int(asg.get("DesiredCapacity") or 0)
            if inservice_count <= 0:
                continue

            ratio = warm_count / max(1, inservice_count)
            if ratio <= max_warm_to_inservice_ratio:
                continue

            flagged.append(
                {
                    "resource_id": name,
                    "resource_type": "asg",
                    "resource_name": name,
                    "region": region,
                    "state": "active",
                    "metadata": {
                        "warm_pool_instances": warm_count,
                        "inservice_instances": inservice_count,
                        "warm_to_inservice_ratio": round(ratio, 3),
                        "max_warm_to_inservice_ratio": max_warm_to_inservice_ratio,
                        "recommended_action": "reduce_warm_pool_size",
                        "check_reason": create_check_reason(
                            "underutilized",
                            {
                                "warm_pool_instances": warm_count,
                                "inservice_instances": inservice_count,
                            },
                        ),
                    },
                }
            )
        except Exception:
            # ASGs without warm pools may throw ValidationError; ignore.
            continue
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="asg_warm_pool_oversized",
        name="ASG Warm Pool Oversized",
        description="Identifies ASGs with oversized warm pools relative to in-service capacity",
        resource_type="asg",
        check_function=check_asg_warm_pool_oversized,
        default_action="reduce_warm_pool_size",
        parameters={
            "max_warm_to_inservice_ratio": 1.0,
            "min_warm_instances": 2,
            "region": None,
        },
    )
)

