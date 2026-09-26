"""CloudWatch Logs check for log groups without a retention policy."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry


def check_cloudwatch_log_groups_no_expiration(
    aws_adapter,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return log groups that retain data indefinitely."""
    log_groups = aws_adapter.get_resources("cloudwatch_log_group", {}, region)
    flagged: List[Dict[str, Any]] = []

    for log_group in log_groups:
        metadata = log_group.setdefault("metadata", {})
        retention_days = metadata.get("retentionInDays")
        if retention_days is not None:
            continue

        log_group_name = (
            log_group.get("resource_id")
            or log_group.get("resource_name")
            or log_group.get("log_group_name")
        )
        metadata["current_retention_days"] = None
        metadata["stored_bytes"] = metadata.get("storedBytes", 0)
        metadata["recommended_action"] = "cloudwatch_set_log_group_retention"
        metadata["recommended_actions"] = ["cloudwatch_set_log_group_retention"]
        metadata["check_reason"] = create_check_reason(
            "missing_retention",
            {
                "resource": "cloudwatch_log_group",
                "log_group_name": log_group_name,
                "stored_bytes": metadata["stored_bytes"],
            },
        )
        flagged.append(log_group)

    return flagged


check_registry.register(CheckMetadata(
    check_id="cloudwatch_log_groups_no_expiration",
    name="CloudWatch Log Groups Without Expiration",
    description="Identifies CloudWatch log groups without a retention policy",
    resource_type="cloudwatch_log_group",
    check_function=check_cloudwatch_log_groups_no_expiration,
    default_action="cloudwatch_set_log_group_retention",
    parameters={"region": None},
))
