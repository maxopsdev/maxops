"""EC2 action handlers."""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import HTTPException
from app.schemas.check import CheckActionResponse
from app.actions.base import ActionExecutionContext
from app.services.cloudwatch_agent_service import (
    CloudWatchAgentError,
    ElevatedRoleRequiredError,
    install_or_configure_cloudwatch_agent,
)
from app.utils.ec2_instance_types import is_graviton_instance_type

logger = logging.getLogger("uvicorn.error")


def _require_target_instance_type(parameters: Dict[str, Any], field_name: str = "target_instance_type") -> str:
    target_instance_type = parameters.get(field_name)
    if not target_instance_type:
        raise HTTPException(
            status_code=400,
            detail=f"Missing {field_name} parameter.",
        )
    return str(target_instance_type)


def _change_instance_type(
    context: ActionExecutionContext,
    target_instance_type: str,
    require_graviton_target: bool,
    success_message: str,
) -> CheckActionResponse:
    ec2 = context.aws_adapter.session.client("ec2", region_name=context.payload.region)
    if require_graviton_target and not is_graviton_instance_type(target_instance_type):
        raise HTTPException(
            status_code=400,
            detail="target_instance_type must be a Graviton instance type.",
        )

    ec2.stop_instances(InstanceIds=[context.payload.resource_id])
    waiter = ec2.get_waiter("instance_stopped")
    waiter.wait(InstanceIds=[context.payload.resource_id])
    ec2.modify_instance_attribute(
        InstanceId=context.payload.resource_id,
        InstanceType={"Value": target_instance_type},
    )
    ec2.start_instances(InstanceIds=[context.payload.resource_id])

    return CheckActionResponse(
        check_id=context.check_id,
        action=context.payload.action,
        status="submitted",
        message=success_message,
        details={
            "instance_id": context.payload.resource_id,
            "target_instance_type": target_instance_type,
        },
    )


def _normalize_schedule_name(name: str) -> str:
    return "".join(char if char.isalnum() or char in "-_." else "-" for char in name)


def _build_scheduler_target(action: str, instance_id: str, role_arn: str) -> Dict[str, Any]:
    if action not in {"startInstances", "stopInstances"}:
        raise ValueError(f"Unsupported scheduler EC2 action: {action}")
    return {
        "Arn": f"arn:aws:scheduler:::aws-sdk:ec2:{action}",
        "RoleArn": role_arn,
        "Input": f'{{"InstanceIds":["{instance_id}"]}}',
    }


def _create_off_hours_schedules(
    scheduler: Any,
    *,
    instance_id: str,
    schedule_group: str,
    timezone: str,
    role_arn: str,
    stop_expression: str,
    start_expression: str,
) -> List[str]:
    stop_name = _normalize_schedule_name(f"maxops-{instance_id}-stop")
    start_name = _normalize_schedule_name(f"maxops-{instance_id}-start")

    scheduler.create_schedule(
        Name=stop_name,
        GroupName=schedule_group,
        ScheduleExpression=stop_expression,
        FlexibleTimeWindow={"Mode": "OFF"},
        ScheduleExpressionTimezone=timezone,
        Target=_build_scheduler_target("stopInstances", instance_id, role_arn),
        Description=f"MaxOps scheduled stop for {instance_id}",
    )
    scheduler.create_schedule(
        Name=start_name,
        GroupName=schedule_group,
        ScheduleExpression=start_expression,
        FlexibleTimeWindow={"Mode": "OFF"},
        ScheduleExpressionTimezone=timezone,
        Target=_build_scheduler_target("startInstances", instance_id, role_arn),
        Description=f"MaxOps scheduled start for {instance_id}",
    )
    return [stop_name, start_name]


def handle_install_cloudwatch_agent(context: ActionExecutionContext):
    """Install or configure CloudWatch agent on EC2 instance."""
    elevated_role_arn = (context.payload.parameters or {}).get("elevated_role_arn")
    try:
        details = install_or_configure_cloudwatch_agent(
            session=context.aws_adapter.session,
            region=context.payload.region,
            instance_id=context.payload.resource_id,
            elevated_role_arn=str(elevated_role_arn) if elevated_role_arn else None,
        )
        installed_during_action = bool(details.get("installed_during_action"))
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message=(
                "CloudWatch agent installed and configured for memory/disk metrics"
                if installed_during_action
                else "CloudWatch agent configuration updated for memory/disk metrics"
            ),
            details=details,
        )
    except ElevatedRoleRequiredError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "details": exc.details,
            },
        )
    except CloudWatchAgentError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to install or configure CloudWatch agent: {str(exc)}"
        )
    except Exception as exc:
        logger.exception(
            "Failed to install or configure CloudWatch agent",
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
            detail=f"Failed to install or configure CloudWatch agent: {str(exc)}"
        )


