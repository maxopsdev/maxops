"""DynamoDB action handlers."""
from __future__ import annotations

import logging
from fastapi import HTTPException
from app.schemas.check import CheckActionResponse
from app.actions.base import ActionExecutionContext

logger = logging.getLogger("uvicorn.error")


def handle_use_provisioned_capacity(context: ActionExecutionContext) -> CheckActionResponse:
    """Switch DynamoDB table to provisioned capacity mode."""
    try:
        params = context.payload.parameters or {}
        read_capacity = params.get("read_capacity_units")
        write_capacity = params.get("write_capacity_units")
        if not read_capacity or not write_capacity:
            raise HTTPException(
                status_code=400,
                detail="Missing read_capacity_units or write_capacity_units.",
            )
        try:
            read_capacity = int(read_capacity)
            write_capacity = int(write_capacity)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail="read_capacity_units and write_capacity_units must be integers.",
            )
        if read_capacity <= 0 or write_capacity <= 0:
            raise HTTPException(
                status_code=400,
                detail="read_capacity_units and write_capacity_units must be positive integers.",
            )

        dynamodb = context.aws_adapter.session.client("dynamodb", region_name=context.payload.region)
        response = dynamodb.update_table(
            TableName=context.payload.resource_id,
            BillingMode="PROVISIONED",
            ProvisionedThroughput={
                "ReadCapacityUnits": read_capacity,
                "WriteCapacityUnits": write_capacity,
            },
        )
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="DynamoDB table updated to PROVISIONED capacity.",
            details={
                "table_name": context.payload.resource_id,
                "read_capacity_units": read_capacity,
                "write_capacity_units": write_capacity,
                "response": response,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to set DynamoDB provisioned capacity",
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
            detail=f"Failed to set DynamoDB provisioned capacity: {str(exc)}"
        )


def handle_enable_autoscaling(context: ActionExecutionContext) -> CheckActionResponse:
    """Enable auto-scaling for DynamoDB table."""
    try:
        params = context.payload.parameters or {}
        min_read = int(params.get("min_read_capacity", 5))
        max_read = int(params.get("max_read_capacity", 100))
        min_write = int(params.get("min_write_capacity", 5))
        max_write = int(params.get("max_write_capacity", 100))
        target_utilization = float(params.get("target_utilization", 70.0))

        if min_read <= 0 or max_read <= 0 or min_write <= 0 or max_write <= 0:
            raise HTTPException(
                status_code=400,
                detail="Autoscaling min/max capacities must be positive.",
            )
        if min_read > max_read or min_write > max_write:
            raise HTTPException(
                status_code=400,
                detail="Autoscaling min capacity cannot exceed max capacity.",
            )
        if target_utilization <= 0 or target_utilization > 100:
            raise HTTPException(
                status_code=400,
                detail="Target utilization must be between 1 and 100.",
            )

        autoscaling = context.aws_adapter.session.client(
            "application-autoscaling",
            region_name=context.payload.region,
        )

        resource_id = f"table/{context.payload.resource_id}"
        for dimension, min_cap, max_cap in (
            ("dynamodb:table:ReadCapacityUnits", min_read, max_read),
            ("dynamodb:table:WriteCapacityUnits", min_write, max_write),
        ):
            autoscaling.register_scalable_target(
                ServiceNamespace="dynamodb",
                ResourceId=resource_id,
                ScalableDimension=dimension,
                MinCapacity=min_cap,
                MaxCapacity=max_cap,
            )
            autoscaling.put_scaling_policy(
                PolicyName=f"maxops-{context.payload.resource_id}-{dimension.split(':')[-1]}",
                ServiceNamespace="dynamodb",
                ResourceId=resource_id,
                ScalableDimension=dimension,
                PolicyType="TargetTrackingScaling",
                TargetTrackingScalingPolicyConfiguration={
                    "TargetValue": target_utilization,
                    "PredefinedMetricSpecification": {
                        "PredefinedMetricType": (
                            "DynamoDBReadCapacityUtilization"
                            if dimension.endswith("ReadCapacityUnits")
                            else "DynamoDBWriteCapacityUtilization"
                        )
                    },
                },
            )

        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="DynamoDB autoscaling enabled.",
            details={
                "table_name": context.payload.resource_id,
                "min_read_capacity": min_read,
                "max_read_capacity": max_read,
                "min_write_capacity": min_write,
                "max_write_capacity": max_write,
                "target_utilization": target_utilization,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to enable DynamoDB autoscaling",
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
            detail=f"Failed to enable DynamoDB autoscaling: {str(exc)}"
        )


