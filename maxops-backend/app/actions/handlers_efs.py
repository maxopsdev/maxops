"""EFS action handlers."""
from __future__ import annotations

import logging
import time
from fastapi import HTTPException
from app.schemas.check import CheckActionResponse
from app.actions.base import ActionExecutionContext

logger = logging.getLogger("uvicorn.error")


def handle_delete_file_system(context: ActionExecutionContext) -> CheckActionResponse:
    """Delete EFS file system."""
    try:
        efs = context.aws_adapter.session.client("efs", region_name=context.payload.region)
        response = efs.delete_file_system(FileSystemId=context.payload.resource_id)
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="EFS file system deletion submitted.",
            details={"file_system_id": context.payload.resource_id, "response": response},
        )
    except Exception as exc:
        logger.exception(
            "Failed to delete EFS file system",
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
            detail=f"Failed to delete EFS file system: {str(exc)}"
        )


def handle_modify_throughput_mode(context: ActionExecutionContext) -> CheckActionResponse:
    """Modify EFS throughput mode between bursting and provisioned."""
    try:
        params = context.payload.parameters or {}
        throughput_mode = params.get("throughput_mode", "provisioned")
        provisioned_throughput = params.get("provisioned_throughput_in_mibps")
    
        if throughput_mode not in ["bursting", "provisioned"]:
            raise HTTPException(
                status_code=400,
                detail="throughput_mode must be 'bursting' or 'provisioned'"
            )
    
        efs = context.aws_adapter.session.client("efs", region_name=context.payload.region)
        update_params = {
            "FileSystemId": context.payload.resource_id,
            "ThroughputMode": throughput_mode
        }
    
        if throughput_mode == "provisioned":
            if not provisioned_throughput:
                raise HTTPException(
                    status_code=400,
                    detail="provisioned_throughput_in_mibps is required when throughput_mode is 'provisioned'"
                )
            try:
                provisioned_throughput = float(provisioned_throughput)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail="provisioned_throughput_in_mibps must be a number"
                )
            update_params["ProvisionedThroughputInMibps"] = provisioned_throughput
    
        response = efs.update_file_system(**update_params)
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="EFS throughput mode modification submitted.",
            details={
                "file_system_id": context.payload.resource_id,
                "throughput_mode": throughput_mode,
                "provisioned_throughput_in_mibps": provisioned_throughput if throughput_mode == "provisioned" else None,
                "response": response,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to modify EFS throughput mode",
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
            detail=f"Failed to modify EFS throughput mode: {str(exc)}"
        )


def handle_add_lifecycle_policy(context: ActionExecutionContext) -> CheckActionResponse:
    """Add EFS lifecycle policy for transition to Infrequent Access storage class."""
    try:
        params = context.payload.parameters or {}
        transition_to_ia = params.get("transition_to_ia", "AFTER_30_DAYS")
    
        # Valid transition values
        valid_transitions = [
            "AFTER_7_DAYS",
            "AFTER_14_DAYS",
            "AFTER_30_DAYS",
            "AFTER_60_DAYS",
            "AFTER_90_DAYS"
        ]
    
        if transition_to_ia not in valid_transitions:
            raise HTTPException(
                status_code=400,
                detail=f"transition_to_ia must be one of: {', '.join(valid_transitions)}"
            )
    
        efs = context.aws_adapter.session.client("efs", region_name=context.payload.region)
    
        # Get existing lifecycle policies
        try:
            existing_policies = efs.describe_lifecycle_configuration(FileSystemId=context.payload.resource_id)
            policies = existing_policies.get("LifecyclePolicies", [])
        except Exception:
            policies = []
    
        # Check if policy already exists
        for policy in policies:
            if policy.get("TransitionToIA") == transition_to_ia:
                return CheckActionResponse(
                    check_id=context.check_id,
                    action=context.payload.action,
                    status="no_op",
                    message=f"Lifecycle policy with transition_to_ia={transition_to_ia} already exists.",
                    details={"file_system_id": context.payload.resource_id},
                )
    
        # Add new lifecycle policy
        policies.append({"TransitionToIA": transition_to_ia})
        response = efs.put_lifecycle_configuration(
            FileSystemId=context.payload.resource_id,
            LifecyclePolicies=policies
        )
    
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message=f"EFS lifecycle policy added: transition to IA {transition_to_ia}.",
            details={
                "file_system_id": context.payload.resource_id,
                "transition_to_ia": transition_to_ia,
                "response": response,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to add EFS lifecycle policy",
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
            detail=f"Failed to add EFS lifecycle policy: {str(exc)}"
        )


