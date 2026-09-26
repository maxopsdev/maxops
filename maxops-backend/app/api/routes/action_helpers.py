"""API routes and shared helpers for action execution."""
import logging
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Any, Optional
from sqlalchemy.orm import Session

from app.adapters.aws.adapter import AWSAdapter
from app.config import settings
from app.actions import action_registry
from app.actions.action_mapping import resolve_action_key
from app.actions.base import ActionExecutionContext
from app.database import get_db
from app.models.action_execution import ActionExecution
from app.schemas.check import CheckActionRequest, CheckActionResponse
from app.services.settings_service import is_action_enabled
from app.services.cloudwatch_agent_service import (
    CloudWatchAgentError,
    ElevatedRoleRequiredError,
    install_or_configure_cloudwatch_agent,
    validate_cloudwatch_agent,
)
from app.utils.settings_guard import require_account_region, get_settings_scope

router = APIRouter()
logger = logging.getLogger("uvicorn.error")


def execute_action_for_check(
    check_id: str,
    payload: CheckActionRequest,
    check: Any,
    db: Session,
) -> CheckActionResponse:
    """Run a registered action and record its lifecycle in ``action_executions``.

    ``check_id`` is provenance stored on the execution row. The caller must
    perform any registry validation appropriate to its route before calling
    this helper; the rightsizer route intentionally supplies a provenance
    label that is not a check registry key.
    """
    existing_action = (
        db.query(ActionExecution)
        .filter(
            ActionExecution.check_id == check_id,
            ActionExecution.resource_id == payload.resource_id,
            ActionExecution.status.in_(["running", "submitted"]),
        )
        .order_by(ActionExecution.started_at.desc())
        .first()
    )

    if existing_action:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Action '{existing_action.action}' is already running for resource "
                f"{payload.resource_id}. Started at {existing_action.started_at}. "
                "Please wait for it to complete."
            ),
        )

    logger.info(
        "Executing action %s for resource %s (account=%s region=%s) profile=%s role_arn=%s iam_role=%s",
        payload.action,
        payload.resource_id,
        payload.account_id,
        payload.region,
        settings.aws_profile,
        settings.aws_role_arn,
        settings.aws_use_iam_role,
    )

    action_execution = ActionExecution(
        check_id=check_id,
        resource_id=payload.resource_id,
        action=payload.action,
        status="running",
        account_id=payload.account_id,
        region=payload.region,
        parameters_json=payload.parameters,
    )
    db.add(action_execution)
    db.commit()
    db.refresh(action_execution)

    try:
        raw_action = payload.action.strip().lower()
        action_key = resolve_action_key(raw_action, check_id)

        # Nothing may touch a real AWS resource unless the action is enabled in
        # Settings and MAXOPS_ENABLE_ACTIONS is set. This gate lived inline in
        # the checks route before this call was extracted; it has to travel
        # with the execution path, not stay behind in the caller.
        if not is_action_enabled(db, action_key):
            action_execution.status = "failed"
            action_execution.error_message = (
                f"Action '{action_key}' is disabled. Enable it in Settings, and make sure "
                "MAXOPS_ENABLE_ACTIONS is set, before running this action."
            )
            db.commit()
            raise HTTPException(status_code=403, detail=action_execution.error_message)

        aws_adapter = AWSAdapter(default_region=payload.region)
        factory_context = ActionExecutionContext(
            check_id=check_id,
            action_key=action_key,
            payload=payload,
            check=check,
            aws_adapter=aws_adapter,
            db=db,
            action_execution=action_execution,
        )
        factory_response = action_registry.execute(factory_context)
        if factory_response is not None:
            return factory_response

        action_execution.status = "failed"
        action_execution.error_message = f"Unsupported action '{payload.action}'"
        db.commit()
        raise HTTPException(
            status_code=400, detail=f"Unsupported action '{payload.action}'"
        )
    except HTTPException:
        action_execution.status = "failed"
        db.commit()
        raise
    except Exception as exc:
        logger.exception(
            "Unexpected error executing action %s for resource %s",
            payload.action,
            payload.resource_id,
        )
        action_execution.status = "failed"
        action_execution.error_message = str(exc)
        db.commit()
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error executing action: {str(exc)}",
        )


