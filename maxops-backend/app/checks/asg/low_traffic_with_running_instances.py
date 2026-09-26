"""ASG low traffic with running instances check."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.asg._common import (
    avg_ec2_cpu_for_instances,
    asg_instance_ids,
    list_asgs,
    total_ec2_network_bytes_for_instances,
)


def check_asg_low_traffic_with_running_instances(
    aws_adapter,
    lookback_days: int = 14,
    max_avg_cpu: float = 10.0,
    max_network_gb_total: float = 1.0,
    min_running_instances: int = 1,
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
        if len(ids) < min_running_instances:
            continue
        try:
            avg_cpu = avg_ec2_cpu_for_instances(cloudwatch, ids, start_date, end_date)
            if avg_cpu is None:
                continue
            network_bytes_total = total_ec2_network_bytes_for_instances(
                cloudwatch, ids, start_date, end_date
            )
            network_gb_total = network_bytes_total / (1024 * 1024 * 1024)
            if avg_cpu > max_avg_cpu or network_gb_total > max_network_gb_total:
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
                        "running_instances": len(ids),
                        "avg_cpu_utilization": round(avg_cpu, 2),
                        "network_gb_total": round(network_gb_total, 3),
                        "max_avg_cpu": max_avg_cpu,
                        "max_network_gb_total": max_network_gb_total,
                        "recommended_action": "scale_down_or_schedule_off_hours",
                        "check_reason": create_check_reason(
                            "unused",
                            {
                                "reason": "Low CPU and low network traffic while instances keep running"
                            },
                        ),
                    },
                }
            )
        except Exception as exc:
            print(f"Error checking ASG low traffic for {name}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="asg_low_traffic_with_running_instances",
        name="ASG Low Traffic With Running Instances",
        description="Identifies ASGs with low traffic and low CPU despite running instances",
        resource_type="asg",
        check_function=check_asg_low_traffic_with_running_instances,
        default_action="scale_down_or_schedule_off_hours",
        parameters={
            "lookback_days": 14,
            "max_avg_cpu": 10.0,
            "max_network_gb_total": 1.0,
            "min_running_instances": 1,
            "region": None,
        },
    )
)

