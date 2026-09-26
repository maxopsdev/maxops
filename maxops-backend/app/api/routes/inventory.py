"""Inventory overview routes."""
from datetime import datetime, timezone
from math import ceil
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.settings import ResourceExemption, ResourceSnoozeAudit
from app.services.inventory_service import (
    get_asg_inventory_overview,
    get_dynamodb_inventory_overview,
    get_ebs_inventory_overview,
    get_ec2_inventory_overview,
    get_elasticache_inventory_overview,
    get_rds_inventory_overview,
    get_s3_inventory_overview,
)
from app.utils.resource_snooze import (
    GLOBAL_RESOURCE_SNOOZE_CHECK_ID,
    as_aware_utc,
    get_global_snooze_map,
    serialize_snooze,
)

router = APIRouter(prefix="/inventory", tags=["inventory"])


class SnoozeResourceRequest(BaseModel):
    resource_id: str = Field(..., min_length=1)
    resource_type: str = Field(..., min_length=1)
    resource_name: Optional[str] = None
    account_id: Optional[str] = None
    region: Optional[str] = None


class SnoozeResourcesRequest(BaseModel):
    resources: List[SnoozeResourceRequest] = Field(..., min_items=1)
    snoozed_until: datetime
    reason: str = Field(..., min_length=1, max_length=2000)


class RemoveSnoozeResourcesRequest(BaseModel):
    resources: List[SnoozeResourceRequest] = Field(..., min_items=1)
    reason: str = Field(..., min_length=1, max_length=2000)


def _annotate_global_snoozes(response: Dict[str, Any], collection_key: str, db: Session) -> Dict[str, Any]:
    resources = response.get(collection_key) or []
    snoozes = get_global_snooze_map(db, [str(resource.get("resource_id") or "") for resource in resources])
    for resource in resources:
        resource["snooze"] = serialize_snooze(snoozes.get(str(resource.get("resource_id") or "")))
    return response


@router.get("/ec2/overview")
def ec2_inventory_overview(db: Session = Depends(get_db)):
    """Return imported EC2 inventory data prepared for the resource-specific overview UI."""
    return _annotate_global_snoozes(get_ec2_inventory_overview(db), "instances", db)


@router.get("/s3/overview")
def s3_inventory_overview(db: Session = Depends(get_db)):
    """Return imported S3 inventory data prepared for the resource-specific overview UI."""
    return _annotate_global_snoozes(get_s3_inventory_overview(db), "buckets", db)


@router.get("/rds/overview")
def rds_inventory_overview(db: Session = Depends(get_db)):
    """Return imported RDS inventory data prepared for the resource-specific overview UI."""
    return _annotate_global_snoozes(get_rds_inventory_overview(db), "instances", db)


@router.get("/elasticache/overview")
def elasticache_inventory_overview(db: Session = Depends(get_db)):
    """Return imported ElastiCache inventory data prepared for the resource-specific overview UI."""
    return _annotate_global_snoozes(get_elasticache_inventory_overview(db), "resources", db)


@router.get("/asg/overview")
def asg_inventory_overview(db: Session = Depends(get_db)):
    """Return imported ASG inventory data prepared for the resource-specific overview UI."""
    return _annotate_global_snoozes(get_asg_inventory_overview(db), "resources", db)


@router.get("/dynamodb/overview")
def dynamodb_inventory_overview(db: Session = Depends(get_db)):
    """Return imported DynamoDB inventory data prepared for the resource-specific overview UI."""
    return _annotate_global_snoozes(get_dynamodb_inventory_overview(db), "resources", db)


@router.get("/ebs/overview")
def ebs_inventory_overview(db: Session = Depends(get_db)):
    """Return imported EBS inventory data prepared for the resource-specific overview UI."""
    return _annotate_global_snoozes(get_ebs_inventory_overview(db), "volumes", db)


