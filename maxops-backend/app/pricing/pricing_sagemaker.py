"""SageMaker pricing and savings application."""

from __future__ import annotations

from app.pricing.base import PricingContext, build_pricing_lookup_payload, monthly_price_value
from app.services.aws_pricing_cache import AwsPricingCacheService


def handle_sagemaker_pricing(context: PricingContext) -> None:
    """Apply resolved SageMaker prices to the check's already chosen savings formula."""
    pricing_service = AwsPricingCacheService()
    for resource in context.resources:
        metadata = resource.get("metadata") or {}
        instance_type = metadata.get("instance_type") or resource.get("instance_type")
        if not instance_type:
            continue
        payload = build_pricing_lookup_payload(
            resource,
            resource_type="sagemaker",
            metadata_overrides={"instance_type": instance_type},
        )
        price = pricing_service.get_price_for_resource(context.db, payload)
        if not price:
            continue
        resource["pricing"] = price
        metadata["pricing"] = price
        monthly = monthly_price_value(price)
        if monthly is None:
            continue
        metadata["hourly_price"] = monthly / 730.0
        # Checks store a basis and this handler supplies the resolved rate.
        basis = metadata.get("sagemaker_savings_basis")
        if basis is not None:
            savings = float(basis) * (monthly / 730.0)
            metadata["potential_savings_monthly"] = round(savings, 2)
            metadata["potential_savings_yearly"] = round(savings * 12, 2)

