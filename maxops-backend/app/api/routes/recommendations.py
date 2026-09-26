"""Read-only rightsizing recommendation endpoints."""

from __future__ import annotations

import logging

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.ec2_rightsizer import EC2Rightsizer, EC2ScopePolicy
from app.services.asg_rightsizer import ASGRightsizer
from app.services.elasticache_rightsizer import ElastiCacheRightsizer
from app.services.rds_rightsizer import RDSRightsizer
from rightsizers.ec2.ec2_rightsizer.models import PerformanceWarningPolicy
from rightsizers.elasticache.elasticache_rightsizer.models import (
    ElastiCachePerformanceWarningPolicy,
)

router = APIRouter(prefix="/recommendations", tags=["recommendations"])
logger = logging.getLogger("uvicorn.error")

_ASG_FILTER_CLASSIFICATIONS = {
    "ACTIONABLE",
    "CONDITIONAL",
    "PREVIEW",
    "DEFERRED",
    "INSUFFICIENT_DATA",
}

_RDS_FILTER_CLASSIFICATIONS = {
    "ACTIONABLE",
    "CONDITIONAL",
    "DEFERRED",
    "INSUFFICIENT_DATA",
    "NONE",
}


def _rds_classification_filter(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    if normalized not in _RDS_FILTER_CLASSIFICATIONS:
        allowed = ", ".join(sorted(_RDS_FILTER_CLASSIFICATIONS))
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported RDS classification '{value}'. Allowed: {allowed}",
        )
    return normalized


def _asg_classification_filter(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    if normalized not in _ASG_FILTER_CLASSIFICATIONS:
        allowed = ", ".join(sorted(_ASG_FILTER_CLASSIFICATIONS))
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported ASG classification '{value}'. Allowed: {allowed}",
        )
    return normalized


def _service(
    db: Session,
    network_medium_ratio: float,
    network_high_ratio: float,
    ebs_medium_ratio: float,
    ebs_high_ratio: float,
    allow_unknown_instance_store_usage: bool,
) -> EC2Rightsizer:
    return EC2Rightsizer(
        db,
        warning_policy=PerformanceWarningPolicy(
            network_medium_ratio=network_medium_ratio,
            network_high_ratio=network_high_ratio,
            ebs_medium_ratio=ebs_medium_ratio,
            ebs_high_ratio=ebs_high_ratio,
        ),
        scope_policy=EC2ScopePolicy(
            allow_unknown_instance_store_usage=allow_unknown_instance_store_usage
        ),
    )


def _elasticache_service(
    db: Session,
    network_medium_ratio: float,
    network_high_ratio: float,
    memory_medium_ratio: float,
    memory_high_ratio: float,
) -> ElastiCacheRightsizer:
    return ElastiCacheRightsizer(
        db,
        warning_policy=ElastiCachePerformanceWarningPolicy(
            network_medium_ratio=network_medium_ratio,
            network_high_ratio=network_high_ratio,
            memory_medium_ratio=memory_medium_ratio,
            memory_high_ratio=memory_high_ratio,
        ),
    )


@router.get("/ec2/rightsize")
def list_ec2_rightsize_recommendations(
    account_id: str | None = None,
    region: str | None = None,
    state: str = "running",
    min_monthly_savings: float = Query(0.0, ge=0.0),
    candidate_limit: int = Query(10, ge=1, le=100),
    network_medium_ratio: float = Query(0.40, ge=0.0, le=1.0),
    network_high_ratio: float = Query(0.70, ge=0.0, le=1.0),
    ebs_medium_ratio: float = Query(0.40, ge=0.0, le=1.0),
    ebs_high_ratio: float = Query(0.70, ge=0.0, le=1.0),
    allow_unknown_instance_store_usage: bool = False,
    db: Session = Depends(get_db),
):
    """Evaluate persisted EC2 inventory using scan telemetry and local pricing."""
    return _service(
        db,
        network_medium_ratio,
        network_high_ratio,
        ebs_medium_ratio,
        ebs_high_ratio,
        allow_unknown_instance_store_usage,
    ).list_recommendations(
        account_id=account_id,
        region=region,
        state=state,
        min_monthly_savings=min_monthly_savings,
        candidate_limit=candidate_limit,
    )


