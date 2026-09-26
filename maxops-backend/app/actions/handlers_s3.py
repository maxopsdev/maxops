"""S3 action handlers."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from fastapi import HTTPException
from app.schemas.check import CheckActionResponse
from app.actions.base import ActionExecutionContext

logger = logging.getLogger("uvicorn.error")


def _empty_s3_bucket(s3_client, bucket_name: str) -> None:
    """Helper function to empty all objects and versions from S3 bucket."""
    versions = s3_client.get_paginator("list_object_versions")
    for page in versions.paginate(Bucket=bucket_name):
        objs = []
        for v in page.get("Versions", []):
            if v.get("Key") and v.get("VersionId"):
                objs.append({"Key": v["Key"], "VersionId": v["VersionId"]})
        for d in page.get("DeleteMarkers", []):
            if d.get("Key") and d.get("VersionId"):
                objs.append({"Key": d["Key"], "VersionId": d["VersionId"]})
        if objs:
            s3_client.delete_objects(Bucket=bucket_name, Delete={"Objects": objs})
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket_name):
        contents = page.get("Contents", [])
        if not contents:
            continue
        objects = [{"Key": obj["Key"]} for obj in contents if obj.get("Key")]
        if objects:
            s3_client.delete_objects(Bucket=bucket_name, Delete={"Objects": objects})


def handle_disable_logging_and_delete_log_bucket(context: ActionExecutionContext) -> CheckActionResponse:
    """Disable S3 logging and delete the target log bucket."""
    try:
        s3 = context.aws_adapter.session.client("s3", region_name=context.payload.region)
        logging_cfg = s3.get_bucket_logging(Bucket=context.payload.resource_id)
        le = logging_cfg.get("LoggingEnabled") or {}
        target_bucket = le.get("TargetBucket")
        if not target_bucket:
            return CheckActionResponse(
                check_id=context.check_id,
                action=context.payload.action,
                status="no_op",
                message="Bucket logging is not enabled.",
                details={"source_bucket": context.payload.resource_id},
            )

        # Disable logging on source bucket
        s3.put_bucket_logging(Bucket=context.payload.resource_id, BucketLoggingStatus={})

        # Empty and delete target bucket
        _empty_s3_bucket(s3, target_bucket)
        s3.delete_bucket(Bucket=target_bucket)

        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="Logging disabled and target log bucket deletion requested.",
            details={
                "source_bucket": context.payload.resource_id,
                "target_bucket": target_bucket,
            },
        )
    except Exception as exc:
        logger.exception(
            "Failed to disable S3 logging and delete log bucket",
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
            detail=f"Failed to disable S3 logging and delete log bucket: {str(exc)}"
        )


def handle_disable_inventory_and_delete_log_bucket(context: ActionExecutionContext) -> CheckActionResponse:
    """Disable S3 inventory and delete the target inventory bucket."""
    try:
        s3 = context.aws_adapter.session.client("s3", region_name=context.payload.region)
        inventory_cfg = s3.list_bucket_inventory_configurations(Bucket=context.payload.resource_id)
        configs = inventory_cfg.get("InventoryConfigurationList") or inventory_cfg.get("InventoryConfigurations") or []
        if not configs:
            return CheckActionResponse(
                check_id=context.check_id,
                action=context.payload.action,
                status="no_op",
                message="Bucket inventory is not enabled.",
                details={"source_bucket": context.payload.resource_id},
            )

        target_buckets = set()
        for cfg in configs:
            cfg_id = cfg.get("Id")
            if cfg_id:
                s3.delete_bucket_inventory_configuration(Bucket=context.payload.resource_id, Id=cfg_id)
            dest = cfg.get("Destination") or {}
            s3_dest = dest.get("S3BucketDestination") or dest.get("s3BucketDestination") or {}
            bucket_arn = s3_dest.get("Bucket") or s3_dest.get("bucket")
            if isinstance(bucket_arn, str):
                if bucket_arn.startswith("arn:aws:s3:::"):
                    target_buckets.add(bucket_arn.split(":::", 1)[1])
                else:
                    target_buckets.add(bucket_arn)

        for bucket in target_buckets:
            _empty_s3_bucket(s3, bucket)
            s3.delete_bucket(Bucket=bucket)

        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="Inventory disabled and target inventory bucket deletion requested.",
            details={
                "source_bucket": context.payload.resource_id,
                "target_buckets": sorted(list(target_buckets)),
            },
        )
    except Exception as exc:
        logger.exception(
            "Failed to disable S3 inventory and delete inventory bucket",
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
            detail=f"Failed to disable S3 inventory and delete inventory bucket: {str(exc)}"
        )


def handle_add_log_retention_lifecycle(context: ActionExecutionContext) -> CheckActionResponse:
    """Add lifecycle policy for log retention (90 days expiration)."""
    try:
        s3 = context.aws_adapter.session.client("s3", region_name=context.payload.region)
        try:
            lifecycle = s3.get_bucket_lifecycle_configuration(Bucket=context.payload.resource_id)
            rules = lifecycle.get("Rules", [])
        except Exception:
            rules = []

        rule_id = f"maxops-log-retention-{int(datetime.now(timezone.utc).timestamp())}"
        new_rule = {
            "ID": rule_id,
            "Status": "Enabled",
            "Filter": {"Prefix": ""},
            "Expiration": {"Days": 90},
        }
        rules.append(new_rule)

        s3.put_bucket_lifecycle_configuration(
            Bucket=context.payload.resource_id,
            LifecycleConfiguration={"Rules": rules},
        )
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="Log retention lifecycle policy added.",
            details={"bucket": context.payload.resource_id, "rule_id": rule_id, "days": 90},
        )
    except Exception as exc:
        logger.exception(
            "Failed to add S3 log retention lifecycle policy",
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
            detail=f"Failed to add S3 log retention lifecycle policy: {str(exc)}"
        )


def handle_add_archival_transition(context: ActionExecutionContext) -> CheckActionResponse:
    """Add lifecycle policy for archival transition to GLACIER."""
    try:
        s3 = context.aws_adapter.session.client("s3", region_name=context.payload.region)
        try:
            lifecycle = s3.get_bucket_lifecycle_configuration(Bucket=context.payload.resource_id)
            rules = lifecycle.get("Rules", [])
        except Exception:
            rules = []

        params = context.payload.parameters or {}
        days = params.get("archival_days", 30)
        if not isinstance(days, int) or days < 1:
            raise HTTPException(status_code=400, detail="archival_days must be a positive integer.")

        rule_id = f"maxops-archival-transition-{int(datetime.now(timezone.utc).timestamp())}"
        new_rule = {
            "ID": rule_id,
            "Status": "Enabled",
            "Filter": {"Prefix": ""},
            "Transitions": [{"Days": days, "StorageClass": "GLACIER"}],
        }
        rules.append(new_rule)

        s3.put_bucket_lifecycle_configuration(
            Bucket=context.payload.resource_id,
            LifecycleConfiguration={"Rules": rules},
        )
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="Archival transition lifecycle policy added.",
            details={"bucket": context.payload.resource_id, "rule_id": rule_id, "days": days},
        )
    except Exception as exc:
        logger.exception(
            "Failed to add S3 archival transition lifecycle policy",
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
            detail=f"Failed to add S3 archival transition lifecycle policy: {str(exc)}"
        )


def handle_enable_delete_marker_cleanup(context: ActionExecutionContext) -> CheckActionResponse:
    """Enable automatic cleanup of expired object delete markers."""
    try:
        s3 = context.aws_adapter.session.client("s3", region_name=context.payload.region)
        try:
            lifecycle = s3.get_bucket_lifecycle_configuration(Bucket=context.payload.resource_id)
            rules = lifecycle.get("Rules", [])
        except Exception:
            rules = []

        rule_id = f"maxops-delete-marker-cleanup-{int(datetime.now(timezone.utc).timestamp())}"
        new_rule = {
            "ID": rule_id,
            "Status": "Enabled",
            "Filter": {"Prefix": ""},
            "Expiration": {"ExpiredObjectDeleteMarker": True},
        }
        rules.append(new_rule)

        s3.put_bucket_lifecycle_configuration(
            Bucket=context.payload.resource_id,
            LifecycleConfiguration={"Rules": rules},
        )
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="Delete marker cleanup lifecycle policy added.",
            details={"bucket": context.payload.resource_id, "rule_id": rule_id},
        )
    except Exception as exc:
        logger.exception(
            "Failed to add S3 delete marker cleanup lifecycle policy",
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
            detail=f"Failed to add S3 delete marker cleanup lifecycle policy: {str(exc)}"
        )


def handle_add_expiration_policy(context: ActionExecutionContext) -> CheckActionResponse:
    """Add lifecycle policy for object expiration."""
    try:
        s3 = context.aws_adapter.session.client("s3", region_name=context.payload.region)
        try:
            lifecycle = s3.get_bucket_lifecycle_configuration(Bucket=context.payload.resource_id)
            rules = lifecycle.get("Rules", [])
        except Exception:
            rules = []

        params = context.payload.parameters or {}
        days = params.get("expiration_days", 90)
        if not isinstance(days, int) or days < 1:
            raise HTTPException(status_code=400, detail="expiration_days must be a positive integer.")

        rule_id = f"maxops-expiration-{int(datetime.now(timezone.utc).timestamp())}"
        new_rule = {
            "ID": rule_id,
            "Status": "Enabled",
            "Filter": {"Prefix": ""},
            "Expiration": {"Days": days},
        }
        rules.append(new_rule)

        s3.put_bucket_lifecycle_configuration(
            Bucket=context.payload.resource_id,
            LifecycleConfiguration={"Rules": rules},
        )
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="Expiration lifecycle policy added.",
            details={"bucket": context.payload.resource_id, "rule_id": rule_id, "days": days},
        )
    except Exception as exc:
        logger.exception(
            "Failed to add S3 expiration lifecycle policy",
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
            detail=f"Failed to add S3 expiration lifecycle policy: {str(exc)}"
        )


def handle_add_lifecycle_policy(context: ActionExecutionContext) -> CheckActionResponse:
    """Add comprehensive lifecycle policy with transitions, expiration, and MPU abort."""
    try:
        params = context.payload.parameters or {}
        transition_days = params.get("transition_days", 30)
        transition_storage_class = params.get("transition_storage_class", "GLACIER")
        expiration_days = params.get("expiration_days", 365)
        abort_mpu_days = params.get("abort_mpu_days", 7)
        
        # Validate parameters
        try:
            transition_days = int(transition_days)
            expiration_days = int(expiration_days)
            abort_mpu_days = int(abort_mpu_days)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail="transition_days, expiration_days, and abort_mpu_days must be integers."
            )
        
        # Validate minimum transition days based on storage class
        min_days = {"GLACIER": 30, "GLACIER_IR": 0, "DEEP_ARCHIVE": 90, "INTELLIGENT_TIERING": 0}
        if transition_storage_class in min_days and transition_days < min_days[transition_storage_class]:
            raise HTTPException(
                status_code=400,
                detail=f"{transition_storage_class} requires minimum {min_days[transition_storage_class]} days."
            )
        
        s3 = context.aws_adapter.session.client("s3", region_name=context.payload.region)
        try:
            lifecycle = s3.get_bucket_lifecycle_configuration(Bucket=context.payload.resource_id)
            rules = lifecycle.get("Rules", [])
        except Exception:
            rules = []

        rule_id = f"maxops-basic-lifecycle-{int(datetime.now(timezone.utc).timestamp())}"
        new_rule = {
            "ID": rule_id,
            "Status": "Enabled",
            "Filter": {"Prefix": ""},
            "Transitions": [{"Days": transition_days, "StorageClass": transition_storage_class}],
            "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": abort_mpu_days},
        }
        
        # Only add expiration if > 0
        if expiration_days > 0:
            new_rule["Expiration"] = {"Days": expiration_days}
        
        rules.append(new_rule)

        s3.put_bucket_lifecycle_configuration(
            Bucket=context.payload.resource_id,
            LifecycleConfiguration={"Rules": rules},
        )
        
        message = f"Lifecycle policy added: Transition to {transition_storage_class} after {transition_days} days"
        if expiration_days > 0:
            message += f", delete after {expiration_days} days"
        message += f", abort incomplete uploads after {abort_mpu_days} days."
        
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message=message,
            details={
                "bucket": context.payload.resource_id,
                "rule_id": rule_id,
                "transition_days": transition_days,
                "transition_storage_class": transition_storage_class,
                "expiration_days": expiration_days,
                "abort_mpu_days": abort_mpu_days,
            },
        )
    except Exception as exc:
        logger.exception(
            "Failed to add S3 lifecycle policy",
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
            detail=f"Failed to add S3 lifecycle policy: {str(exc)}"
        )


def handle_add_abort_mpu_policy(context: ActionExecutionContext) -> CheckActionResponse:
    """Add lifecycle policy to abort incomplete multipart uploads."""
    try:
        s3 = context.aws_adapter.session.client("s3", region_name=context.payload.region)
        try:
            lifecycle = s3.get_bucket_lifecycle_configuration(Bucket=context.payload.resource_id)
            rules = lifecycle.get("Rules", [])
        except Exception:
            rules = []

        rule_id = f"maxops-abort-mpu-{int(datetime.now(timezone.utc).timestamp())}"
        new_rule = {
            "ID": rule_id,
            "Status": "Enabled",
            "Filter": {"Prefix": ""},
            "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7},
        }
        rules.append(new_rule)

        s3.put_bucket_lifecycle_configuration(
            Bucket=context.payload.resource_id,
            LifecycleConfiguration={"Rules": rules},
        )
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="Abort incomplete MPUs lifecycle policy added.",
            details={"bucket": context.payload.resource_id, "rule_id": rule_id, "days": 7},
        )
    except Exception as exc:
        logger.exception(
            "Failed to add S3 abort MPU lifecycle policy",
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
            detail=f"Failed to add S3 abort MPU lifecycle policy: {str(exc)}"
        )


def handle_add_noncurrent_expiration(context: ActionExecutionContext) -> CheckActionResponse:
    """Add lifecycle policy for noncurrent version expiration."""
    try:
        s3 = context.aws_adapter.session.client("s3", region_name=context.payload.region)
        try:
            lifecycle = s3.get_bucket_lifecycle_configuration(Bucket=context.payload.resource_id)
            rules = lifecycle.get("Rules", [])
        except Exception:
            rules = []

        params = context.payload.parameters or {}
        days = params.get("noncurrent_expiration_days", 30)
        if not isinstance(days, int) or days < 1:
            raise HTTPException(status_code=400, detail="noncurrent_expiration_days must be a positive integer.")

        rule_id = f"maxops-noncurrent-expiration-{int(datetime.now(timezone.utc).timestamp())}"
        new_rule = {
            "ID": rule_id,
            "Status": "Enabled",
            "Filter": {"Prefix": ""},
            "NoncurrentVersionExpiration": {"NoncurrentDays": days},
        }
        rules.append(new_rule)

        s3.put_bucket_lifecycle_configuration(
            Bucket=context.payload.resource_id,
            LifecycleConfiguration={"Rules": rules},
        )
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="Noncurrent version expiration lifecycle policy added.",
            details={"bucket": context.payload.resource_id, "rule_id": rule_id, "days": days},
        )
    except Exception as exc:
        logger.exception(
            "Failed to add S3 noncurrent expiration lifecycle policy",
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
            detail=f"Failed to add S3 noncurrent expiration lifecycle policy: {str(exc)}"
        )


def handle_add_noncurrent_archival_transition(context: ActionExecutionContext) -> CheckActionResponse:
    """Add lifecycle policy for noncurrent version archival transition."""
    try:
        s3 = context.aws_adapter.session.client("s3", region_name=context.payload.region)
        try:
            lifecycle = s3.get_bucket_lifecycle_configuration(Bucket=context.payload.resource_id)
            rules = lifecycle.get("Rules", [])
        except Exception:
            rules = []

        params = context.payload.parameters or {}
        days = params.get("noncurrent_transition_days", 30)
        if not isinstance(days, int) or days < 1:
            raise HTTPException(status_code=400, detail="noncurrent_transition_days must be a positive integer.")

        rule_id = f"maxops-noncurrent-archival-{int(datetime.now(timezone.utc).timestamp())}"
        new_rule = {
            "ID": rule_id,
            "Status": "Enabled",
            "Filter": {"Prefix": ""},
            "NoncurrentVersionTransitions": [{"NoncurrentDays": days, "StorageClass": "GLACIER"}],
        }
        rules.append(new_rule)

        s3.put_bucket_lifecycle_configuration(
            Bucket=context.payload.resource_id,
            LifecycleConfiguration={"Rules": rules},
        )
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="Noncurrent archival transition lifecycle policy added.",
            details={"bucket": context.payload.resource_id, "rule_id": rule_id, "days": days},
        )
    except Exception as exc:
        logger.exception(
            "Failed to add S3 noncurrent archival transition lifecycle policy",
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
            detail=f"Failed to add S3 noncurrent archival transition lifecycle policy: {str(exc)}"
        )


def handle_disable_replication_and_delete_bucket(context: ActionExecutionContext) -> CheckActionResponse:
    """Disable S3 replication and delete the target replication bucket."""
    try:
        s3 = context.aws_adapter.session.client("s3", region_name=context.payload.region)
        try:
            replication = s3.get_bucket_replication(Bucket=context.payload.resource_id)
            rc = replication.get("ReplicationConfiguration") or replication
            rules = rc.get("Rules") or []
        except Exception:
            rules = []

        target_buckets = set()
        for r in rules:
            dest = r.get("Destination") or {}
            bucket_arn = dest.get("Bucket")
            if isinstance(bucket_arn, str):
                if bucket_arn.startswith("arn:aws:s3:::"):
                    target_buckets.add(bucket_arn.split(":::", 1)[1])
                else:
                    target_buckets.add(bucket_arn)

        try:
            s3.delete_bucket_replication(Bucket=context.payload.resource_id)
        except Exception:
            pass

        for bucket in target_buckets:
            _empty_s3_bucket(s3, bucket)
            s3.delete_bucket(Bucket=bucket)

        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="Replication disabled and replicate bucket deletion requested.",
            details={"source_bucket": context.payload.resource_id, "target_buckets": sorted(list(target_buckets))},
        )
    except Exception as exc:
        logger.exception(
            "Failed to disable S3 replication and delete replicate bucket",
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
            detail=f"Failed to disable S3 replication and delete replicate bucket: {str(exc)}"
        )


# Backwards-compatible aliases expected by app.actions.__init__
def handle_add_basic_lifecycle_policy(context: ActionExecutionContext) -> CheckActionResponse:
    return handle_add_lifecycle_policy(context)
