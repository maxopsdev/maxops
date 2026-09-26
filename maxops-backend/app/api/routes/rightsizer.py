"""Rightsizer hub and evidence endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.routes.action_helpers import execute_action_for_check
from app.schemas.check import CheckActionRequest, CheckActionResponse
from app.services.rightsizer_service import (
    get_rightsizer_detail,
    list_rightsizer_resource_types,
    list_rightsizer_resources,
)

router = APIRouter(prefix="/rightsizer", tags=["rightsizer"])


class RightsizerApplyRequest(CheckActionRequest):
    """Request body for applying an EC2 rightsizer recommendation."""

    action: str = "rightsize"
    target_instance_type: str = Field(..., min_length=1)
    recommendation_option: Optional[str] = None


EC2_RIGHTSIZER_ACTION_CHECK_ID = "ec2_rightsizer"
"""Label stored in ``action_executions``; this is not a check registry key."""


@router.get("/resource-types")
def get_resource_types(db: Session = Depends(get_db)):
    return list_rightsizer_resource_types(db)


@router.get("/resources/{resource_type}")
def get_resources(
    resource_type: str,
    q: str | None = None,
    region: str | None = None,
    state: str | None = None,
    status: str | None = None,
    current_type: str | None = None,
    target_type: str | None = None,
    db: Session = Depends(get_db),
):
    try:
        return list_rightsizer_resources(
            resource_type,
            db,
            q=q,
            region=region,
            state=state,
            status=status,
            current_type=current_type,
            target_type=target_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/resources/{resource_type}/{inventory_id}")
def get_resource_detail(
    resource_type: str,
    inventory_id: int,
    db: Session = Depends(get_db),
):
    try:
        return get_rightsizer_detail(resource_type, inventory_id, db)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/resources/ec2/{inventory_id}/apply",
    response_model=CheckActionResponse,
)
def apply_ec2_recommendation(
    inventory_id: int,
    payload: RightsizerApplyRequest,
    db: Session = Depends(get_db),
):
    """Apply the selected EC2 recommendation through the shared action runner.

    ``inventory_id`` identifies the recommendation page that initiated the
    request. The action handler still receives the explicit resource identity
    from the body, matching the existing action contract.
    """
    del inventory_id
    action_payload = CheckActionRequest(
        action="rightsize",
        account_id=payload.account_id,
        region=payload.region,
        resource_id=payload.resource_id,
        parameters={
            "target_instance_type": payload.target_instance_type,
            "recommendation_option": payload.recommendation_option,
        },
    )
    return execute_action_for_check(
        EC2_RIGHTSIZER_ACTION_CHECK_ID,
        action_payload,
        None,
        db,
    )
