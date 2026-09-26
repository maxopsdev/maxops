"""CloudWatch check for alarms without alarm-state actions."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry


def check_cloudwatch_alarms_no_actions(
    aws_adapter,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return metric and composite alarms with no ALARM-state actions."""
    alarms = aws_adapter.get_resources("cloudwatch_alarm", {}, region)
    flagged: List[Dict[str, Any]] = []

    for alarm in alarms:
        metadata = alarm.setdefault("metadata", {})
        alarm_actions = metadata.get("AlarmActions") or []
        if alarm_actions:
            continue

        alarm_name = (
            alarm.get("resource_id")
            or alarm.get("resource_name")
            or alarm.get("alarm_name")
        )
        metadata["alarm_actions"] = []
        metadata["ok_actions"] = list(metadata.get("OKActions") or [])
        metadata["insufficient_data_actions"] = list(
            metadata.get("InsufficientDataActions") or []
        )
        metadata["recommended_action"] = "cloudwatch_delete_alarm"
        metadata["recommended_actions"] = ["cloudwatch_delete_alarm"]
        metadata["check_reason"] = create_check_reason(
            "missing_action",
            {
                "resource": "cloudwatch_alarm",
                "alarm_name": alarm_name,
                "ok_actions": metadata["ok_actions"],
                "insufficient_data_actions": metadata["insufficient_data_actions"],
            },
        )
        flagged.append(alarm)

    return flagged


check_registry.register(CheckMetadata(
    check_id="cloudwatch_alarms_no_actions",
    name="CloudWatch Alarms Without Actions",
    description="Identifies metric and composite alarms with no ALARM-state actions",
    resource_type="cloudwatch_alarm",
    check_function=check_cloudwatch_alarms_no_actions,
    default_action="cloudwatch_delete_alarm",
    parameters={"region": None},
))
