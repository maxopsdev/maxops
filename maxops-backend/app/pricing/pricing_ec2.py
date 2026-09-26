"""EC2 pricing handler."""
from __future__ import annotations

from app.pricing.base import PricingContext, build_pricing_lookup_payload, monthly_price_value
from app.services.aws_pricing_cache import AwsPricingCacheService


def handle_ec2_pricing(context: PricingContext) -> None:
    """Apply EC2 pricing and calculate savings."""
    pricing_service = AwsPricingCacheService()
    savings_ratio = context.savings_ratio or 0.7  # Default 70% savings
    
    for resource in context.resources:
        metadata = resource.get("metadata") or {}
        instance_type = metadata.get("instance_type") or metadata.get("InstanceType")
        if not instance_type:
            continue
        pricing_payload = build_pricing_lookup_payload(
            resource,
            metadata_overrides={"InstanceType": instance_type},
        )
        current_price = pricing_service.get_price_for_resource(context.db, pricing_payload)
        if not current_price:
            continue
        resource["pricing"] = current_price
        metadata["pricing"] = current_price
        monthly = monthly_price_value(current_price)
        if monthly is None:
            continue
        savings_monthly = monthly * savings_ratio
        metadata["estimated_monthly_cost"] = monthly
        metadata["potential_savings_monthly"] = round(savings_monthly, 2)
        metadata["potential_savings_yearly"] = round(savings_monthly * 12, 2)
