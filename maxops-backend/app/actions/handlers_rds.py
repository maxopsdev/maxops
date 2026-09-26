"""RDS action handlers."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import HTTPException

from app.actions.base import ActionExecutionContext
from app.checks.rds.non_graviton import _is_graviton_instance_class
from app.schemas.check import CheckActionResponse

logger = logging.getLogger("uvicorn.error")


def _normalize_bool(value: Any, default: bool = False) -> Optional[bool]:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return None


def handle_rds_migrate_graviton(
    context: ActionExecutionContext,
) -> CheckActionResponse:
    """Change an RDS DB instance to an explicitly selected Graviton class."""
    try:
        params = context.payload.parameters or {}
        target_class = (
            params.get("target_instance_class")
            or params.get("target_db_instance_class")
            or params.get("db_instance_class")
        )
        if not isinstance(target_class, str) or not target_class.strip():
            raise HTTPException(
                status_code=400,
                detail="target_instance_class is required.",
            )
        target_class = target_class.strip().lower()
        if not _is_graviton_instance_class(target_class):
            raise HTTPException(
                status_code=400,
                detail="target_instance_class must be a Graviton RDS class.",
            )

        apply_immediately = _normalize_bool(params.get("apply_immediately"), False)
        if apply_immediately is None:
            raise HTTPException(
                status_code=400,
                detail="apply_immediately must be a boolean.",
            )

        rds = context.aws_adapter.session.client(
            "rds", region_name=context.payload.region
        )
        response = rds.describe_db_instances(
            DBInstanceIdentifier=context.payload.resource_id
        )
        instances = response.get("DBInstances", [])
        if not instances:
            raise HTTPException(status_code=404, detail="RDS DB instance not found.")

        instance = instances[0]
        current_class = instance.get("DBInstanceClass")
        if current_class == target_class:
            raise HTTPException(
                status_code=409,
                detail=f"RDS DB instance already uses {target_class}.",
            )

        modify_response = rds.modify_db_instance(
            DBInstanceIdentifier=context.payload.resource_id,
            DBInstanceClass=target_class,
            ApplyImmediately=apply_immediately,
        )
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="RDS Graviton migration submitted",
            details={
                "db_instance_identifier": context.payload.resource_id,
                "previous_instance_class": current_class,
                "target_instance_class": target_class,
                "apply_immediately": apply_immediately,
                "response": modify_response,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to migrate RDS instance to Graviton",
            extra={
                "check_id": context.check_id,
                "resource_id": context.payload.resource_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to migrate RDS instance to Graviton: {str(exc)}",
        )
