"""ElastiCache pricing handler."""
from __future__ import annotations

import re
from typing import Optional

from app.pricing.base import PricingContext
from app.pricing.base import build_pricing_lookup_payload, monthly_price_value
from app.pricing.constants_elasticache import (
    ELASTICACHE_GRAVITON_NODE_TYPES,
)
from app.services.aws_pricing_cache import AwsPricingCacheService


def _parse_elasticache_node_type(node_type: str):
    parts = node_type.split(".")
    if len(parts) < 3:
        return None
    family = parts[1]
    size = ".".join(parts[2:])
    return family, size


def _family_base(family: str) -> str:
    return re.sub(r"\d+", "", family).replace("g", "")


def _family_generation(family: str) -> Optional[int]:
    match = re.search(r"\d+", family)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def _find_nearest_graviton_node_type(node_type: str) -> Optional[str]:
    parsed = _parse_elasticache_node_type(node_type)
    if not parsed:
        return None
    family, size = parsed
    base = _family_base(family)
    current_gen = _family_generation(family)
    candidates = []
    for candidate in ELASTICACHE_GRAVITON_NODE_TYPES:
        parsed_candidate = _parse_elasticache_node_type(candidate)
        if not parsed_candidate:
            continue
        cand_family, cand_size = parsed_candidate
        if cand_size != size:
            continue
        if _family_base(cand_family) != base:
            continue
        candidates.append(candidate)
    if not candidates:
        return None
    if current_gen is None:
        return candidates[0]
    candidates.sort(
        key=lambda value: abs((_family_generation(value.split(".")[1]) or 0) - current_gen)
    )
    return candidates[0]


def _apply_elasticache_non_graviton_pricing_impl(
    context: PricingContext,
) -> None:
    pricing_service = AwsPricingCacheService()
    for resource in context.resources:
        metadata = resource.get("metadata") or {}
        node_type = metadata.get("CacheNodeType") or metadata.get("cache_node_type")
        if not node_type:
            continue
        engine = metadata.get("Engine") or metadata.get("engine") or "redis"
        if "Engine" not in metadata and "engine" not in metadata:
            metadata["Engine"] = engine
        target_node_type = _find_nearest_graviton_node_type(node_type)
        if not target_node_type:
            continue
        node_count = None
        if isinstance(metadata.get("MemberClusters"), list) and metadata.get("MemberClusters"):
            node_count = len(metadata.get("MemberClusters"))
        if node_count is None and metadata.get("NumCacheNodes") is not None:
            try:
                node_count = int(metadata.get("NumCacheNodes"))
            except (TypeError, ValueError):
                node_count = None
        if not node_count:
            node_count = 1
        pricing_payload = build_pricing_lookup_payload(
            resource,
            metadata_overrides={"CacheNodeType": node_type, "Engine": engine},
        )
        target_payload = build_pricing_lookup_payload(
            resource,
            metadata_overrides={"CacheNodeType": target_node_type, "Engine": engine},
        )
        current_price = pricing_service.get_price_for_resource(context.db, pricing_payload)
        target_price = pricing_service.get_price_for_resource(context.db, target_payload)
        if not current_price or not target_price:
            continue
        resource["pricing"] = current_price
        metadata["pricing"] = current_price
        current_monthly_unit = monthly_price_value(current_price)
        target_monthly_unit = monthly_price_value(target_price)
        if current_monthly_unit is None or target_monthly_unit is None:
            continue
        current_monthly = current_monthly_unit * node_count
        target_monthly = target_monthly_unit * node_count
        potential_monthly = max(current_monthly - target_monthly, 0)
        metadata["current_node_type_price_per_unit"] = current_price.get("price_per_unit")
        metadata["target_graviton_node_type"] = target_node_type
        metadata["target_node_type_price_per_unit"] = target_price.get("price_per_unit")
        metadata["estimated_monthly_cost"] = current_monthly
        metadata["estimated_monthly_cost_target"] = target_monthly
        metadata["potential_savings_monthly"] = potential_monthly
        metadata["potential_savings_yearly"] = potential_monthly * 12


def _apply_elasticache_pricing_impl(
    context: PricingContext,
    savings_ratio: float = 1.0,
    note: str = None,
) -> None:
    """Apply ElastiCache pricing and potential savings."""
    pricing_service = AwsPricingCacheService()
    for resource in context.resources:
        metadata = resource.get("metadata") or {}
        node_type = metadata.get("CacheNodeType") or metadata.get("cache_node_type")
        if not node_type:
            continue
        engine = metadata.get("Engine") or metadata.get("engine") or "redis"
        if "Engine" not in metadata and "engine" not in metadata:
            metadata["Engine"] = engine

        node_count = None
        if isinstance(metadata.get("MemberClusters"), list) and metadata.get("MemberClusters"):
            node_count = len(metadata.get("MemberClusters"))
        if node_count is None and metadata.get("NumCacheNodes") is not None:
            try:
                node_count = int(metadata.get("NumCacheNodes"))
            except (TypeError, ValueError):
                node_count = None
        if not node_count:
            node_count = 1

        pricing_payload = build_pricing_lookup_payload(
            resource,
            metadata_overrides={"CacheNodeType": node_type, "Engine": engine},
        )

        current_price = pricing_service.get_price_for_resource(context.db, pricing_payload)
        if not current_price:
            continue

        resource["pricing"] = current_price
        metadata["pricing"] = current_price
        current_monthly_unit = monthly_price_value(current_price)
        if current_monthly_unit is None:
            continue

        current_monthly = current_monthly_unit * node_count
        metadata["estimated_monthly_cost"] = round(current_monthly, 2)
        potential_monthly = current_monthly * savings_ratio
        metadata["potential_savings_monthly"] = round(potential_monthly, 2)
        metadata["potential_savings_yearly"] = round(potential_monthly * 12, 2)

        if note:
            metadata["savings_note"] = note


def handle_elasticache_non_graviton_pricing(context: PricingContext) -> None:
    """Handler for ElastiCache non-Graviton pricing calculations."""
    _apply_elasticache_non_graviton_pricing_impl(context)


def handle_elasticache_pricing(context: PricingContext) -> None:
    """Handler for general ElastiCache pricing calculations."""
    savings_ratio = context.savings_ratio or 1.0
    note = context.metadata.get("note") if context.metadata else None
    _apply_elasticache_pricing_impl(context, savings_ratio, note)
