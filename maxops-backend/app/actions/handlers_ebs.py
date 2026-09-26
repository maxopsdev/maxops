"""EBS action handlers."""
from __future__ import annotations

import logging
from fastapi import HTTPException
from app.schemas.check import CheckActionResponse
from app.actions.base import ActionExecutionContext

logger = logging.getLogger("uvicorn.error")


def handle_snapshot_and_terminate(context: ActionExecutionContext):
    """Create snapshot of EBS volume and then delete it."""
    try:
        ec2 = context.aws_adapter.session.client("ec2", region_name=context.payload.region)
        snapshot = ec2.create_snapshot(
            VolumeId=context.payload.resource_id,
            Description=f"Snapshot before deleting volume {context.payload.resource_id}",
        )
        delete_response = ec2.delete_volume(VolumeId=context.payload.resource_id)
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="Snapshot created and delete volume request submitted",
            details={"snapshot": snapshot, "delete_volume": delete_response},
        )
    except Exception as exc:
        logger.exception(
            "Failed to execute EBS snapshot and terminate",
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
            detail=f"Failed to execute EBS snapshot and terminate: {str(exc)}"
        )


def handle_ebs_downsize_volume(context: ActionExecutionContext):
    """Modify EBS volume type, IOPS, or throughput."""
    try:
        params = context.payload.parameters or {}
        ec2 = context.aws_adapter.session.client("ec2", region_name=context.payload.region)
        vol_response = ec2.describe_volumes(VolumeIds=[context.payload.resource_id])
        volume = (vol_response.get("Volumes") or [None])[0]
        if not volume:
            raise HTTPException(status_code=404, detail="EBS volume not found.")
        current_volume_type = (volume.get("VolumeType") or "").lower()
        current_iops = volume.get("Iops")
        current_throughput = volume.get("Throughput")

        target_iops = params.get("iops")
        target_throughput = params.get("throughput")
        target_volume_type = params.get("volume_type")
        if target_iops is None and target_throughput is None and target_volume_type is None:
            raise HTTPException(
                status_code=400,
                detail="Provide iops, throughput, or volume_type to downsize.",
            )

        modify_params = {"VolumeId": context.payload.resource_id}
        desired_volume_type = current_volume_type
        if target_volume_type:
            desired_volume_type = str(target_volume_type).lower()
            modify_params["VolumeType"] = desired_volume_type
        if target_iops is not None:
            try:
                target_iops = int(target_iops)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="iops must be an integer.")
            if target_iops <= 0:
                raise HTTPException(status_code=400, detail="iops must be a positive integer.")
            if current_iops and target_iops > current_iops:
                raise HTTPException(status_code=400, detail="iops cannot exceed current provisioned IOPS.")
            if desired_volume_type == "gp3" and target_iops < 3000:
                raise HTTPException(status_code=400, detail="For gp3 volumes, iops must be at least 3000.")
            modify_params["Iops"] = target_iops
        if target_throughput is not None:
            try:
                target_throughput = int(target_throughput)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="throughput must be an integer.")
            if target_throughput <= 0:
                raise HTTPException(status_code=400, detail="throughput must be a positive integer.")
            if current_throughput and target_throughput > current_throughput:
                raise HTTPException(status_code=400, detail="throughput cannot exceed current provisioned throughput.")
            if desired_volume_type != "gp3":
                raise HTTPException(status_code=400, detail="throughput can only be set when target/current volume_type is gp3.")
            if target_throughput < 125 or target_throughput > 1000:
                raise HTTPException(status_code=400, detail="For gp3 volumes, throughput must be between 125 and 1000 MB/s.")
            modify_params["Throughput"] = target_throughput

        response = ec2.modify_volume(**modify_params)
        context.action_execution.status = "submitted"
        context.action_execution.message = "EBS volume modification submitted."
        context.action_execution.details_json = {
            "volume_id": context.payload.resource_id,
            "previous_volume_type": current_volume_type,
            "target_volume_type": desired_volume_type,
            "previous_iops": current_iops,
            "previous_throughput": current_throughput,
            "response": response,
        }
        context.db.commit()
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="EBS volume modification submitted.",
            details={
                "volume_id": context.payload.resource_id,
                "previous_iops": current_iops,
                "previous_throughput": current_throughput,
                "response": response,
            },
        )
    except HTTPException:
        context.action_execution.status = "failed"
        context.action_execution.error_message = "Validation failed or invalid EBS modify request."
        context.db.commit()
        raise
    except Exception as exc:
        logger.exception(
            "Failed to modify EBS volume",
            extra={
                "check_id": context.check_id,
                "action": context.payload.action,
                "account_id": context.payload.account_id,
                "region": context.payload.region,
                "resource_id": context.payload.resource_id,
            },
        )
        context.action_execution.status = "failed"
        context.action_execution.error_message = str(exc)
        context.db.commit()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to modify EBS volume: {str(exc)}"
        )


