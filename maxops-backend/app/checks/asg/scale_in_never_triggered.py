"""ASG scale-in never triggered check."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.asg._common import list_asgs


def check_asg_scale_in_never_triggered(
    aws_adapter,
    min_scale_out_activities: int = 3,
    max_activity_records: int = 100,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    autoscaling = aws_adapter.session.client("autoscaling", region_name=region)
    flagged: List[Dict[str, Any]] = []

    for asg in list_asgs(autoscaling):
        name = asg.get("AutoScalingGroupName")
        if not name:
            continue
        try:
            activities = autoscaling.describe_scaling_activities(
                AutoScalingGroupName=name,
                MaxRecords=max_activity_records,
            ).get("Activities", [])
            scale_out = 0
            scale_in = 0
            for act in activities:
                desc = str(act.get("Description") or "").lower()
                cause = str(act.get("Cause") or "").lower()
                text = f"{desc} {cause}"
                if "launch" in text or "adding" in text or "increase" in text:
                    scale_out += 1
                if "terminat" in text or "remove" in text or "decrease" in text:
                    scale_in += 1

            if scale_out >= min_scale_out_activities and scale_in == 0:
                flagged.append(
                    {
                        "resource_id": name,
                        "resource_type": "asg",
                        "resource_name": name,
                        "region": region,
                        "state": "active",
                        "metadata": {
                            "scale_out_activities": scale_out,
                            "scale_in_activities": scale_in,
                            "min_scale_out_activities": min_scale_out_activities,
                            "recommended_action": "tune_scale_in_policies",
                            "check_reason": create_check_reason(
                                "unused",
                                {"reason": "Scale out events observed but no scale in events"},
                            ),
                        },
                    }
                )
        except Exception as exc:
            print(f"Error checking ASG scaling activities for {name}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="asg_scale_in_never_triggered",
        name="ASG Scale-In Never Triggered",
        description="Identifies ASGs with repeated scale-out but no scale-in activities",
        resource_type="asg",
        check_function=check_asg_scale_in_never_triggered,
        default_action="tune_scale_in_policies",
        parameters={
            "min_scale_out_activities": 3,
            "max_activity_records": 100,
            "region": None,
        },
    )
)

