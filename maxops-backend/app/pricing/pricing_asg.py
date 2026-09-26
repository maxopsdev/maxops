"""ASG pricing handler."""
from __future__ import annotations

from app.pricing.base import PricingContext, build_pricing_lookup_payload, monthly_price_value
from app.services.aws_pricing_cache import AwsPricingCacheService
from app.checks.base import estimate_ec2_monthly_cost


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def handle_asg_pricing(context: PricingContext) -> None:
    """Apply ASG pricing using EC2 instance pricing and capacity data."""
    pricing_service = AwsPricingCacheService()
    savings_ratio = context.savings_ratio if context.savings_ratio is not None else 0.3

    for resource in context.resources:
        metadata = resource.get("metadata") or {}
        instance_type = (
            metadata.get("InstanceType")
            or metadata.get("instance_type")
            or metadata.get("launch_template_instance_type")
            or "m6i.large"
        )

        desired_capacity = _as_int(
            metadata.get("desired_capacity") or metadata.get("DesiredCapacity"),
            2,
        )
        warm_pool_size = _as_int(
            metadata.get("warm_pool_size") or metadata.get("WarmPoolSize"),
            0,
        )
        total_capacity_units = max(1, desired_capacity + warm_pool_size)

        pricing_payload = build_pricing_lookup_payload(
            resource,
            resource_type="ec2",
            metadata_overrides={"InstanceType": instance_type},
        )
        price = pricing_service.get_price_for_resource(context.db, pricing_payload)

        if price and price.get("price_per_unit") is not None:
            unit_monthly = monthly_price_value(price)
            if unit_monthly is None:
                continue
            monthly_compute = unit_monthly * total_capacity_units
            pricing_source = str(price.get("source") or "aws_pricing")
        else:
            monthly_compute = estimate_ec2_monthly_cost(instance_type) * total_capacity_units
            pricing_source = "estimate"
            price = {
                "price_per_unit": round(monthly_compute / max(1, total_capacity_units), 6),
                "unit": "month",
                "currency": "USD",
                "source": "estimate",
            }

        savings_monthly = max(0.0, monthly_compute * savings_ratio)
        resource["pricing"] = price
        metadata["pricing"] = price
        metadata["pricing_source"] = pricing_source
        metadata["estimated_monthly_cost"] = round(monthly_compute, 2)
        metadata["potential_savings_monthly"] = round(savings_monthly, 2)
        metadata["potential_savings_yearly"] = round(savings_monthly * 12, 2)
