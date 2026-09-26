"""Base context for pricing calculations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session


@dataclass
class PricingContext:
    """Context passed to pricing handlers."""
    check_id: str
    resources: List[Dict[str, Any]]
    db: Session
    # Common pricing parameters
    savings_ratio: Optional[float] = None
    full_savings: Optional[bool] = None
    # Additional context as needed
    metadata: Optional[Dict[str, Any]] = None


HOURS_PER_MONTH = 730


def build_pricing_lookup_payload(
    resource: Dict[str, Any],
    *,
    resource_type: Optional[str] = None,
    metadata_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a pricing lookup payload without dropping resource metadata needed for CUR."""
    metadata = resource.get("metadata")
    payload_metadata = dict(metadata) if isinstance(metadata, dict) else {}
    if metadata_overrides:
        payload_metadata.update(metadata_overrides)

    return {
        "resource_type": resource_type or resource.get("resource_type"),
        "resource_id": resource.get("resource_id"),
        "region": resource.get("region"),
        "metadata": payload_metadata,
    }


def monthly_price_value(pricing: Optional[Dict[str, Any]]) -> Optional[float]:
    """Normalize a pricing payload into a monthly USD value."""
    if not pricing:
        return None

    raw_value = pricing.get("price_per_unit")
    if raw_value is None:
        return None

    try:
        price_per_unit = float(raw_value)
    except (TypeError, ValueError):
        return None

    unit = str(pricing.get("unit") or "").strip().lower()
    if not unit:
        return price_per_unit

    normalized_unit = unit.replace(" ", "").replace("-", "")
    if normalized_unit in {"month", "monthly", "mo", "gbmo", "gibmo"}:
        return price_per_unit
    if normalized_unit in {"hr", "hrs", "hour", "hours", "taskhrs"}:
        return price_per_unit * HOURS_PER_MONTH

    return price_per_unit
