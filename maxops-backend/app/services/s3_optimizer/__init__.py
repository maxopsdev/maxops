"""Offline S3 storage-class optimizer."""

from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.inventory import S3Inventory

from .engine import evaluate_bucket


def get_bucket_detail(db: Session, inventory_id: int, cache_root: str = "") -> Optional[dict[str, Any]]:
    """Return the stored optimizer result for one inventory row, or ``None``.

    ``cache_root`` remains in the signature for the API's CUR route convention;
    V1 serves the frozen scan result and never recomputes from the cache.
    """

    row = db.query(S3Inventory).filter(S3Inventory.inventory_id == inventory_id).first()
    if row is None:
        return None
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    result = metadata.get("s3_optimizer")
    return result if isinstance(result, dict) else {
        "inventory_id": row.inventory_id,
        "resource_id": row.resource_id,
        "region": row.region,
        "telemetry_status": "unknown",
        "error": "s3 optimizer result is not available",
    }


def list_bucket_summaries(
    db: Session,
    *,
    limit: int = 100,
    offset: int = 0,
    region: Optional[str] = None,
) -> dict[str, Any]:
    """Return paginated stored optimizer summaries without recomputation."""

    query = db.query(S3Inventory)
    if region is not None:
        query = query.filter(S3Inventory.region == region)
    total = query.count()
    rows = query.order_by(S3Inventory.inventory_id).offset(max(offset, 0)).limit(max(limit, 1)).all()
    items = []
    for row in rows:
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        result = metadata.get("s3_optimizer") if isinstance(metadata.get("s3_optimizer"), dict) else {}
        pattern = result.get("pattern") or {}
        recommendation = result.get("recommendation") or {}
        items.append(
            {
                "inventory_id": row.inventory_id,
                "resource_id": row.resource_id,
                "region": row.region,
                "telemetry_status": result.get("telemetry_status", "unknown"),
                "confidence": result.get("confidence"),
                "pattern_label": pattern.get("pattern_label"),
                "recommendation": recommendation,
            }
        )
    return {"items": items, "total": total, "limit": max(limit, 1), "offset": max(offset, 0)}

__all__ = ["evaluate_bucket", "get_bucket_detail", "list_bucket_summaries"]
