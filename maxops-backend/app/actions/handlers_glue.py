"""Glue action handlers."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import HTTPException

from app.actions.base import ActionExecutionContext
from app.schemas.check import CheckActionResponse

logger = logging.getLogger("uvicorn.error")


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


def handle_glue_set_workers_count(context: ActionExecutionContext) -> CheckActionResponse:
    """Update AWS Glue job worker count (and optionally worker type)."""
    try:
        glue = context.aws_adapter.session.client("glue", region_name=context.payload.region)

        params = context.payload.parameters or {}
        job_name = (
            params.get("job_name")
            or params.get("jobName")
            or context.payload.resource_id
        )
        number_of_workers = _normalize_int(
            params.get("number_of_workers")
            or params.get("numberOfWorkers")
            or params.get("worker_count")
            or params.get("workerCount")
        )
        worker_type = params.get("worker_type") or params.get("workerType")

        if not job_name:
            raise HTTPException(
                status_code=400,
                detail="job_name is required.",
            )
        if number_of_workers is None or number_of_workers <= 0:
            raise HTTPException(
                status_code=400,
                detail="number_of_workers is required and must be a positive integer.",
            )

        # Verify job exists
        try:
            glue.get_job(JobName=job_name)
        except HTTPException:
            raise
        except Exception as verify_exc:
            raise HTTPException(
                status_code=404,
                detail=f"Failed to verify Glue job: {str(verify_exc)}",
            )

        job_update: Dict[str, Any] = {
            "NumberOfWorkers": number_of_workers,
        }
        if worker_type:
            job_update["WorkerType"] = worker_type

        response = glue.update_job(
            JobName=job_name,
            JobUpdate=job_update,
        )

        return CheckActionResponse(
            check_id=context.check_id,
            resource_id=job_name,
            action=context.payload.action,
            status="submitted",
            message="Glue job workers update submitted",
            details={
                "job_name": job_name,
                "region": context.payload.region,
                "number_of_workers": number_of_workers,
                "worker_type": worker_type,
                "response": response,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to update Glue job workers",
            extra={
                "check_id": context.check_id,
                "resource_id": context.payload.resource_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update Glue job workers: {str(exc)}",
        )


def handle_glue_upgrade_version(context: ActionExecutionContext) -> CheckActionResponse:
    """Update AWS Glue job to a specified Glue version."""
    try:
        glue = context.aws_adapter.session.client("glue", region_name=context.payload.region)

        params = context.payload.parameters or {}
        job_name = (
            params.get("job_name")
            or params.get("jobName")
            or context.payload.resource_id
        )
        glue_version = params.get("glue_version") or params.get("glueVersion")

        if not job_name:
            raise HTTPException(
                status_code=400,
                detail="job_name is required.",
            )
        if not glue_version:
            raise HTTPException(
                status_code=400,
                detail="glue_version is required.",
            )

        # Verify job exists
        try:
            glue.get_job(JobName=job_name)
        except HTTPException:
            raise
        except Exception as verify_exc:
            raise HTTPException(
                status_code=404,
                detail=f"Failed to verify Glue job: {str(verify_exc)}",
            )

        update_params: Dict[str, Any] = {
            "JobName": job_name,
            "JobUpdate": {
                "GlueVersion": glue_version,
            },
        }

        response = glue.update_job(**update_params)

        return CheckActionResponse(
            check_id=context.check_id,
            resource_id=job_name,
            action=context.payload.action,
            status="submitted",
            message="Glue job version update submitted",
            details={
                "job_name": job_name,
                "region": context.payload.region,
                "glue_version": glue_version,
                "response": response,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to update Glue job version",
            extra={
                "check_id": context.check_id,
                "resource_id": context.payload.resource_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update Glue job version: {str(exc)}",
        )