def handle_ec2_stop(context: ActionExecutionContext):
    """Stop EC2 instance or RDS instance."""
    # Handle RDS stop
    if context.check.resource_type in {"rds", "rds_instance"}:
        try:
            rds = context.aws_adapter.session.client("rds", region_name=context.payload.region)
            response = rds.stop_db_instance(DBInstanceIdentifier=context.payload.resource_id)
            return CheckActionResponse(
                check_id=context.check_id,
                action=context.payload.action,
                status="submitted",
                message="Stop RDS instance request submitted",
                details=response,
            )
        except Exception as exc:
            logger.exception(
                "Failed to execute RDS stop action",
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
                detail=f"Failed to execute RDS stop action: {str(exc)}"
            )
    
    # Handle EC2 stop
    try:
        ec2 = context.aws_adapter.session.client("ec2", region_name=context.payload.region)
        response = ec2.stop_instances(InstanceIds=[context.payload.resource_id])
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="Stop instance request submitted",
            details=response,
        )
    except Exception as exc:
        logger.exception(
            "Failed to execute EC2 stop action",
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
            detail=f"Failed to execute EC2 stop action: {str(exc)}"
        )


def handle_ec2_terminate(context: ActionExecutionContext):
    """Terminate EC2 instance."""
    try:
        ec2 = context.aws_adapter.session.client("ec2", region_name=context.payload.region)
        term = ec2.terminate_instances(InstanceIds=[context.payload.resource_id])
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="Terminate instance request submitted",
            details=term,
        )
    except Exception as exc:
        logger.exception(
            "Failed to execute EC2 terminate action",
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
            detail=f"Failed to execute EC2 action: {str(exc)}"
        )


def handle_ec2_terminate_with_snapshot(context: ActionExecutionContext):
    """Terminate EC2 instance with snapshots of attached volumes."""
    try:
        ec2 = context.aws_adapter.session.client("ec2", region_name=context.payload.region)
        instance = ec2.describe_instances(InstanceIds=[context.payload.resource_id])["Reservations"][0]["Instances"][0]
        block_mappings = instance.get("BlockDeviceMappings", [])
        
        snapshot_ids = []
        for mapping in block_mappings:
            ebs = mapping.get("Ebs")
            if not ebs:
                continue
            volume_id = ebs.get("VolumeId")
            if volume_id:
                snap = ec2.create_snapshot(
                    VolumeId=volume_id,
                    Description=f"Snapshot before terminating {context.payload.resource_id}",
                )
                snapshot_ids.append(snap.get("SnapshotId"))
        
        term = ec2.terminate_instances(InstanceIds=[context.payload.resource_id])
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="Terminate instance with snapshots submitted",
            details={"snapshots": snapshot_ids, "terminate": term},
        )
    except Exception as exc:
        logger.exception(
            "Failed to execute EC2 terminate with snapshot",
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
            detail=f"Failed to execute EC2 action: {str(exc)}"
        )


def handle_ec2_terminate_leave_volume(context: ActionExecutionContext):
    """Terminate EC2 instance but preserve attached volumes."""
    try:
        ec2 = context.aws_adapter.session.client("ec2", region_name=context.payload.region)
        instance = ec2.describe_instances(InstanceIds=[context.payload.resource_id])["Reservations"][0]["Instances"][0]
        block_mappings = instance.get("BlockDeviceMappings", [])
        
        for mapping in block_mappings:
            ebs = mapping.get("Ebs")
            if not ebs:
                continue
            if ebs.get("DeleteOnTermination"):
                ec2.modify_instance_attribute(
                    InstanceId=context.payload.resource_id,
                    BlockDeviceMappings=[
                        {
                            "DeviceName": mapping["DeviceName"],
                            "Ebs": {"DeleteOnTermination": False},
                        }
                    ],
                )
        
        term = ec2.terminate_instances(InstanceIds=[context.payload.resource_id])
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="Terminate instance request submitted (volumes preserved)",
            details=term,
        )
    except Exception as exc:
        logger.exception(
            "Failed to execute EC2 terminate leave volume",
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
            detail=f"Failed to execute EC2 action: {str(exc)}"
        )


