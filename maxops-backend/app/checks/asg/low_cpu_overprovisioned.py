"""ASG low CPU overprovisioned check."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.asg._common import avg_ec2_cpu_for_instances, asg_instance_ids, list_asgs


def check_asg_low_cpu_overprovisioned(
    aws_adapter,
    lookback_days: int = 14,
    cpu_threshold: float = 20.0,
    min_extra_capacity: int = 1,
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
        min_size = int(asg.get("MinSize") or 0)
        desired = int(asg.get("DesiredCapacity") or 0)
        extra = max(0, desired - min_size)
        if extra < min_extra_capacity:
            continue
        try:
            avg_cpu = avg_ec2_cpu_for_instances(cloudwatch, instance_ids, start_date, end_date)
            if avg_cpu is None or avg_cpu > cpu_threshold:
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
                        "cpu_threshold": cpu_threshold,
                        "min_size": min_size,
                        "desired_capacity": desired,
                        "extra_capacity_above_min": extra,
                        "recommended_action": "reduce_desired_or_instance_size",
                        "check_reason": create_check_reason(
                            "underutilized",
                            {"avg_cpu": round(avg_cpu, 2), "cpu_threshold": cpu_threshold},
                        ),
                    },
                }
            )
        except Exception as exc:
            print(f"Error checking ASG CPU overprovisioning for {name}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="asg_low_cpu_overprovisioned",
        name="ASG Low CPU Overprovisioned",
        description="Identifies ASGs with low CPU utilization and extra capacity above minimum",
        resource_type="asg",
        check_function=check_asg_low_cpu_overprovisioned,
        default_action="reduce_desired_or_instance_size",
        parameters={
            "lookback_days": 14,
            "cpu_threshold": 20.0,
            "min_extra_capacity": 1,
            "region": None,
        },
    )
)