@router.get("/actions/rds/{resource_id}/instance-classes")
def get_rds_instance_classes(
    resource_id: str,
    region: str = Query(..., description="AWS region"),
):
    """
    Get available RDS Graviton instance classes for a given RDS instance.
    
    Used by the UI to populate instance class options when executing
    "Migrate to Graviton" action.
    """
    try:
        aws_adapter = AWSAdapter(default_region=region)
        rds = aws_adapter.session.client("rds", region_name=region)
        response = rds.describe_db_instances(DBInstanceIdentifier=resource_id)
        db_instance = response["DBInstances"][0]
        engine = db_instance.get("Engine")
        engine_version = db_instance.get("EngineVersion")
        license_model = db_instance.get("LicenseModel")
        current_class = db_instance.get("DBInstanceClass")

        if not engine or not engine_version:
            raise HTTPException(status_code=400, detail="Unable to determine RDS engine or version.")

        def _fetch_options(**kwargs):
            paginator = rds.get_paginator("describe_orderable_db_instance_options")
            collected = []
            for page in paginator.paginate(**kwargs):
                collected.extend(page.get("OrderableDBInstanceOptions", []))
            return collected

        options_kwargs = {"Engine": engine, "EngineVersion": engine_version}
        if license_model:
            options_kwargs["LicenseModel"] = license_model

        options = _fetch_options(**options_kwargs)
        if not options:
            # Retry without EngineVersion (some engines return no results with exact version)
            fallback_kwargs = {"Engine": engine}
            if license_model:
                fallback_kwargs["LicenseModel"] = license_model
            options = _fetch_options(**fallback_kwargs)
        if not options and license_model:
            # Retry without LicenseModel if still empty
            options = _fetch_options(Engine=engine)

        classes = {
            option.get("DBInstanceClass")
            for option in options
            if option.get("DBInstanceClass")
        }
        logger.info(
            "RDS orderable classes for %s (engine=%s version=%s license=%s): %s",
            resource_id,
            engine,
            engine_version,
            license_model,
            sorted(classes),
        )
        graviton_classes = sorted([cls for cls in classes if "g." in cls], key=str.lower)
        if not graviton_classes:
            # Fallback: derive from existing instances in the account/region
            all_instances = rds.describe_db_instances().get("DBInstances", [])
            instance_classes = {
                inst.get("DBInstanceClass")
                for inst in all_instances
                if inst.get("DBInstanceClass")
            }
            logger.info(
                "RDS instance classes in account/region for %s: %s",
                resource_id,
                sorted(instance_classes),
            )
            graviton_classes = sorted([cls for cls in instance_classes if "g." in cls], key=str.lower)

        return {
            "current_instance_class": current_class,
            "graviton_instance_classes": graviton_classes,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to load RDS instance class options",
            extra={
                "resource_id": resource_id,
                "region": region,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load RDS instance class options: {str(exc)}"
        )


@router.get("/actions/ec2/{resource_id}/cloudwatch-agent")
def get_cloudwatch_agent_status(
    resource_id: str,
    region: Optional[str] = Query(None, description="Region for the instance"),
    elevated_role_arn: Optional[str] = Query(None, description="Optional elevated role ARN for IAM inspection"),
    db: Session = Depends(get_db)
):
    """
    Get CloudWatch agent status for an EC2 instance.
    
    Used by the UI to check if CloudWatch agent is installed before
    showing the "Install CloudWatch Agent" action.
    """
    try:
        user_settings = require_account_region(db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    _, active_region = get_settings_scope(user_settings)
    query_region = region or active_region
    aws_adapter = AWSAdapter(default_region=query_region)
    try:
        return validate_cloudwatch_agent(
            session=aws_adapter.session,
            region=query_region,
            instance_id=resource_id,
            elevated_role_arn=elevated_role_arn,
        )
    except ElevatedRoleRequiredError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "details": exc.details})
    except CloudWatchAgentError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/actions/ec2/{resource_id}/cloudwatch-agent/install")
def install_cloudwatch_agent(
    resource_id: str,
    region: Optional[str] = Query(None, description="Region for the instance"),
    elevated_role_arn: Optional[str] = Query(None, description="Optional elevated role ARN for IAM remediation"),
    db: Session = Depends(get_db)
):
    """
    Trigger CloudWatch agent installation for an EC2 instance.
    
    Used by the UI to install CloudWatch agent on EC2 instances.
    This is an EC2-specific action helper.
    """
    try:
        user_settings = require_account_region(db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    _, active_region = get_settings_scope(user_settings)
    query_region = region or active_region
    aws_adapter = AWSAdapter(default_region=query_region)

    try:
        details = install_or_configure_cloudwatch_agent(
            session=aws_adapter.session,
            region=query_region,
            instance_id=resource_id,
            elevated_role_arn=elevated_role_arn,
        )
        return {
            "resource_id": resource_id,
            "installed": bool(details.get("agent_installed")),
            "status": details.get("validation_status"),
            "message": (
                "CloudWatch agent installed and configured"
                if details.get("installed_during_action")
                else "CloudWatch agent configuration updated"
            ),
            "details": details,
        }
    except ElevatedRoleRequiredError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "details": exc.details})
    except CloudWatchAgentError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/actions/{check_id}/{resource_id}/status")
def get_action_status(
    check_id: str,
    resource_id: str,
    db: Session = Depends(get_db),
):
    """
    Get the status of the latest action execution for a resource.
    
    Used by the UI to poll for action completion status.
    """
    action_execution = (
        db.query(ActionExecution)
        .filter(
            ActionExecution.check_id == check_id,
            ActionExecution.resource_id == resource_id,
            ActionExecution.status.in_(["running", "submitted"]),
        )
        .order_by(ActionExecution.started_at.desc())
        .first()
    )
    
    if not action_execution:
        return {"status": "none", "action": None}
    
    return {
        "status": action_execution.status,
        "action": action_execution.action,
        "message": action_execution.message,
        "details": action_execution.details_json,
        "started_at": action_execution.started_at.isoformat() if action_execution.started_at else None,
        "requires_polling": action_execution.requires_polling,
        "polling_identifier": action_execution.polling_identifier,
    }