def handle_ec2_migrate_to_graviton(context: ActionExecutionContext):
    """Migrate EC2 instance to Graviton-based instance type."""
    try:
        params = context.payload.parameters or {}
        target_instance_type = _require_target_instance_type(params)
        return _change_instance_type(
            context,
            target_instance_type,
            require_graviton_target=True,
            success_message=f"Instance migration to {target_instance_type} submitted",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to migrate EC2 to Graviton",
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
            detail=f"Failed to migrate EC2 to Graviton: {str(exc)}"
        )


def handle_ec2_rightsize(context: ActionExecutionContext) -> CheckActionResponse:
    """Resize an EC2 instance to another instance type."""
    try:
        params = context.payload.parameters or {}
        target_instance_type = _require_target_instance_type(params)
        return _change_instance_type(
            context,
            target_instance_type,
            require_graviton_target=False,
            success_message=f"Instance rightsize to {target_instance_type} submitted",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to rightsize EC2 instance",
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
            detail=f"Failed to rightsize EC2 instance: {str(exc)}",
        )


def handle_ec2_schedule_off_hours(context: ActionExecutionContext) -> CheckActionResponse:
    """Create paired stop/start schedules for an EC2 instance."""
    try:
        params = context.payload.parameters or {}
        role_arn = params.get("scheduler_role_arn")
        stop_expression = params.get("stop_schedule_expression")
        start_expression = params.get("start_schedule_expression")
        if not role_arn or not stop_expression or not start_expression:
            raise HTTPException(
                status_code=400,
                detail=(
                    "scheduler_role_arn, stop_schedule_expression, and "
                    "start_schedule_expression are required."
                ),
            )

        scheduler = context.aws_adapter.session.client(
            "scheduler", region_name=context.payload.region
        )
        schedule_group = str(params.get("schedule_group") or "default")
        timezone = str(params.get("timezone") or "UTC")
        schedule_names = _create_off_hours_schedules(
            scheduler,
            instance_id=context.payload.resource_id,
            schedule_group=schedule_group,
            timezone=timezone,
            role_arn=str(role_arn),
            stop_expression=str(stop_expression),
            start_expression=str(start_expression),
        )

        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="Off-hours schedules created",
            details={
                "instance_id": context.payload.resource_id,
                "schedule_group": schedule_group,
                "timezone": timezone,
                "schedule_names": schedule_names,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to create EC2 off-hours schedules",
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
            detail=f"Failed to create EC2 off-hours schedules: {str(exc)}",
        )


def handle_release_unused_elastic_ip(
    context: ActionExecutionContext,
) -> CheckActionResponse:
    """Release an explicitly confirmed, currently unassociated Elastic IP."""
    try:
        params = context.payload.parameters or {}
        if params.get("confirm_release") is not True:
            raise HTTPException(
                status_code=400,
                detail="confirm_release=true is required.",
            )

        allocation_id = (
            params.get("allocation_id")
            or params.get("allocationId")
            or context.payload.resource_id
        )
        if not allocation_id:
            raise HTTPException(status_code=400, detail="allocation_id is required.")

        ec2 = context.aws_adapter.session.client(
            "ec2", region_name=context.payload.region
        )
        response = ec2.describe_addresses(AllocationIds=[allocation_id])
        addresses = response.get("Addresses", [])
        if not addresses:
            raise HTTPException(status_code=404, detail="Elastic IP not found.")

        address = addresses[0]
        if (
            address.get("AssociationId")
            or address.get("NetworkInterfaceId")
            or address.get("InstanceId")
        ):
            raise HTTPException(
                status_code=409,
                detail="Elastic IP is associated and cannot be released.",
            )

        ec2.release_address(AllocationId=allocation_id)
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="Unused Elastic IP released",
            details={
                "allocation_id": allocation_id,
                "public_ip": address.get("PublicIp"),
                "region": context.payload.region,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to release unused Elastic IP",
            extra={
                "check_id": context.check_id,
                "resource_id": context.payload.resource_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to release unused Elastic IP: {str(exc)}",
        )
