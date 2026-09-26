"""ASG scheduled scaling mismatch check."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.asg._common import avg_ec2_cpu_for_instances, asg_instance_ids, list_asgs


def check_asg_scheduled_scaling_mismatch(
    aws_adapter,
    lookback_days: int = 28,
    max_cpu_during_schedule: float = 20.0,
    min_scheduled_desired_capacity: int = 2,
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
        ids = asg_instance_ids(asg)
        if not ids:
            continue
        try:
            scheduled = autoscaling.describe_scheduled_actions(
                AutoScalingGroupName=name,
                MaxRecords=100,
            ).get("ScheduledUpdateGroupActions", [])
            if not scheduled:
                continue

            high_schedule_count = 0
            max_scheduled_desired = 0
            for action in scheduled:
                desired = action.get("DesiredCapacity")
                if desired is None:
                    continue
                desired_i = int(desired)
                max_scheduled_desired = max(max_scheduled_desired, desired_i)
                if desired_i >= min_scheduled_desired_capacity:
                    high_schedule_count += 1
            if high_schedule_count == 0:
                continue

            avg_cpu = avg_ec2_cpu_for_instances(cloudwatch, ids, start_date, end_date)
            if avg_cpu is None or avg_cpu > max_cpu_during_schedule:
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
                        "scheduled_actions_count": len(scheduled),
                        "high_desired_schedules_count": high_schedule_count,
                        "max_scheduled_desired_capacity": max_scheduled_desired,
                        "avg_cpu_utilization": round(avg_cpu, 2),
                        "max_cpu_during_schedule": max_cpu_during_schedule,
                        "recommended_action": "adjust_scheduled_scaling",
                        "check_reason": create_check_reason(
                            "unused",
                            {
                                "reason": (
                                    "Scheduled high desired capacity with low observed utilization"
                                )
                            },
                        ),
                    },
                }
            )
        except Exception as exc:
            print(f"Error checking ASG scheduled scaling for {name}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="asg_scheduled_scaling_mismatch",
        name="ASG Scheduled Scaling Mismatch",
        description="Identifies ASGs with high scheduled desired capacity but low utilization",
        resource_type="asg",
        check_function=check_asg_scheduled_scaling_mismatch,
        default_action="adjust_scheduled_scaling",
        parameters={
            "lookback_days": 28,
            "max_cpu_during_schedule": 20.0,
            "min_scheduled_desired_capacity": 2,
            "region": None,
        },
    )
)