def handle_delete_gsi(context: ActionExecutionContext) -> CheckActionResponse:
    """Delete a Global Secondary Index from DynamoDB table."""
    try:
        if "/" not in context.payload.resource_id:
            raise HTTPException(
                status_code=400,
                detail="Expected resource_id format 'table_name/index_name' for GSI deletion.",
            )
        table_name, index_name = context.payload.resource_id.split("/", 1)
        if not table_name or not index_name:
            raise HTTPException(
                status_code=400,
                detail="Both table_name and index_name must be provided for GSI deletion.",
            )
        dynamodb = context.aws_adapter.session.client("dynamodb", region_name=context.payload.region)
        response = dynamodb.update_table(
            TableName=table_name,
            GlobalSecondaryIndexUpdates=[{"Delete": {"IndexName": index_name}}],
        )
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="DynamoDB GSI deletion submitted.",
            details={
                "table_name": table_name,
                "index_name": index_name,
                "response": response,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to delete DynamoDB GSI",
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
            detail=f"Failed to delete DynamoDB GSI: {str(exc)}"
        )


def handle_reduce_rcu(context: ActionExecutionContext) -> CheckActionResponse:
    """Reduce Read Capacity Units for DynamoDB table."""
    try:
        params = context.payload.parameters or {}
        desired_rcu = params.get("read_capacity_units")
        dynamodb = context.aws_adapter.session.client("dynamodb", region_name=context.payload.region)
        table = dynamodb.describe_table(TableName=context.payload.resource_id)["Table"]
        billing_mode = table.get("BillingModeSummary", {}).get("BillingMode")
        if billing_mode == "PAY_PER_REQUEST":
            raise HTTPException(
                status_code=400,
                detail="Table is using On-Demand billing; RCU reduction applies only to PROVISIONED tables.",
            )
        provisioned = table.get("ProvisionedThroughput", {})
        current_read = provisioned.get("ReadCapacityUnits")
        current_write = provisioned.get("WriteCapacityUnits")
        if current_write is None:
            raise HTTPException(
                status_code=400,
                detail="Unable to determine current write capacity units.",
            )

        if desired_rcu is None:
            if current_read is None:
                raise HTTPException(
                    status_code=400,
                    detail="Unable to determine current read capacity units.",
                )
            desired_rcu = max(1, int(round(current_read * 0.5)))

        try:
            desired_rcu = int(desired_rcu)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail="read_capacity_units must be an integer.",
            )
        if desired_rcu <= 0:
            raise HTTPException(
                status_code=400,
                detail="read_capacity_units must be a positive integer.",
            )

        response = dynamodb.update_table(
            TableName=context.payload.resource_id,
            ProvisionedThroughput={
                "ReadCapacityUnits": desired_rcu,
                "WriteCapacityUnits": current_write,
            },
        )
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="DynamoDB read capacity update submitted.",
            details={
                "table_name": context.payload.resource_id,
                "previous_read_capacity_units": current_read,
                "new_read_capacity_units": desired_rcu,
                "write_capacity_units": current_write,
                "response": response,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to reduce DynamoDB RCU",
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
            detail=f"Failed to reduce DynamoDB RCU: {str(exc)}"
        )