@router.post("/resources/snooze")
def snooze_inventory_resources(payload: SnoozeResourcesRequest, db: Session = Depends(get_db)):
    """Globally snooze inventory resources so future checks exclude them."""
    snoozed_until = as_aware_utc(payload.snoozed_until)
    now = datetime.now(timezone.utc)
    if not snoozed_until or snoozed_until <= now:
        raise HTTPException(status_code=400, detail="Snooze expiration must be in the future")

    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Snooze reason is required")

    days = max(1, ceil((snoozed_until - now).total_seconds() / 86400))
    saved = []
    seen_ids = set()
    for resource in payload.resources:
        if resource.resource_id in seen_ids:
            continue
        seen_ids.add(resource.resource_id)
        exemption = (
            db.query(ResourceExemption)
            .filter(
                ResourceExemption.check_id == GLOBAL_RESOURCE_SNOOZE_CHECK_ID,
                ResourceExemption.resource_id == resource.resource_id,
            )
            .first()
        )
        previous_until = exemption.snoozed_until if exemption else None
        previous_reason = exemption.snooze_reason if exemption else None
        action = "updated" if previous_until else "created"

        if exemption is None:
            exemption = ResourceExemption(
                check_id=GLOBAL_RESOURCE_SNOOZE_CHECK_ID,
                resource_id=resource.resource_id,
                resource_type=resource.resource_type,
                exempted=False,
            )
            db.add(exemption)

        exemption.resource_type = resource.resource_type
        exemption.exempted = False
        exemption.snoozed_until = snoozed_until
        exemption.snooze_days = days
        exemption.snooze_reason = reason
        exemption.updated_at = now
        db.add(
            ResourceSnoozeAudit(
                action=action,
                resource_id=resource.resource_id,
                resource_type=resource.resource_type,
                resource_name=resource.resource_name,
                account_id=resource.account_id,
                region=resource.region,
                previous_snoozed_until=previous_until,
                new_snoozed_until=snoozed_until,
                previous_reason=previous_reason,
                new_reason=reason,
                actor="local-user",
                metadata_json={"check_id": GLOBAL_RESOURCE_SNOOZE_CHECK_ID},
            )
        )
        saved.append(exemption)

    db.commit()
    for exemption in saved:
        db.refresh(exemption)

    return {
        "resources": [serialize_snooze(exemption) for exemption in saved],
        "message": f"Snoozed {len(saved)} resource{'s' if len(saved) != 1 else ''}",
    }


@router.post("/resources/snooze/remove")
def remove_inventory_resource_snoozes(payload: RemoveSnoozeResourcesRequest, db: Session = Depends(get_db)):
    """Remove global snoozes for inventory resources and audit the change."""
    now = datetime.now(timezone.utc)
    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Audit comment is required")
    updated = []
    seen_ids = set()
    for resource in payload.resources:
        if resource.resource_id in seen_ids:
            continue
        seen_ids.add(resource.resource_id)
        exemption = (
            db.query(ResourceExemption)
            .filter(
                ResourceExemption.check_id == GLOBAL_RESOURCE_SNOOZE_CHECK_ID,
                ResourceExemption.resource_id == resource.resource_id,
            )
            .first()
        )
        if exemption is None or not exemption.snoozed_until:
            continue

        previous_until = exemption.snoozed_until
        previous_reason = exemption.snooze_reason
        exemption.snoozed_until = None
        exemption.snooze_days = None
        exemption.snooze_reason = None
        exemption.updated_at = now
        db.add(
            ResourceSnoozeAudit(
                action="removed",
                resource_id=resource.resource_id,
                resource_type=resource.resource_type or exemption.resource_type,
                resource_name=resource.resource_name,
                account_id=resource.account_id,
                region=resource.region,
                previous_snoozed_until=previous_until,
                new_snoozed_until=None,
                previous_reason=previous_reason,
                new_reason=reason,
                actor="local-user",
                metadata_json={"check_id": GLOBAL_RESOURCE_SNOOZE_CHECK_ID},
            )
        )
        updated.append(exemption)

    db.commit()
    for exemption in updated:
        db.refresh(exemption)

    return {
        "resources": [serialize_snooze(exemption) for exemption in updated],
        "message": f"Removed snooze from {len(updated)} resource{'s' if len(updated) != 1 else ''}",
    }
