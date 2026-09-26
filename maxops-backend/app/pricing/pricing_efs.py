"""EFS pricing handler."""
from __future__ import annotations

import logging

from app.pricing.base import PricingContext, build_pricing_lookup_payload
from app.services.aws_pricing_cache import AwsPricingCacheService

logger = logging.getLogger("uvicorn.error")


def handle_efs_pricing(context: PricingContext) -> None:
    """Apply EFS pricing and calculate potential savings."""
    full_savings = context.full_savings or False
    savings_ratio = context.savings_ratio or 1.0
    
    logger.info(
        "Applying EFS pricing to %s resources (full_savings=%s, savings_ratio=%s)",
        len(context.resources),
        full_savings,
        savings_ratio,
    )
    pricing_service = AwsPricingCacheService()
    for resource in context.resources:
        metadata = resource.get("metadata") or {}
        size_gb = metadata.get("size_gb")
        if size_gb is None or size_gb == 0:
            size_in_bytes = metadata.get("SizeInBytes", {})
            if isinstance(size_in_bytes, dict):
                size_value = size_in_bytes.get("Value", 0)
                size_gb = size_value / (1024 ** 3) if size_value and size_value > 0 else 0
            else:
                size_gb = 0
        if size_gb is None or size_gb == 0:
            logger.warning(
                "EFS resource %s has no size data, using default 1.0 GB for pricing",
                resource.get("resource_id"),
            )
            size_gb = 1.0
        try:
            size_gb = float(size_gb)
        except (TypeError, ValueError):
            continue
        if size_gb < 1.0:
            size_gb = 1.0
        throughput_mode = metadata.get("ThroughputMode") or metadata.get("throughput_mode") or "bursting"
        pricing_payload = build_pricing_lookup_payload(
            resource,
            resource_type="efs",
            metadata_overrides={"ThroughputMode": throughput_mode},
        )
        try:
            price = pricing_service.get_price_for_resource(context.db, pricing_payload)
            if price and price.get("price_per_unit") is not None:
                resource["pricing"] = price
                metadata["pricing"] = price
                unit_price = price.get("price_per_unit")
                unit = price.get("unit", "").lower()
                if "gb" in unit or "gib" in unit or "gb-mo" in unit:
                    estimated_monthly = unit_price * size_gb
                elif "byte" in unit or "b" in unit:
                    estimated_monthly = unit_price * size_gb * (1024 ** 3)
                else:
                    estimated_monthly = unit_price * size_gb
            else:
                estimated_monthly = size_gb * 0.30
                price = {
                    "price_per_unit": 0.30,
                    "unit": "GB-Mo",
                    "currency": "USD",
                    "source": "estimate",
                    "note": "Pricing estimated (AWS Pricing API lookup failed or not configured)",
                }
                resource["pricing"] = price
                metadata["pricing"] = price
                metadata["pricing_note"] = "Pricing estimated (AWS Pricing API lookup failed or not configured)"
        except Exception as pricing_error:
            estimated_monthly = size_gb * 0.30
            price = {
                "price_per_unit": 0.30,
                "unit": "GB-Mo",
                "currency": "USD",
                "source": "estimate",
                "note": f"Pricing estimated (Error: {str(pricing_error)})",
            }
            resource["pricing"] = price
            metadata["pricing"] = price
            metadata["pricing_note"] = f"Pricing estimated (Error: {str(pricing_error)})"

        metadata["estimated_monthly_cost"] = round(estimated_monthly, 2)
        savings_monthly = estimated_monthly if full_savings else estimated_monthly * savings_ratio
        metadata["potential_savings_monthly"] = round(savings_monthly, 2)
        metadata["potential_savings_yearly"] = round(savings_monthly * 12, 2)