@router.get("/ec2/rightsize/{inventory_id}")
def get_ec2_rightsize_recommendation(
    inventory_id: int,
    min_monthly_savings: float = Query(0.0, ge=0.0),
    candidate_limit: int = Query(10, ge=1, le=100),
    network_medium_ratio: float = Query(0.40, ge=0.0, le=1.0),
    network_high_ratio: float = Query(0.70, ge=0.0, le=1.0),
    ebs_medium_ratio: float = Query(0.40, ge=0.0, le=1.0),
    ebs_high_ratio: float = Query(0.70, ge=0.0, le=1.0),
    allow_unknown_instance_store_usage: bool = False,
    db: Session = Depends(get_db),
):
    result = _service(
        db,
        network_medium_ratio,
        network_high_ratio,
        ebs_medium_ratio,
        ebs_high_ratio,
        allow_unknown_instance_store_usage,
    ).get_recommendation(
        inventory_id,
        min_monthly_savings=min_monthly_savings,
        candidate_limit=candidate_limit,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="EC2 inventory resource not found")
    return result


@router.get("/ec2/rightsize/{inventory_id}/trend")
def get_ec2_rightsize_confidence_trend(
    inventory_id: int,
    db: Session = Depends(get_db),
):
    try:
        result = EC2Rightsizer(db).get_confidence_trend(inventory_id)
    except (BotoCoreError, ClientError) as exc:
        logger.warning(
            "EC2 confidence trend lookup failed for %s: %s", inventory_id, exc
        )
        raise HTTPException(
            status_code=502, detail="Unable to retrieve EC2 confidence trend"
        ) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="EC2 inventory resource not found")
    return result


