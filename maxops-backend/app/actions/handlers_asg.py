"""Auto Scaling group action handlers."""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import HTTPException

from app.actions.base import ActionExecutionContext
from app.schemas.check import CheckActionResponse

logger = logging.getLogger("uvicorn.error")

# (request parameter, UpdateAutoScalingGroup kwarg) pairs, in capacity order.
_TARGET_FIELDS = (
    ("target_min_size", "MinSize"),
    ("target_desired_capacity", "DesiredCapacity"),
    ("target_max_size", "MaxSize"),
)


def _parse_capacity_targets(parameters: Dict[str, Any]) -> Dict[str, int]:
    """Extract the requested capacity values as UpdateAutoScalingGroup kwargs."""
    targets: Dict[str, int] = {}
    for field_name, update_key in _TARGET_FIELDS:
        raw_value = parameters.get(field_name)
        if raw_value is None:
            continue
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, str)):
            raise HTTPException(
                status_code=400,
                detail=f"{field_name} must be a non-negative integer.",
            )
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail=f"{field_name} must be a non-negative integer.",
            )
        if value < 0:
            raise HTTPException(
                status_code=400,
                detail=f"{field_name} must be a non-negative integer.",
            )
        targets[update_key] = value
    if not targets:
        raise HTTPException(
            status_code=400,
            detail=(
                "At least one of target_min_size, target_desired_capacity, "
                "target_max_size is required."
            ),
        )
    return targets


def handle_asg_rightsize(context: ActionExecutionContext) -> CheckActionResponse:
    """Change an Auto Scaling group's min/desired/max capacity.

    Only the provided capacity values are sent to UpdateAutoScalingGroup;
    omitted values keep their current setting. Ordering is validated against
    the effective (requested or current) values so a partial update cannot
    leave the group with min > desired or desired > max.
    """
    try:
        targets = _parse_capacity_targets(context.payload.parameters or {})

        autoscaling = context.aws_adapter.session.client(
            "autoscaling", region_name=context.payload.region
        )
        describe_response = autoscaling.describe_auto_scaling_groups(
            AutoScalingGroupNames=[context.payload.resource_id]
        )
        groups = describe_response.get("AutoScalingGroups", [])
        if not groups:
            raise HTTPException(status_code=404, detail="Auto Scaling group not found.")

        group = groups[0]
        current = {
            "MinSize": group.get("MinSize"),
            "DesiredCapacity": group.get("DesiredCapacity"),
            "MaxSize": group.get("MaxSize"),
        }
        effective = {
            key: targets.get(key, current.get(key))
            for key in ("MinSize", "DesiredCapacity", "MaxSize")
        }
        if effective["MinSize"] > effective["DesiredCapacity"]:
            raise HTTPException(
                status_code=400,
                detail="target_min_size cannot exceed target_desired_capacity.",
            )
        if effective["DesiredCapacity"] > effective["MaxSize"]:
            raise HTTPException(
                status_code=400,
                detail="target_desired_capacity cannot exceed target_max_size.",
            )
        if all(effective[key] == current.get(key) for key in effective):
            raise HTTPException(
                status_code=409,
                detail="Auto Scaling group already matches the requested capacity.",
            )

        update_response = autoscaling.update_auto_scaling_group(
            AutoScalingGroupName=context.payload.resource_id,
            **targets,
        )
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="ASG capacity update submitted",
            details={
                "auto_scaling_group_name": context.payload.resource_id,
                "previous_capacity": {
                    "min_size": current["MinSize"],
                    "desired_capacity": current["DesiredCapacity"],
                    "max_size": current["MaxSize"],
                },
                "target_capacity": {
                    field_name: targets[update_key]
                    for field_name, update_key in _TARGET_FIELDS
                    if update_key in targets
                },
                "response": update_response,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to rightsize Auto Scaling group",
            extra={
                "check_id": context.check_id,
                "action": context.payload.action,
                "account_id": context.payload.account_id,
                "region": context.payload.region,
                "resource_id": context.payload.resource_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to rightsize Auto Scaling group: {str(exc)}",
        )
