"""EBS snapshot pricing handler."""
from __future__ import annotations

from app.pricing.base import PricingContext, build_pricing_lookup_payload
from app.services.aws_pricing_cache import AwsPricingCacheService


def handle_snapshot_pricing(context: PricingContext) -> None:
    """Apply EBS snapshot pricing and savings."""
    pricing_service = AwsPricingCacheService()
    full_savings = context.full_savings if context.full_savings is not None else True
    
    for resource in context.resources:
        metadata = resource.get("metadata") or {}
        size_gb = (
            metadata.get("size")
            or metadata.get("Size")
            or metadata.get("size_gb")
            or metadata.get("VolumeSize")
        )
        if size_gb is None:
            continue
        try:
            size_gb = float(size_gb)
        except (TypeError, ValueError):
            continue

        pricing_payload = build_pricing_lookup_payload(resource, resource_type="snapshot")
        price = pricing_service.get_price_for_resource(context.db, pricing_payload)
        if not price:
            continue

        resource["pricing"] = price
        metadata["pricing"] = price
        unit_price = price.get("price_per_unit")
        if unit_price is None:
            continue
        estimated_monthly = unit_price * size_gb
        metadata["estimated_monthly_cost"] = round(estimated_monthly, 4)
        savings_monthly = estimated_monthly if full_savings else 0
        metadata["potential_savings_monthly"] = round(savings_monthly, 4)
        metadata["potential_savings_yearly"] = round(savings_monthly * 12, 2)