@router.get("/asg/rightsize")
def list_asg_rightsize_recommendations(
    account_id: str | None = None,
    region: str | None = None,
    state: str = "active",
    classification: str | None = None,
    min_monthly_savings: float = Query(0.0, ge=0.0),
    candidate_limit: int | None = Query(None, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return ASGRightsizer(db).list_recommendations(
        account_id=account_id,
        region=region,
        state=state,
        classification=_asg_classification_filter(classification),
        min_monthly_savings=min_monthly_savings,
        candidate_limit=candidate_limit,
    )


@router.get("/asg/rightsize/{inventory_id}")
def get_asg_rightsize_recommendation(
    inventory_id: int,
    min_monthly_savings: float = Query(0.0, ge=0.0),
    candidate_limit: int | None = Query(None, ge=1, le=100),
    db: Session = Depends(get_db),
):
    result = ASGRightsizer(db).get_recommendation(
        inventory_id,
        min_monthly_savings=min_monthly_savings,
        candidate_limit=candidate_limit,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="ASG inventory resource not found")
    return result


@router.get("/asg/rightsize/{inventory_id}/trend")
def get_asg_rightsize_confidence_trend(
    inventory_id: int,
    db: Session = Depends(get_db),
):
    try:
        result = ASGRightsizer(db).get_confidence_trend(inventory_id)
    except (BotoCoreError, ClientError) as exc:
        logger.warning("ASG confidence trend lookup failed for %s: %s", inventory_id, exc)
        raise HTTPException(
            status_code=502, detail="Unable to retrieve ASG confidence trend"
        ) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="ASG inventory resource not found")
    return result


@router.get("/elasticache/rightsize")
def list_elasticache_rightsize_recommendations(
    account_id: str | None = None,
    region: str | None = None,
    state: str = "available",
    min_monthly_savings: float = Query(0.0, ge=0.0),
    candidate_limit: int = Query(10, ge=1, le=100),
    network_medium_ratio: float = Query(0.40, ge=0.0, le=1.0),
    network_high_ratio: float = Query(0.70, ge=0.0, le=1.0),
    memory_medium_ratio: float = Query(0.40, ge=0.0, le=1.0),
    memory_high_ratio: float = Query(0.70, ge=0.0, le=1.0),
    db: Session = Depends(get_db),
):
    """Evaluate persisted Redis/Valkey inventory without applying changes."""
    return _elasticache_service(
        db,
        network_medium_ratio,
        network_high_ratio,
        memory_medium_ratio,
        memory_high_ratio,
    ).list_recommendations(
        account_id=account_id,
        region=region,
        state=state,
        min_monthly_savings=min_monthly_savings,
        candidate_limit=candidate_limit,
    )


@router.get("/elasticache/rightsize/{inventory_id}")
def get_elasticache_rightsize_recommendation(
    inventory_id: int,
    min_monthly_savings: float = Query(0.0, ge=0.0),
    candidate_limit: int = Query(10, ge=1, le=100),
    network_medium_ratio: float = Query(0.40, ge=0.0, le=1.0),
    network_high_ratio: float = Query(0.70, ge=0.0, le=1.0),
    memory_medium_ratio: float = Query(0.40, ge=0.0, le=1.0),
    memory_high_ratio: float = Query(0.70, ge=0.0, le=1.0),
    db: Session = Depends(get_db),
):
    result = _elasticache_service(
        db,
        network_medium_ratio,
        network_high_ratio,
        memory_medium_ratio,
        memory_high_ratio,
    ).get_recommendation(
        inventory_id,
        min_monthly_savings=min_monthly_savings,
        candidate_limit=candidate_limit,
    )
    if result is None:
        raise HTTPException(
            status_code=404, detail="ElastiCache inventory resource not found"
        )
    return result


@router.get("/elasticache/rightsize/{inventory_id}/trend")
def get_elasticache_rightsize_confidence_trend(
    inventory_id: int,
    db: Session = Depends(get_db),
):
    try:
        result = ElastiCacheRightsizer(db).get_confidence_trend(inventory_id)
    except (BotoCoreError, ClientError) as exc:
        logger.warning(
            "ElastiCache confidence trend lookup failed for %s: %s",
            inventory_id,
            exc,
        )
        raise HTTPException(
            status_code=502,
            detail="Unable to retrieve ElastiCache confidence trend",
        ) from exc
    if result is None:
        raise HTTPException(
            status_code=404, detail="ElastiCache inventory resource not found"
        )
    return result


@router.get("/rds/rightsize")
def list_rds_rightsize_recommendations(
    account_id: str | None = None,
    region: str | None = None,
    engine: str | None = None,
    state: str = "available",
    classification: str | None = None,
    min_monthly_savings: float = Query(0.0, ge=0.0),
    candidate_limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Evaluate persisted, scan-time RDS evidence without modifying databases."""
    return RDSRightsizer(db).list_recommendations(
        account_id=account_id,
        region=region,
        engine=engine,
        state=state,
        classification=_rds_classification_filter(classification),
        min_monthly_savings=min_monthly_savings,
        candidate_limit=candidate_limit,
    )


@router.get("/rds/rightsize/{inventory_id}")
def get_rds_rightsize_recommendation(
    inventory_id: int,
    min_monthly_savings: float = Query(0.0, ge=0.0),
    candidate_limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    result = RDSRightsizer(db).get_recommendation(
        inventory_id,
        min_monthly_savings=min_monthly_savings,
        candidate_limit=candidate_limit,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="RDS inventory resource not found")
    return result


@router.get("/rds/rightsize/{inventory_id}/trend")
def get_rds_rightsize_confidence_trend(
    inventory_id: int,
    db: Session = Depends(get_db),
):
    try:
        result = RDSRightsizer(db).get_confidence_trend(inventory_id)
    except (BotoCoreError, ClientError) as exc:
        logger.warning("RDS confidence trend lookup failed for %s: %s", inventory_id, exc)
        raise HTTPException(status_code=502, detail="Unable to retrieve RDS confidence trend") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="RDS inventory resource not found")
    return result
