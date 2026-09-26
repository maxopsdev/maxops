"""Shared read-only helpers for the S3 optimizer checks."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Mapping, Optional

from app.checks.base import create_check_reason


def optimizer_result(resource: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """Return the stored optimizer result, or ``None`` for an unenriched bucket."""

    metadata = resource.get("metadata")
    result = metadata.get("s3_optimizer") if isinstance(metadata, Mapping) else None
    return result if isinstance(result, dict) else None


def stored_gb(result: Mapping[str, Any]) -> Optional[float]:
    """Sum known current bytes; return ``None`` when no class size is known."""

    breakdown = (result.get("current") or {}).get("storage_class_breakdown") or {}
    values = [item.get("bytes") for item in breakdown.values() if isinstance(item, Mapping) and item.get("bytes") is not None]
    return None if not values else sum(float(value) for value in values) / (2**30)


def _covered_dates(result: Mapping[str, Any]) -> Optional[set[str]]:
    """Read optional exact coverage membership from additive result metadata."""

    coverage = result.get("coverage") or {}
    values = coverage.get("covered_days")
    if isinstance(values, list):
        return {str(value)[:10] for value in values}
    return None


def coverage_complete(result: Mapping[str, Any], window_days: int) -> bool:
    """Return whether the strict deletion-review coverage gate is satisfied."""

    dates = _covered_dates(result)
    if dates is not None:
        return len(dates) >= window_days
    return ((result.get("coverage") or {}).get("cur_days_covered") or 0) >= window_days


def request_total(result: Mapping[str, Any], tier: str) -> float:
    """Return a raw Tier1/Tier2 count, defaulting only a known empty map to zero."""

    totals = (result.get("signals") or {}).get("window_totals") or {}
    values = (totals.get(tier) or {}).values()
    return float(sum(float(value or 0.0) for value in values))


def bucket_age_days(resource: Mapping[str, Any]) -> Optional[int]:
    """Return bucket age from metadata, or ``None`` when creation is unknown."""

    metadata = resource.get("metadata") or {}
    value = metadata.get("creation_date") or resource.get("creation_date")
    if value is None:
        return None
    if isinstance(value, datetime):
        created = value.astimezone(timezone.utc).date() if value.tzinfo else value.date()
    else:
        try:
            created = date.fromisoformat(str(value)[:10])
        except ValueError:
            return None
    return max(0, (date.today() - created).days)


def skip(resource: Mapping[str, Any], reason: str, logger: Any) -> None:
    """Log a check skip without adding a finding."""

    logger.debug("Skipping S3 optimizer bucket %s: %s", resource.get("resource_id"), reason)


def finding_reason(check_type: str, details: Mapping[str, Any]) -> str:
    """Use the repository's stable check-reason format for optimizer findings."""

    return create_check_reason(check_type, dict(details))
