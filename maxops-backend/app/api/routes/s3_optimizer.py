"""Stored S3 optimizer result routes."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.s3_optimizer import get_bucket_detail, list_bucket_summaries
from pricing.cur.datasets import DEFAULT_CACHE_ROOT


router = APIRouter(prefix="/s3-optimizer", tags=["s3-optimizer"])


@router.get("")
def list_s3_optimizer_buckets(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    region: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List persisted S3 optimizer summaries."""

    return list_bucket_summaries(db, limit=limit, offset=offset, region=region)


@router.get("/{inventory_id}")
def get_s3_optimizer_detail(
    inventory_id: int,
    cache_root: str = Query(default=str(DEFAULT_CACHE_ROOT)),
    db: Session = Depends(get_db),
):
    """Return the stored optimizer result, including valid unknown telemetry."""

    result = get_bucket_detail(db, inventory_id, cache_root)
    if result is None:
        raise HTTPException(status_code=404, detail="S3 inventory row not found")
    return result
