"""Frozen Appendix result assembly for the S3 optimizer."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from .object_counts import daily_size_series


def build_result(
    signals: Mapping[str, Any],
    price_map: Mapping[str, Any],
    cloudwatch: Mapping[str, Any],
    inventory_meta: Optional[Mapping[str, Any]],
    params: Mapping[str, Any],
    scenarios: list[dict[str, Any]],
    recommendation: tuple[Optional[str], str],
    pattern: Mapping[str, Any],
    breakdown: Mapping[str, Any],
) -> dict[str, Any]:
    """Assemble exactly the frozen top-level Appendix result keys."""

    daily = daily_size_series(cloudwatch, signals.get("resource_id"))
    covered_days = signals.get("covered_days")
    if isinstance(covered_days, (list, tuple, set, frozenset)):
        covered_days = sorted({str(day)[:10] for day in covered_days})
    else:
        covered_days = None
    coverage = {
        "cur_days_covered": signals.get("cur_days_covered"),
        "cur_first_day": signals.get("cur_first_day"),
        "cur_last_day": signals.get("cur_last_day"),
        "covered_days": covered_days,
        "cloudwatch_days_covered": len({point["date"] for point in daily}),
    }
    window_totals = signals.get("window_totals")
    window_costs = window_totals if isinstance(window_totals, Mapping) else {}
    signal_block = {
        "monthly_data_read_requests": signals.get("monthly_data_read_requests"),
        "monthly_data_write_requests": signals.get("monthly_data_write_requests"),
        "monthly_list_requests": signals.get("monthly_list_requests"),
        "monthly_config_requests": signals.get("monthly_config_requests"),
        "monthly_tier1_requests": signals.get("monthly_tier1_requests"),
        "monthly_tier2_requests": signals.get("monthly_tier2_requests"),
        "restore_requests_by_class": signals.get("restore_requests_by_class"),
        "monthly_restore_requests": signals.get("monthly_restore_requests"),
        "retrieval_gb_per_month_by_class": signals.get("retrieval_gb_per_month_by_class"),
        "monthly_retrieval_gb_by_class": signals.get("monthly_retrieval_gb_by_class"),
        "requests_per_object_per_month": signals.get("requests_per_object_per_month"),
        "retrieval_ratio_by_class": signals.get("retrieval_ratio_by_class"),
        "per_class_observed_retrieval_cost": window_costs.get("per_class_observed_retrieval_cost"),
        "standard_storage_cost_equivalent": window_costs.get("standard_storage_cost_equivalent"),
        "current_class_storage_cost": window_costs.get("current_class_storage_cost"),
        "request_cost_delta_vs_standard": window_costs.get("request_cost_delta_vs_standard"),
        "early_delete_cost": signals.get("early_delete_cost"),
        "config_requests": signals.get("config_requests"),
        "monthly_early_delete_gb_hours_by_class": signals.get("monthly_early_delete_gb_hours_by_class"),
        "window_totals": window_totals,
    }
    return {
        "inventory_id": (inventory_meta or {}).get("inventory_id") if "inventory_id" in (inventory_meta or {}) else 0,
        "resource_id": (inventory_meta or {}).get("resource_id", signals.get("resource_id")),
        "account_id": (inventory_meta or {}).get("account_id"),
        "region": (inventory_meta or {}).get("region"),
        "telemetry_status": signals.get("telemetry_status"),
        "confidence": signals.get("confidence"),
        "coverage": coverage,
        "current": {
            "storage_class_breakdown": breakdown,
            "monthly_storage_cost": signals.get("current_storage_cost"),
            "monthly_request_cost": signals.get("current_request_cost"),
            "monthly_retrieval_cost": signals.get("current_retrieval_cost"),
        },
        "signals": signal_block,
        "checks_triggered": [],  # Checks land in Phase 3; Phase 2 only supplies their shared engine data.
        "scenarios": scenarios,
        "recommendation": {"policy": recommendation[0], "reason": recommendation[1]},
        "pattern": pattern,
        "unmapped_usage_types": signals.get("unmapped_usage_types", []),
        "price_map_window": price_map.get("price_map_window"),
        "seed_as_of": price_map.get("seed_as_of"),
    }
