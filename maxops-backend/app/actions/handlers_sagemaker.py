"""SageMaker V1 action handlers."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from botocore.exceptions import ClientError

from app.actions.base import ActionExecutionContext
from app.schemas.check import CheckActionResponse

logger = logging.getLogger("uvicorn.error")


def _client(context: ActionExecutionContext) -> Any:
    """Return the regional SageMaker client from the action's adapter."""
    return context.aws_adapter.session.client("sagemaker", region_name=context.payload.region)


def handle_sagemaker_stop_notebook(context: ActionExecutionContext) -> CheckActionResponse:
    """Stop a notebook and treat an already-stopped notebook as idempotent."""
    try:
        client = _client(context)
        name = context.payload.resource_id
        parameters = context.payload.parameters or {}
        status = str(parameters.get("notebook_status") or parameters.get("status") or "").casefold()
        if status == "stopped":
            return CheckActionResponse(
                check_id=context.check_id,
                action=context.payload.action,
                status="submitted",
                message="Notebook is already stopped; no action was needed.",
                details={"notebook_instance_name": name, "idempotent": True},
            )
        response = client.stop_notebook_instance(NotebookInstanceName=name)
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="SageMaker notebook stop request submitted.",
            details=response,
        )
    except ClientError as exc:
        error = exc.response.get("Error", {})
        if error.get("Code") == "ValidationException" and "stopped" in str(error.get("Message") or exc).casefold():
            return CheckActionResponse(
                check_id=context.check_id,
                action=context.payload.action,
                status="submitted",
                message="Notebook is already stopped; no action was needed.",
                details={"notebook_instance_name": context.payload.resource_id, "idempotent": True},
            )
        logger.exception("Failed to stop SageMaker notebook", extra={"resource_id": context.payload.resource_id})
        raise HTTPException(status_code=500, detail=f"Failed to stop SageMaker notebook: {exc}")
    except Exception as exc:
        logger.exception("Failed to stop SageMaker notebook", extra={"resource_id": context.payload.resource_id})
        raise HTTPException(status_code=500, detail=f"Failed to stop SageMaker notebook: {exc}")


def handle_sagemaker_delete_endpoint(context: ActionExecutionContext) -> CheckActionResponse:
    """Delete only an endpoint; shared models and endpoint configs are retained."""
    try:
        response = _client(context).delete_endpoint(EndpointName=context.payload.resource_id)
        return CheckActionResponse(
            check_id=context.check_id,
            action=context.payload.action,
            status="submitted",
            message="SageMaker endpoint delete request submitted. The endpoint config and model were retained because they may be shared.",
            details=response,
        )
    except Exception as exc:
        logger.exception("Failed to delete SageMaker endpoint", extra={"resource_id": context.payload.resource_id})
        raise HTTPException(status_code=500, detail=f"Failed to delete SageMaker endpoint: {exc}")