# Note: modify_performance_mode is extremely complex (300+ lines, involves creating new FS,
# DataSync setup, etc.). Will be extracted next.


def handle_modify_performance_mode(context: ActionExecutionContext) -> CheckActionResponse:
    """Modify EFS performance mode (requires creating new FS and migrating data)."""
    try:
        params = context.payload.parameters or {}
        performance_mode = params.get("performance_mode", "maxIO")
        copy_data = params.get("copy_data", False)
        delete_old_after_copy = params.get("delete_old_after_copy", False)
    
        if performance_mode not in ["generalPurpose", "maxIO"]:
            raise HTTPException(
                status_code=400,
                detail="performance_mode must be 'generalPurpose' or 'maxIO'"
            )
    
        efs = context.aws_adapter.session.client("efs", region_name=context.payload.region)
    
        # Get current file system details
        try:
            fs_response = efs.describe_file_systems(FileSystemId=context.payload.resource_id)
            if not fs_response.get("FileSystems"):
                raise HTTPException(status_code=404, detail="File system not found")
        
            current_fs = fs_response["FileSystems"][0]
            current_performance_mode = current_fs.get("PerformanceMode", "generalPurpose")
        
            # Check if already in desired mode
            if current_performance_mode == performance_mode:
                return CheckActionResponse(
                    check_id=context.check_id,
                    action=context.payload.action,
                    status="no_op",
                    message=f"File system already uses {performance_mode} performance mode",
                    details={"current_performance_mode": current_performance_mode}
                )
        
            # Get VPC and subnet information for new file system
            mount_targets = efs.describe_mount_targets(FileSystemId=context.payload.resource_id)
            mount_target_list = mount_targets.get("MountTargets", [])
        
            if not mount_target_list:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot create replacement file system: No mount targets found. Please create mount targets first."
                )
        
            # Use the first mount target's subnet and security groups
            first_mt = mount_target_list[0]
            subnet_id = first_mt.get("SubnetId")
            security_groups = first_mt.get("SecurityGroups", [])
        
            # Get file system tags
            tags_response = efs.describe_tags(FileSystemId=context.payload.resource_id)
            tags = [{"Key": tag["Key"], "Value": tag["Value"]} for tag in tags_response.get("Tags", [])]
        
            # Create new file system with desired performance mode
            creation_token = f"maxops-migration-{context.payload.resource_id}-{int(time.time())}"
            new_fs_params = {
                "CreationToken": creation_token,
                "PerformanceMode": performance_mode,
                "ThroughputMode": current_fs.get("ThroughputMode", "bursting"),
                "Tags": tags + [{"Key": "maxops_migration_source", "Value": context.payload.resource_id}]
            }
        
            if current_fs.get("Encrypted"):
                new_fs_params["Encrypted"] = True
                if current_fs.get("KmsKeyId"):
                    new_fs_params["KmsKeyId"] = current_fs["KmsKeyId"]
        
            new_fs_response = efs.create_file_system(**new_fs_params)
            new_fs_id = new_fs_response["FileSystemId"]
        
            # Wait for file system to be available
            max_wait = 300
            wait_interval = 5
            waited = 0
            while waited < max_wait:
                fs_status = efs.describe_file_systems(FileSystemId=new_fs_id)
                if fs_status["FileSystems"][0]["LifeCycleState"] == "available":
                    break
                time.sleep(wait_interval)
                waited += wait_interval
        
            if waited >= max_wait:
                raise HTTPException(
                    status_code=500,
                    detail="New file system did not become available in time"
                )
        
            # Create mount target in the same subnet
            try:
                efs.create_mount_target(
                    FileSystemId=new_fs_id,
                    SubnetId=subnet_id,
                    SecurityGroups=security_groups
                )
                # Wait for mount target to be available
                time.sleep(10)  # Mount targets typically become available quickly
            except Exception as mt_error:
                logger.warning(f"Failed to create mount target for new file system: {mt_error}")
        
            copy_status = None
            copy_task_arn = None
            copy_task_execution_arn = None
        
            # If copy_data is requested, attempt to use AWS DataSync
            if copy_data:
                try:
                    datasync = context.aws_adapter.session.client("datasync", region_name=context.payload.region)
                
                    # Try to create a DataSync task
                    try:
                        # Create source location (old EFS)
                        source_location = datasync.create_location_efs(
                            EfsFilesystemArn=f"arn:aws:elasticfilesystem:{context.payload.region}:{context.payload.account_id}:file-system/{context.payload.resource_id}",
                            Ec2Config={
                                "SubnetArn": f"arn:aws:ec2:{context.payload.region}:{context.payload.account_id}:subnet/{subnet_id}",
                                "SecurityGroupArns": [
                                    f"arn:aws:ec2:{context.payload.region}:{context.payload.account_id}:security-group/{sg}" 
                                    for sg in security_groups
                                ]
                            }
                        )
                        source_location_arn = source_location["LocationArn"]
                    
                        # Create destination location (new EFS)
                        dest_location = datasync.create_location_efs(
                            EfsFilesystemArn=f"arn:aws:elasticfilesystem:{context.payload.region}:{context.payload.account_id}:file-system/{new_fs_id}",
                            Ec2Config={
                                "SubnetArn": f"arn:aws:ec2:{context.payload.region}:{context.payload.account_id}:subnet/{subnet_id}",
                                "SecurityGroupArns": [
                                    f"arn:aws:ec2:{context.payload.region}:{context.payload.account_id}:security-group/{sg}" 
                                    for sg in security_groups
                                ]
                            }
                        )
                        dest_location_arn = dest_location["LocationArn"]
                    
                        # Create DataSync task
                        task_name = f"maxops-efs-migration-{context.payload.resource_id}-{new_fs_id}"
                        task = datasync.create_task(
                            SourceLocationArn=source_location_arn,
                            DestinationLocationArn=dest_location_arn,
                            Name=task_name,
                            Options={
                                "VerifyMode": "POINT_IN_TIME_CONSISTENT",
                                "OverwriteMode": "ALWAYS",
                                "Atime": "BEST_EFFORT",
                                "Mtime": "PRESERVE",
                                "Uid": "NONE",
                                "Gid": "NONE",
                                "PreserveDeletedFiles": "REMOVE",
                                "PreserveDevices": "NONE",
                                "PosixPermissions": "NONE",
                                "BytesPerSecond": -1,
                                "TaskQueueing": "ENABLED",
                                "LogLevel": "TRANSFER",
                                "TransferMode": "CHANGED"
                            }
                        )
                        copy_task_arn = task["TaskArn"]
                    
                        # Start the task and get execution ARN
                        execution_response = datasync.start_task_execution(TaskArn=copy_task_arn)
                        copy_task_execution_arn = execution_response.get("TaskExecutionArn")
                        copy_status = "started"
                        logger.info(f"DataSync task started: {copy_task_arn}, execution: {copy_task_execution_arn}")
                    
                    except Exception as datasync_error:
                        # DataSync setup failed (likely no agent configured)
                        logger.warning(f"DataSync setup failed, will provide manual copy instructions: {datasync_error}")
                        copy_status = "manual_required"
                        copy_task_arn = None
                        copy_task_execution_arn = None
                    
                except Exception as copy_error:
                    logger.warning(f"Failed to initiate data copy: {copy_error}")
                    copy_status = "manual_required"
                    copy_task_execution_arn = None
        
            # Prepare response details
            response_details = {
                "old_file_system_id": context.payload.resource_id,
                "new_file_system_id": new_fs_id,
                "old_performance_mode": current_performance_mode,
                "new_performance_mode": performance_mode,
                "copy_data": copy_data,
                "delete_old_after_copy": delete_old_after_copy,
            }
        
            if copy_data:
                response_details["copy_status"] = copy_status
                if copy_task_arn and copy_task_execution_arn:
                    response_details["datasync_task_arn"] = copy_task_arn
                    response_details["datasync_task_execution_arn"] = copy_task_execution_arn
                    response_details["requires_polling"] = True
                else:
                    response_details["requires_polling"] = True
                    response_details["copy_instructions"] = [
                        "DataSync is not available. Please copy data manually:",
                        f"1. Mount both file systems on an EC2 instance in the same VPC",
                        f"2. Run: rsync -avz --delete /mnt/{context.payload.resource_id}/ /mnt/{new_fs_id}/",
                        f"3. Verify data integrity",
                        f"4. Update applications to use new file system: {new_fs_id}",
                    ]
                    if delete_old_after_copy:
                        response_details["copy_instructions"].append(
                            f"5. After verification, delete old file system: {context.payload.resource_id}"
                        )
            else:
                response_details["migration_instructions"] = {
                    "step1": "New file system created",
                    "step2": "Manually migrate data using rsync or AWS DataSync",
                    "step3": "Update applications to use new file system",
                    "step4": "After verification, delete old file system",
                    "migration_command": f"rsync -avz /mnt/{context.payload.resource_id}/ /mnt/{new_fs_id}/",
                }
        
            message = f"New file system created with {performance_mode} performance mode."
            if copy_data:
                if copy_status == "started":
                    message += " DataSync task started to copy data."
                else:
                    message += " Please copy data manually (see instructions)."
            else:
                message += " Please migrate data from old file system."
        
            # Update action execution record
            context.action_execution.status = "submitted"
            context.action_execution.message = message
            context.action_execution.details_json = response_details
            if copy_data and copy_task_execution_arn:
                context.action_execution.requires_polling = "datasync"
                context.action_execution.polling_identifier = copy_task_execution_arn
            elif copy_data:
                # Even if DataSync failed, mark as requiring manual tracking
                context.action_execution.requires_polling = "manual"
            context.db.commit()
        
            return CheckActionResponse(
                check_id=context.check_id,
                action=context.payload.action,
                status="submitted",
                message=message,
                details=response_details
            )
        
        except HTTPException:
            context.action_execution.status = "failed"
            context.db.commit()
            raise
        except Exception as fs_error:
            logger.exception("Failed to get file system details")
            context.action_execution.status = "failed"
            context.action_execution.error_message = str(fs_error)
            context.db.commit()
            raise HTTPException(
                status_code=500,
                detail=f"Failed to get file system details: {str(fs_error)}"
            )
        
    except HTTPException:
        context.action_execution.status = "failed"
        context.db.commit()
        raise
    except Exception as exc:
        logger.exception(
            "Failed to create replacement EFS file system with new performance mode",
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
            detail=f"Failed to create replacement file system: {str(exc)}"
        )


# Backwards-compatible aliases expected by app.actions.__init__
def handle_efs_delete_file_system(context: ActionExecutionContext) -> CheckActionResponse:
    return handle_delete_file_system(context)


def handle_efs_modify_throughput_mode(context: ActionExecutionContext) -> CheckActionResponse:
    return handle_modify_throughput_mode(context)


def handle_efs_add_lifecycle_policy(context: ActionExecutionContext) -> CheckActionResponse:
    return handle_add_lifecycle_policy(context)


def handle_efs_modify_performance_mode(context: ActionExecutionContext) -> CheckActionResponse:
    return handle_modify_performance_mode(context)
