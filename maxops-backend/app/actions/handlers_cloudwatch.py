"""CloudWatch action handlers."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.actions.base import ActionExecutionContext
from app.schemas.check import CheckActionResponse

logger = logging.getLogger("uvicorn.error")


def _normalize_alarm_names(value: Any) -> Optional[List[str]]:
    """Normalize alarm names to a list of strings."""
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return None


def handle_cloudwatch_delete_alarm(context: ActionExecutionContext) -> CheckActionResponse:
    """Delete one or more CloudWatch alarms by name."""
    try:
        cw = context.aws_adapter.session.client("cloudwatch", region_name=context.payload.region)

        params = context.payload.parameters or {}
        alarm_names = _normalize_alarm_names(
            params.get("alarm_names")
            or params.get("alarmNames")
            or params.get("alarm_name")
            or params.get("alarmName")
            or context.payload.resource_id
        )

        if not alarm_names:
            raise HTTPException(
                status_code=400,
                detail="alarm_names is required (string or list of strings).",
            )

        # Verify alarms exist
        try:
            response = cw.describe_alarms(AlarmNames=alarm_names)
            existing = response.get("MetricAlarms", []) + response.get("CompositeAlarms", [])
            existing_names = {a.get("AlarmName") for a in existing if a.get("AlarmName")}
            missing = [name for name in alarm_names if name not in existing_names]
            if missing:
                raise HTTPException(
                    status_code=404,
                    detail=f"Alarm(s) not found: {', '.join(missing)}",
                )
        except HTTPException:
            raise
        except Exception as verify_exc:
            raise HTTPException(
                status_code=404,
                detail=f"Failed to verify alarms: {str(verify_exc)}",
            )

        cw.delete_alarms(AlarmNames=alarm_names)

        return CheckActionResponse(
            check_id=context.check_id,
            resource_id=context.payload.resource_id,
            action=context.payload.action,
            status="submitted",
            message="CloudWatch alarm deletion submitted",
            details={
                "alarm_names": alarm_names,
                "region": context.payload.region,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to delete CloudWatch alarms",
            extra={
                "check_id": context.check_id,
                "resource_id": context.payload.resource_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete CloudWatch alarms: {str(exc)}",
        )


def handle_cloudwatch_delete_log_group(context: ActionExecutionContext) -> CheckActionResponse:
    """Delete a CloudWatch Logs log group."""
    try:
        logs = context.aws_adapter.session.client("logs", region_name=context.payload.region)
        log_group_name = context.payload.resource_id

        if not log_group_name:
            raise HTTPException(
                status_code=400,
                detail="log_group_name is required.",
            )

        # Verify log group exists
        try:
            existing = logs.describe_log_groups(
                logGroupNamePrefix=log_group_name
            ).get("logGroups", [])
            if not any(lg.get("logGroupName") == log_group_name for lg in existing):
                raise HTTPException(
                    status_code=404,
                    detail=f"Log group {log_group_name} not found",
                )
        except HTTPException:
            raise
        except Exception as verify_exc:
            raise HTTPException(
                status_code=404,
                detail=f"Failed to verify log group: {str(verify_exc)}",
            )

        logs.delete_log_group(logGroupName=log_group_name)

        return CheckActionResponse(
            check_id=context.check_id,
            resource_id=log_group_name,
            action=context.payload.action,
            status="submitted",
            message="CloudWatch log group deletion submitted",
            details={
                "log_group_name": log_group_name,
                "region": context.payload.region,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to delete CloudWatch log group",
            extra={
                "check_id": context.check_id,
                "resource_id": context.payload.resource_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete CloudWatch log group: {str(exc)}",
        )


def _normalize_int(value: Any) -> Optional[int]:
    """Normalize value to integer."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def handle_cloudwatch_set_log_group_retention(context: ActionExecutionContext) -> CheckActionResponse:
    """Set retention policy for a CloudWatch Logs log group."""
    try:
        logs = context.aws_adapter.session.client("logs", region_name=context.payload.region)

        params = context.payload.parameters or {}
        log_group_name = (
            params.get("log_group_name")
            or params.get("logGroupName")
            or context.payload.resource_id
        )
        retention_days = _normalize_int(
            params.get("retention_days")
            or params.get("retentionDays")
            or params.get("retention_in_days")
            or params.get("retentionInDays")
        )

        if not log_group_name:
            raise HTTPException(
                status_code=400,
                detail="log_group_name is required.",
            )
        if retention_days is None or retention_days <= 0:
            raise HTTPException(
                status_code=400,
                detail="retention_days is required and must be a positive integer.",
            )

        # Verify log group exists
        try:
            existing = logs.describe_log_groups(
                logGroupNamePrefix=log_group_name
            ).get("logGroups", [])
            if not any(lg.get("logGroupName") == log_group_name for lg in existing):
                raise HTTPException(
                    status_code=404,
                    detail=f"Log group {log_group_name} not found",
                )
        except HTTPException:
            raise
        except Exception as verify_exc:
            raise HTTPException(
                status_code=404,
                detail=f"Failed to verify log group: {str(verify_exc)}",
            )

        logs.put_retention_policy(
            logGroupName=log_group_name,
            retentionInDays=retention_days,
        )

        return CheckActionResponse(
            check_id=context.check_id,
            resource_id=log_group_name,
            action=context.payload.action,
            status="submitted",
            message="CloudWatch log group retention update submitted",
            details={
                "log_group_name": log_group_name,
                "region": context.payload.region,
                "retention_days": retention_days,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to set CloudWatch log group retention",
            extra={
                "check_id": context.check_id,
                "resource_id": context.payload.resource_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to set CloudWatch log group retention: {str(exc)}",
        )


def _get_alarm(cw, alarm_name: str) -> Dict[str, Any]:
    """Return one exact metric or composite alarm."""
    response = cw.describe_alarms(AlarmNames=[alarm_name])
    alarms = response.get("MetricAlarms", []) + response.get("CompositeAlarms", [])
    alarm = next((item for item in alarms if item.get("AlarmName") == alarm_name), None)
    if alarm is None:
        raise HTTPException(status_code=404, detail=f"Alarm {alarm_name} not found")
    return alarm


def _action_response(
    context: ActionExecutionContext,
    message: str,
    details: Dict[str, Any],
) -> CheckActionResponse:
    return CheckActionResponse(
        check_id=context.check_id,
        resource_id=context.payload.resource_id,
        action=context.payload.action,
        status="submitted",
        message=message,
        details=details,
    )


def handle_cloudwatch_disable_alarm_actions(
    context: ActionExecutionContext,
) -> CheckActionResponse:
    """Disable actions without deleting or otherwise changing an alarm."""
    try:
        cw = context.aws_adapter.session.client(
            "cloudwatch", region_name=context.payload.region
        )
        params = context.payload.parameters or {}
        alarm_name = (
            params.get("alarm_name")
            or params.get("alarmName")
            or context.payload.resource_id
        )
        if not alarm_name:
            raise HTTPException(status_code=400, detail="alarm_name is required.")

        _get_alarm(cw, alarm_name)
        cw.disable_alarm_actions(AlarmNames=[alarm_name])
        return _action_response(
            context,
            "CloudWatch alarm actions disabled",
            {"alarm_name": alarm_name, "region": context.payload.region},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to disable CloudWatch alarm actions")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to disable CloudWatch alarm actions: {str(exc)}",
        )


_PUT_METRIC_ALARM_FIELDS = {
    "AlarmName",
    "AlarmDescription",
    "ActionsEnabled",
    "OKActions",
    "AlarmActions",
    "InsufficientDataActions",
    "MetricName",
    "Namespace",
    "Statistic",
    "ExtendedStatistic",
    "Dimensions",
    "Period",
    "Unit",
    "EvaluationPeriods",
    "DatapointsToAlarm",
    "Threshold",
    "ComparisonOperator",
    "TreatMissingData",
    "EvaluateLowSampleCountPercentile",
    "Metrics",
    "ThresholdMetricId",
}

_TUNING_FIELDS = {
    "threshold": "Threshold",
    "period": "Period",
    "evaluation_periods": "EvaluationPeriods",
    "datapoints_to_alarm": "DatapointsToAlarm",
    "treat_missing_data": "TreatMissingData",
}


def _normalize_positive_int(value: Any, field_name: str) -> int:
    normalized: Optional[int] = None
    if isinstance(value, int) and not isinstance(value, bool):
        normalized = value
    elif isinstance(value, float) and value.is_integer():
        normalized = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        normalized = int(value.strip())
    if normalized is None or normalized <= 0:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must be a positive integer.",
        )
    return normalized


def handle_cloudwatch_tune_alarm(
    context: ActionExecutionContext,
) -> CheckActionResponse:
    """Update explicitly selected fields on a simple metric alarm."""
    try:
        cw = context.aws_adapter.session.client(
            "cloudwatch", region_name=context.payload.region
        )
        params = context.payload.parameters or {}
        alarm_name = (
            params.get("alarm_name")
            or params.get("alarmName")
            or context.payload.resource_id
        )
        requested = {
            key: params[key]
            for key in _TUNING_FIELDS
            if key in params and params[key] is not None
        }
        if not alarm_name:
            raise HTTPException(status_code=400, detail="alarm_name is required.")
        if not requested:
            raise HTTPException(
                status_code=400,
                detail=(
                    "At least one tuning parameter is required: threshold, period, "
                    "evaluation_periods, datapoints_to_alarm, or treat_missing_data."
                ),
            )

        alarm = _get_alarm(cw, alarm_name)
        if "AlarmRule" in alarm:
            raise HTTPException(
                status_code=400,
                detail="Composite alarms are not supported by cloudwatch_tune_alarm.",
            )
        if alarm.get("Metrics"):
            raise HTTPException(
                status_code=400,
                detail="Metric-math alarms are not supported by cloudwatch_tune_alarm.",
            )

        update_request = {
            key: value
            for key, value in alarm.items()
            if key in _PUT_METRIC_ALARM_FIELDS and value is not None
        }
        updated_fields: Dict[str, Any] = {}
        for parameter_name, value in requested.items():
            aws_name = _TUNING_FIELDS[parameter_name]
            if parameter_name == "threshold":
                if isinstance(value, bool):
                    raise HTTPException(
                        status_code=400, detail="threshold must be numeric."
                    )
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    raise HTTPException(
                        status_code=400, detail="threshold must be numeric."
                    )
            elif parameter_name in {
                "period",
                "evaluation_periods",
                "datapoints_to_alarm",
            }:
                value = _normalize_positive_int(value, parameter_name)
            elif value not in {"breaching", "notBreaching", "ignore", "missing"}:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "treat_missing_data must be one of breaching, "
                        "notBreaching, ignore, or missing."
                    ),
                )
            update_request[aws_name] = value
            updated_fields[parameter_name] = value

        evaluation_periods = update_request.get("EvaluationPeriods")
        datapoints_to_alarm = update_request.get("DatapointsToAlarm")
        if (
            evaluation_periods is not None
            and datapoints_to_alarm is not None
            and datapoints_to_alarm > evaluation_periods
        ):
            raise HTTPException(
                status_code=400,
                detail="datapoints_to_alarm cannot exceed evaluation_periods.",
            )

        cw.put_metric_alarm(**update_request)
        return _action_response(
            context,
            "CloudWatch alarm tuning submitted",
            {
                "alarm_name": alarm_name,
                "region": context.payload.region,
                "updated_fields": updated_fields,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to tune CloudWatch alarm")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to tune CloudWatch alarm: {str(exc)}",
        )


def handle_cloudwatch_consolidate_duplicate_alarms(
    context: ActionExecutionContext,
) -> CheckActionResponse:
    """Delete explicitly selected alarms after confirming they match the keeper."""
    try:
        from app.checks.cloudwatch.alarms_with_duplicates import alarm_fingerprint

        cw = context.aws_adapter.session.client(
            "cloudwatch", region_name=context.payload.region
        )
        params = context.payload.parameters or {}
        keeper = (
            params.get("keeper_alarm_name")
            or params.get("keeperAlarmName")
            or context.payload.resource_id
        )
        duplicates = _normalize_alarm_names(
            params.get("duplicate_alarm_names")
            or params.get("duplicateAlarmNames")
            or params.get("delete_alarm_names")
            or params.get("deleteAlarmNames")
        )
        if not keeper:
            raise HTTPException(
                status_code=400, detail="keeper_alarm_name is required."
            )
        if not duplicates:
            raise HTTPException(
                status_code=400,
                detail="duplicate_alarm_names is required.",
            )

        duplicates = list(dict.fromkeys(duplicates))
        if keeper in duplicates:
            raise HTTPException(
                status_code=400,
                detail="The keeper alarm cannot be included in duplicate_alarm_names.",
            )

        requested_names = [keeper, *duplicates]
        response = cw.describe_alarms(AlarmNames=requested_names)
        metric_alarms = {
            alarm.get("AlarmName"): alarm
            for alarm in response.get("MetricAlarms", [])
            if alarm.get("AlarmName")
        }
        composite_names = {
            alarm.get("AlarmName")
            for alarm in response.get("CompositeAlarms", [])
            if alarm.get("AlarmName")
        }
        missing = [
            name
            for name in requested_names
            if name not in metric_alarms and name not in composite_names
        ]
        if missing:
            raise HTTPException(
                status_code=404,
                detail=f"Alarm(s) not found: {', '.join(missing)}",
            )
        if composite_names:
            raise HTTPException(
                status_code=400,
                detail="Composite alarms cannot be consolidated as metric duplicates.",
            )

        keeper_fingerprint = alarm_fingerprint(metric_alarms[keeper])
        if keeper_fingerprint is None:
            raise HTTPException(
                status_code=400,
                detail="The keeper alarm does not have a safe duplicate fingerprint.",
            )
        mismatched = [
            name
            for name in duplicates
            if alarm_fingerprint(metric_alarms[name]) != keeper_fingerprint
        ]
        if mismatched:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Selected alarms do not match the keeper fingerprint: "
                    + ", ".join(mismatched)
                ),
            )

        cw.delete_alarms(AlarmNames=duplicates)
        return _action_response(
            context,
            "Duplicate CloudWatch alarms deleted",
            {
                "keeper_alarm_name": keeper,
                "deleted_alarm_names": duplicates,
                "region": context.payload.region,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to consolidate duplicate CloudWatch alarms")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to consolidate duplicate CloudWatch alarms: {str(exc)}",
        )