def handle_ebs_reduce_iops(context: ActionExecutionContext):
    """Reduce provisioned IOPS for an EBS volume."""
    try:
        params = context.payload.parameters or {}
        ec2 = context.aws_adapter.session.client("ec2", region_name=context.payload.region)
        vol_response = ec2.describe_volumes(VolumeIds=[context.payload.resource_id])
        volume = (vol_response.get("Volumes") or [None])[0]
        if not volume:
            raise HTTPException(status_code=404, detail="EBS volume not found.")
        current_volume_type = (volume.get("VolumeType") or "").lower()
        current_iops = volume.get("Iops")

        target_iops = params.get("iops")
        if target_iops is None:
            raise HTTPException(status_code=400, detail="Missing iops parameter.")
        try:
            target_iops = int(target_iops)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="iops must be an integer.")
        if target_iops <= 0:
            raise HTTPException(status_code=400, detail="iops must be a positive integer.")
        if current_iops and target_iops > current_iops:
            raise HTTPException(status_code=400, detail="iops cannot exceed current provisioned IOPS.")
        if current_volume_type == "gp3" and target_iops < 3000:
            raise HTTPException(status_code=400, detail="For gp3 volumes, iops must be at least 3000.")
        
        response = ec2.modify_volume(VolumeId=context.payload.resource_id, Iops=target_iops)
        context.action_execution.status = "submitted"
        context.action_execution.message = "EBS volume IOPS modification submitted."
        context.action_execution.details_json = {
            "volume_id": context.payload.resource_id,
            "previous_iops": current_iops,
            "new_iops": target_iops,
            "response": response,
        }
        context.db.commit()
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="EBS volume IOPS modification submitted.",
            details={
                "volume_id": context.payload.resource_id,
                "previous_iops": current_iops,
                "new_iops": target_iops,
                "response": response,
            },
        )
    except HTTPException:
        context.action_execution.status = "failed"
        context.action_execution.error_message = "Validation failed or invalid EBS modify request."
        context.db.commit()
        raise
    except Exception as exc:
        logger.exception(
            "Failed to reduce EBS IOPS",
            extra={
                "check_id": context.check_id,
                "action": context.payload.action,
                "account_id": context.payload.account_id,
                "region": context.payload.region,
                "resource_id": context.payload.resource_id,
            },
        )
        context.action_execution.status = "failed"
        context.action_execution.error_message = str(exc)
        context.db.commit()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to reduce EBS IOPS: {str(exc)}"
        )


def handle_ebs_lifecycle_policy(context: ActionExecutionContext):
    """Create DLM lifecycle policy for EBS volume snapshots."""
    try:
        params = context.payload.parameters or {}
        policy_name = params.get("policy_name") or f"maxops-ebs-{context.payload.resource_id}"
        interval_hours = params.get("interval_hours")
        retain_count = params.get("retain_count")
        tag_key = params.get("tag_key") or "maxops_policy"
        tag_value = params.get("tag_value") or policy_name
        role_override = params.get("role_arn")

        try:
            interval_hours = int(interval_hours)
            retain_count = int(retain_count)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail="interval_hours and retain_count must be integers.",
            )
        if interval_hours <= 0 or retain_count <= 0:
            raise HTTPException(
                status_code=400,
                detail="interval_hours and retain_count must be positive integers.",
            )
        if not role_override:
            raise HTTPException(
                status_code=400,
                detail="Missing role_arn. Provide an existing IAM role for DLM execution.",
            )

        ec2 = context.aws_adapter.session.client("ec2", region_name=context.payload.region)
        ec2.create_tags(
            Resources=[context.payload.resource_id],
            Tags=[{"Key": tag_key, "Value": tag_value}],
        )

        dlm = context.aws_adapter.session.client("dlm", region_name=context.payload.region)
        response = dlm.create_lifecycle_policy(
            ExecutionRoleArn=role_override,
            Description=f"MaxOps policy for {context.payload.resource_id}",
            State="ENABLED",
            PolicyDetails={
                "ResourceTypes": ["VOLUME"],
                "TargetTags": [{"Key": tag_key, "Value": tag_value}],
                "Schedules": [
                    {
                        "Name": policy_name,
                        "CreateRule": {"Interval": interval_hours, "IntervalUnit": "HOURS"},
                        "RetainRule": {"Count": retain_count},
                        "CopyTags": True,
                    }
                ],
            },
            Tags={"CreatedBy": "maxops"},
        )
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="EBS lifecycle policy created.",
            details={
                "policy_id": response.get("PolicyId"),
                "policy_name": policy_name,
                "tag_key": tag_key,
                "tag_value": tag_value,
                "interval_hours": interval_hours,
                "retain_count": retain_count,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to create EBS lifecycle policy",
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
            detail=f"Failed to create EBS lifecycle policy: {str(exc)}"
        )
