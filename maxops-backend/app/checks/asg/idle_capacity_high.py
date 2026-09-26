"""ASG high idle capacity check."""
from __future__ import annotations

from datetime import datetime, timedelta
from math import ceil
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.asg._common import avg_ec2_cpu_for_instances, asg_instance_ids, list_asgs


def check_asg_idle_capacity_high(
    aws_adapter,
    lookback_days: int = 14,
    target_cpu_utilization: float = 50.0,
    min_idle_instances: int = 1,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    autoscaling = aws_adapter.session.client("autoscaling", region_name=region)
    cloudwatch = aws_adapter.session.client("cloudwatch", region_name=region)
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=lookback_days)

    flagged: List[Dict[str, Any]] = []
    for asg in list_asgs(autoscaling):
        name = asg.get("AutoScalingGroupName")
        if not name:
            continue
        instance_ids = asg_instance_ids(asg)
        if not instance_ids:
            continue
        desired = int(asg.get("DesiredCapacity") or 0)
        if desired <= 0:
            continue
        try:
            avg_cpu = avg_ec2_cpu_for_instances(cloudwatch, instance_ids, start_date, end_date)
            if avg_cpu is None:
                continue
            # Estimate needed capacity at target utilization.
            estimated_needed = max(1, int(ceil((avg_cpu / max(target_cpu_utilization, 1.0)) * desired)))
            idle_instances = max(0, desired - estimated_needed)
            if idle_instances < min_idle_instances:
                continue

            flagged.append(
                {
                    "resource_id": name,
                    "resource_type": "asg",
                    "resource_name": name,
                    "region": region,
                    "state": "active",
                    "metadata": {
                        "lookback_days": lookback_days,
                        "avg_cpu_utilization": round(avg_cpu, 2),
                        "target_cpu_utilization": target_cpu_utilization,
                        "desired_capacity": desired,
                        "estimated_needed_capacity": estimated_needed,
                        "estimated_idle_instances": idle_instances,
                        "recommended_action": "reduce_idle_capacity",
                        "check_reason": create_check_reason(
                            "underutilized",
                            {
                                "avg_cpu": round(avg_cpu, 2),
                                "cpu_threshold": target_cpu_utilization,
                            },
                        ),
                    },
                }
            )
        except Exception as exc:
            print(f"Error checking ASG idle capacity for {name}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="asg_idle_capacity_high",
        name="ASG Idle Capacity High",
        description="Identifies ASGs with excess idle capacity based on CPU-driven estimate",
        resource_type="asg",
        check_function=check_asg_idle_capacity_high,
        default_action="reduce_idle_capacity",
        parameters={
            "lookback_days": 14,
            "target_cpu_utilization": 50.0,
            "min_idle_instances": 1,
            "region": None,
        },
    )
)