def handle_reduce_wcu(context: ActionExecutionContext) -> CheckActionResponse:
    """Reduce Write Capacity Units for DynamoDB table."""
    try:
        params = context.payload.parameters or {}
        desired_wcu = params.get("write_capacity_units")
        dynamodb = context.aws_adapter.session.client("dynamodb", region_name=context.payload.region)
        table = dynamodb.describe_table(TableName=context.payload.resource_id)["Table"]
        billing_mode = table.get("BillingModeSummary", {}).get("BillingMode")
        if billing_mode == "PAY_PER_REQUEST":
            raise HTTPException(
                status_code=400,
                detail="Table is using On-Demand billing; WCU reduction applies only to PROVISIONED tables.",
            )
        provisioned = table.get("ProvisionedThroughput", {})
        current_read = provisioned.get("ReadCapacityUnits")
        current_write = provisioned.get("WriteCapacityUnits")
        if current_read is None:
            raise HTTPException(
                status_code=400,
                detail="Unable to determine current read capacity units.",
            )

        if desired_wcu is None:
            if current_write is None:
                raise HTTPException(
                    status_code=400,
                    detail="Unable to determine current write capacity units.",
                )
            desired_wcu = max(1, int(round(current_write * 0.5)))

        try:
            desired_wcu = int(desired_wcu)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail="write_capacity_units must be an integer.",
            )
        if desired_wcu <= 0:
            raise HTTPException(
                status_code=400,
                detail="write_capacity_units must be a positive integer.",
            )

        response = dynamodb.update_table(
            TableName=context.payload.resource_id,
            ProvisionedThroughput={
                "ReadCapacityUnits": current_read,
                "WriteCapacityUnits": desired_wcu,
            },
        )
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="DynamoDB write capacity update submitted.",
            details={
                "table_name": context.payload.resource_id,
                "previous_write_capacity_units": current_write,
                "new_write_capacity_units": desired_wcu,
                "read_capacity_units": current_read,
                "response": response,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to reduce DynamoDB WCU",
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
            detail=f"Failed to reduce DynamoDB WCU: {str(exc)}"
        )


def handle_delete_table(context: ActionExecutionContext) -> CheckActionResponse:
    """Delete a DynamoDB table."""
    try:
        dynamodb = context.aws_adapter.session.client("dynamodb", region_name=context.payload.region)
        response = dynamodb.delete_table(TableName=context.payload.resource_id)
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="DynamoDB table deletion submitted.",
            details={"table_name": context.payload.resource_id, "response": response},
        )
    except Exception as exc:
        logger.exception(
            "Failed to delete DynamoDB table",
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
            detail=f"Failed to delete DynamoDB table: {str(exc)}"
        )


def handle_backup_and_delete_table(context: ActionExecutionContext) -> CheckActionResponse:
    """Create a backup of DynamoDB table before deleting it."""
    try:
        dynamodb = context.aws_adapter.session.client("dynamodb", region_name=context.payload.region)
        backup_name = f"maxops-backup-{context.payload.resource_id}"
        backup = dynamodb.create_backup(
            TableName=context.payload.resource_id,
            BackupName=backup_name,
        )
        delete_response = dynamodb.delete_table(TableName=context.payload.resource_id)
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="DynamoDB table backup created and deletion submitted.",
            details={
                "table_name": context.payload.resource_id,
                "backup_name": backup_name,
                "backup_arn": backup.get("BackupDetails", {}).get("BackupArn"),
                "delete_response": delete_response,
            },
        )
    except Exception as exc:
        logger.exception(
            "Failed to backup and delete DynamoDB table",
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
            detail=f"Failed to backup and delete DynamoDB table: {str(exc)}"
        )


def handle_switch_to_on_demand_billing(context: ActionExecutionContext) -> CheckActionResponse:
    """Switch DynamoDB table to On-Demand billing mode."""
    try:
        dynamodb = context.aws_adapter.session.client("dynamodb", region_name=context.payload.region)
        response = dynamodb.update_table(
            TableName=context.payload.resource_id,
            BillingMode="PAY_PER_REQUEST",
        )
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="DynamoDB table switched to On-Demand billing.",
            details={"table_name": context.payload.resource_id, "response": response},
        )
    except Exception as exc:
        logger.exception(
            "Failed to switch DynamoDB table to On-Demand billing",
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
            detail=f"Failed to switch DynamoDB table to On-Demand billing: {str(exc)}"
        )
