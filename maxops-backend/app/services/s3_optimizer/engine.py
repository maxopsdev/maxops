"""S3 optimizer orchestration and frozen Appendix-shape result assembly."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from .costs import (
    STORAGE_CLASS_RULES,
    archive_overhead_cost,
    class_price_key,
    padding_cost,
    price_for,
    retrieval_price,
    request_cost,
    retrieval_cost,
    storage_cost,
    transition_cost,
)
from .object_counts import BYTES_PER_GIB, LIFECYCLE_FLOOR_BYTES, ingest_gb_from_size_slope, object_counts_by_class, overhead_bytes_by_class, stored_bytes_by_class, total_objects
from .patterns import build_pattern
from .policies import CandidatePolicy, applicable_policies
from .recommendation import choose_recommendation
from .result import build_result
from .risks import Risk, evaluate_risks


DEFAULT_PARAMS = {
    "full_retrievals_per_year": 1,
    "requests_threshold": 0.01,
    "retrieval_threshold": 0.1,
    "transition_payback_months_max": 12,
    "small_object_share": 0.5,
    "lifecycle_small_object_override": False,
    "it_monitoring_share": 0.5,
}


def _params(params: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """Merge caller tunables with the specification defaults."""

    result = dict(DEFAULT_PARAMS)
    if params:
        result.update(params)
    return result


def _risk_dict(risk: Risk) -> dict[str, Any]:
    """Convert a typed risk to its persisted JSON shape."""

    return {"code": risk.code, "reason": risk.reason, "value": risk.value}


def _total_stored_gb(stored: Mapping[str, Optional[float]]) -> Optional[float]:
    """Sum known current bytes into GiB, preserving an entirely unknown size."""

    values = [value for value in stored.values() if value is not None]
    return None if not values else sum(values) / BYTES_PER_GIB


def _avg_object_bytes(breakdown: Mapping[str, Mapping[str, Any]]) -> Optional[float]:
    """Return the weighted population average object size."""

    bytes_total = sum(item["bytes"] for item in breakdown.values() if item.get("bytes") is not None)
    objects = sum(item["objects"] for item in breakdown.values() if item.get("objects") is not None)
    return None if objects == 0 else bytes_total / objects


def _requests_per_object(
    signals: Mapping[str, Any], objects: Optional[float]
) -> Optional[float]:
    """Fill the access ratio from CloudWatch objects when CUR lacks it.

    CUR rows do not always carry an object count, while CloudWatch's
    ``NumberOfObjects`` is the optimizer's authoritative population signal.
    Preserve ``None`` when either the population or any access-family rate is
    unknown; a known zero access rate remains a real zero.
    """

    existing = signals.get("requests_per_object_per_month")
    if existing is not None:
        return existing
    if objects in (None, 0):
        return None
    access_rates = [
        signals.get(name)
        for name in (
            "monthly_data_read_requests",
            "monthly_data_write_requests",
            "monthly_list_requests",
        )
    ]
    if any(value is None for value in access_rates):
        return None
    return sum(float(value) for value in access_rates) / float(objects)


def _class_signal_map(values: Any, storage_class: str) -> dict[str, Any]:
    """Keep only one class from a class/speed retrieval signal mapping."""

    if not isinstance(values, Mapping):
        return {}
    selected = {}
    for key, value in values.items():
        text = str(key)
        if text == storage_class or text.startswith(f"{storage_class}."):
            selected[key] = value
    return selected


def _per_class_retrieval_signals(
    signals: Mapping[str, Any],
    price_map: Mapping[str, Any],
    stored: Mapping[str, Optional[float]],
) -> dict[str, Any]:
    """Derive the single-class retrieval comparison used by the dominant check."""

    retrieval_by_class: dict[str, Optional[float]] = {}
    standard_equivalent: dict[str, Optional[float]] = {}
    current_storage: dict[str, Optional[float]] = {}
    request_delta: dict[str, Optional[float]] = {}
    standard_request = request_cost(signals, price_map, "STANDARD")
    for storage_class, byte_count in stored.items():
        if byte_count in (None, 0):
            continue
        class_signals = dict(signals)
        class_signals["retrieval_gb_per_month_by_class"] = _class_signal_map(
            signals.get("retrieval_gb_per_month_by_class"), storage_class
        )
        class_signals["restore_requests_by_class"] = _class_signal_map(
            signals.get("restore_requests_by_class"), storage_class
        )
        retrieval_by_class[storage_class] = retrieval_cost(class_signals, price_map)
        standard_price = price_for(price_map, "storage.STANDARD.gb_month")
        current_price = price_for(price_map, class_price_key("storage", storage_class, "gb_month"))
        standard_equivalent[storage_class] = None if standard_price is None else byte_count / BYTES_PER_GIB * standard_price
        current_storage[storage_class] = None if current_price is None else byte_count / BYTES_PER_GIB * current_price
        target_request = request_cost(signals, price_map, storage_class)
        request_delta[storage_class] = (
            None if target_request is None or standard_request is None else target_request - standard_request
        )
    return {
        "per_class_observed_retrieval_cost": retrieval_by_class,
        "standard_storage_cost_equivalent": standard_equivalent,
        "current_class_storage_cost": current_storage,
        "request_cost_delta_vs_standard": request_delta,
    }


def _storage_parts(
    target_class: str,
    total_gb: Optional[float],
    total_objects: Optional[float],
    average_bytes: Optional[float],
    overhead: Mapping[str, Optional[float]],
    prices: Mapping[str, Any],
    *,
    direct_write: bool = False,
) -> tuple[Optional[float], Optional[str], Optional[float]]:
    """Compute target storage, padding method, and archive overhead."""

    if total_gb is None:
        return None, None, None
    base = storage_cost({target_class: total_gb}, prices, target_class=target_class)
    observed = overhead.get(target_class)
    padding, method = padding_cost(
        target_class,
        total_gb * BYTES_PER_GIB,
        total_objects,
        average_bytes,
        prices,
        observed_overhead_bytes=observed,
    )
    archive = archive_overhead_cost(target_class, total_objects, prices)
    if base is None or padding is None or archive is None:
        return None, method, archive
    return base + padding + archive, method, archive


def _candidate_retrieval_stress(
    target_class: str,
    total_gb: Optional[float],
    objects: Optional[float],
    prices: Mapping[str, Any],
    full_retrievals_per_year: float,
) -> Optional[float]:
    """Price the §4.3 annual full-read stress event as a monthly amount."""

    if total_gb is None:
        return None
    price_class = STORAGE_CLASS_RULES[target_class].price_class
    if price_class == "STANDARD":
        return 0.0
    speed = STORAGE_CLASS_RULES[target_class].retrieval_speed
    retrieval = retrieval_price(prices, price_class, speed or "standard")
    if retrieval is None:
        return None
    result = full_retrievals_per_year * total_gb * retrieval / 12.0
    if price_class in {"GLACIER", "DEEP_ARCHIVE"}:
        if objects is None:
            return None
        restore = price_for(prices, f"restore_request.{price_class}.standard.per_1000")
        if restore is None:
            return None
        result += full_retrievals_per_year * objects / 1000.0 * restore / 12.0
    return result


def _retrieval_speed_assumption(target_class: str) -> str:
    """Return the fixed V1 retrieval speed label for a target class."""

    return "0" if target_class == "STANDARD" else STORAGE_CLASS_RULES[target_class].retrieval_speed or "standard"


def _scenario_template(policy: CandidatePolicy, assumptions: dict[str, Any]) -> dict[str, Any]:
    """Create every persisted scenario field before filling numeric values."""

    return {
        "policy": policy.name,
        "status": "ok",
        "ladder_rank": policy.ladder_rank,
        "ladder_group": policy.ladder_group,
        "recommended": False,
        "recommendation_reason": "",
        "tolerated_full_retrievals_per_year": None,
        "monthly_costs": {"storage": None, "request": None, "retrieval_observed": None, "retrieval_stress": None, "it_monitoring": None},
        "transition_cost": None,
        "transition_cost_existing": None,
        "transition_cost_new_objects": None,
        "backlog_scenario": None,
        "savings_monthly_new_objects": None,
        "savings_monthly": {"low": None, "high": None},
        "savings_yearly": {"low": None, "high": None},
        "breakeven_months": None,
        "risks": [],
        "assumptions": assumptions,
    }


def _pattern_history(signals: Mapping[str, Any], params: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    """Read optional history without inventing a monthly series from totals."""

    return params.get("history") or signals.get("history") or signals.get("monthly_history")


def _build_scenario(
    policy: CandidatePolicy,
    state: Mapping[str, Any],
    prices: Mapping[str, Any],
    params: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one candidate scenario from its registry cost-model descriptor."""

    descriptor = policy.cost_model(state)
    target = descriptor["target_class"]
    direct = descriptor["direct_write"]
    total_gb = state["total_stored_gb"]
    objects = state["total_objects"]
    average = state["avg_object_bytes"]
    override = bool(params["lifecycle_small_object_override"])
    size_allowed = direct or average is not None and (average >= LIFECYCLE_FLOOR_BYTES or override)
    assumptions = {
        "full_retrievals_per_year": params["full_retrievals_per_year"],
        "retrieval_assumption": state["retrieval_assumption"],
        "retrieval_speed_assumption": _retrieval_speed_assumption(target),
        "padding_method": None,
        "object_count_method": state["object_count_method"],
        "pricing_tier": "first_tier",
        "lifecycle_small_object_override": override if not direct else None,
        "size_distribution": "unknown_assumed_all_eligible" if size_allowed and not direct else None,
        "savings_basis": descriptor.get("savings_basis"),
    }
    if direct:
        assumptions.pop("lifecycle_small_object_override", None)
        assumptions.pop("size_distribution", None)
    scenario = _scenario_template(policy, assumptions)
    if not size_allowed and target != "STANDARD":
        scenario["status"] = "size_distribution_unavailable"
        scenario["assumptions"]["size_distribution"] = None
        scenario["reason"] = "default lifecycle rules skip objects under 128 KB; the population's split above and below that line is unknown from average size alone."
        return _finish_scenario(scenario, policy, state, params, prices, None, None)
    if total_gb is None:
        scenario["status"] = "size_unavailable"
        return _finish_scenario(scenario, policy, state, params, prices, None, None)
    storage, padding_method, _ = _storage_parts(target, total_gb, objects, average, state["overhead_bytes"], prices, direct_write=direct)
    scenario["assumptions"]["padding_method"] = padding_method
    custom_cost_model = descriptor.get("cost_model")
    if custom_cost_model is not None:
        model = custom_cost_model(state, prices, size_allowed)
        if model.get("status") != "ok":
            scenario["status"] = model.get("status", "pricing_unavailable")
            return _finish_scenario(scenario, policy, state, params, prices, None, None)
        scenario["monthly_costs"].update(
            {
                "storage": model["storage"],
                "request": model["request"],
                "retrieval_observed": model["retrieval_observed"],
                "retrieval_stress": model["retrieval_stress"],
                "it_monitoring": model["it_monitoring"],
            }
        )
        scenario["savings_monthly"] = {"low": model["savings_low"], "high": model["savings_high"]}
        scenario["savings_yearly"] = {
            "low": model["savings_low"] * 12,
            "high": model["savings_high"] * 12,
        }
        return _finish_scenario(scenario, policy, state, params, prices, model["storage"], 0.0)
    request = request_cost(state["signals"], prices, target)
    projected = retrieval_cost(state["signals"], prices, target_class=target)
    stress = _candidate_retrieval_stress(target, total_gb, objects, prices, float(params["full_retrievals_per_year"]))
    if storage is None or request is None or projected is None or stress is None:
        scenario["status"] = "pricing_unavailable"
        return _finish_scenario(scenario, policy, state, params, prices, None, None)
    scenario["monthly_costs"].update({"storage": storage, "request": request, "retrieval_observed": projected, "retrieval_stress": stress})
    transition = 0.0 if direct or descriptor["mode"] == "back_to_standard" else transition_cost(objects, target, prices)
    if not direct and transition is None:
        scenario["status"] = "pricing_unavailable"
        return scenario
    scenario["transition_cost"] = transition
    scenario["transition_cost_existing"] = transition if not direct else transition_cost(objects, target, prices)
    if direct:
        scenario["transition_cost_new_objects"] = 0.0
        scenario["backlog_scenario"] = target if target != "GLACIER" else "GLACIER_FLEXIBLE"
    candidate_high = storage + request + projected
    candidate_low = storage + request + stress
    low = state["current_cost"] - candidate_low
    high = state["current_cost"] - candidate_high
    if descriptor["mode"] == "back_to_standard" and (state.get("inventory_meta") or {}).get("versioning_status") in {"Enabled", "Suspended"}:
        low = None
        high = None
    scenario["savings_monthly"] = {"low": low, "high": high}
    scenario["savings_yearly"] = {"low": low * 12, "high": high * 12}
    scenario["breakeven_months"] = None if direct or low <= 0 else transition / low
    if not direct and high <= 0:
        scenario["status"] = "not_beneficial"
    storage_saving = state["current_storage_cost"] - storage
    full_read = _candidate_full_read_cost(target, total_gb, objects, prices)
    scenario["_risk_state"] = {
        **state,
        "policy": policy.name,
        "target_class": target,
        "direct_write": direct,
        "transition_cost": transition,
        "transition_price": price_for(prices, f"transition.{STORAGE_CLASS_RULES[target].price_class}.per_1000"),
        "breakeven_months": scenario["breakeven_months"],
        "savings_monthly_low": low,
        "storage_saving_monthly": storage_saving,
        "retrieval_observed_cost": projected,
        "it_monitoring_cost": scenario["monthly_costs"].get("it_monitoring"),
        "retrieval_price": retrieval_price(prices, STORAGE_CLASS_RULES[target].price_class, STORAGE_CLASS_RULES[target].retrieval_speed or "standard"),
        "retrieval_request_price": price_for(prices, f"restore_request.{STORAGE_CLASS_RULES[target].price_class}.standard.per_1000"),
        "it_monitoring_share": params["it_monitoring_share"],
        "transition_payback_months_max": params["transition_payback_months_max"],
        "lifecycle_small_object_override": override,
    }
    scenario["risks"] = [_risk_dict(risk) for risk in evaluate_risks(scenario["_risk_state"])]
    if full_read and storage_saving > 0:
        scenario["tolerated_full_retrievals_per_year"] = storage_saving * 12 / full_read
    if direct:
        ingest = state.get("monthly_ingest_gb")
        standard_write = price_for(prices, "request.STANDARD.data_write.per_1000")
        target_write = price_for(prices, f"request.{STORAGE_CLASS_RULES[target].price_class}.data_write.per_1000")
        if ingest is not None and standard_write is not None and target_write is not None:
            scenario["savings_monthly_new_objects"] = ingest * (price_for(prices, "storage.STANDARD.gb_month") - price_for(prices, class_price_key("storage", target, "gb_month"))) - state["monthly_data_write_requests"] / 1000.0 * (target_write - standard_write)
        scenario["assumptions"]["savings_basis"] = "positive CloudWatch storage-growth slope; deletes are ignored"
    return _finish_scenario(scenario, policy, state, params, prices, storage, stress)


def _candidate_full_read_cost(target: str, gb: Optional[float], objects: Optional[float], prices: Mapping[str, Any]) -> Optional[float]:
    """Compute one full retrieval's per-year tolerance denominator."""

    if target == "STANDARD" or gb is None:
        return None if gb is None else 0.0
    price_class = STORAGE_CLASS_RULES[target].price_class
    speed = STORAGE_CLASS_RULES[target].retrieval_speed
    retrieval = retrieval_price(prices, price_class, speed or "standard")
    if retrieval is None:
        return None
    request = 0.0
    if price_class in {"GLACIER", "DEEP_ARCHIVE"}:
        restore = price_for(prices, f"restore_request.{price_class}.standard.per_1000")
        if restore is None or objects is None:
            return None
        request = objects / 1000.0 * restore
    return gb * retrieval + request


def _finish_scenario(scenario: dict[str, Any], policy: CandidatePolicy, state: Mapping[str, Any], params: Mapping[str, Any], prices: Mapping[str, Any], storage: Optional[float], stress: Optional[float]) -> dict[str, Any]:
    """Finalize risks and remove orchestration-only fields."""

    if "_risk_state" not in scenario:
        risk_state = {
            **state,
            "policy": policy.name,
            "target_class": policy.target_class,
            "direct_write": policy.ladder_group == "direct_write",
            "monthly_early_delete_gb_hours_by_class": state.get("signals", {}).get("monthly_early_delete_gb_hours_by_class"),
            "confidence": state.get("signals", {}).get("confidence"),
            "lifecycle_small_object_override": params["lifecycle_small_object_override"],
            "storage_saving_monthly": None if state.get("current_storage_cost") is None or storage is None else state["current_storage_cost"] - storage,
        }
        scenario["risks"] = [_risk_dict(risk) for risk in evaluate_risks(risk_state)]
    scenario.pop("_risk_state", None)
    scenario.pop("reason", None)
    return scenario


def evaluate_bucket(
    signals: Mapping[str, Any],
    price_map: Mapping[str, Any],
    cloudwatch: Mapping[str, Any],
    inventory_meta: Optional[Mapping[str, Any]] = None,
    params: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Evaluate one bucket offline without AWS calls or mutable side effects."""

    tunables = _params(params)
    inventory_meta = inventory_meta or {}
    bucket = inventory_meta.get("resource_id", signals.get("resource_id"))
    stored = stored_bytes_by_class(cloudwatch, bucket)
    breakdown = object_counts_by_class(cloudwatch, bucket)
    total_gb = _total_stored_gb(stored)
    objects = total_objects(cloudwatch, bucket)
    average = _avg_object_bytes(breakdown)
    requests_per_object = _requests_per_object(signals, objects)
    overhead = overhead_bytes_by_class(cloudwatch, bucket)
    observed_padding = signals.get("monthly_storage_padding_gb_month_by_class") or {}
    for storage_class, amount in observed_padding.items():
        if amount is not None:
            # CUR's small-object line is GB-month of the observed 128 KiB
            # padding population; costs.py consumes the equivalent bytes.
            overhead[storage_class] = float(amount) * BYTES_PER_GIB
    pattern_signals = dict(signals)
    pattern_signals.update({"storage_class_breakdown": breakdown, "total_objects": objects, "avg_object_bytes": average, "requests_per_object_per_month": requests_per_object})
    pattern = build_pattern(pattern_signals, _pattern_history(signals, tunables))
    telemetry = signals.get("telemetry_status")
    if telemetry == "unknown" or total_gb is None:
        empty = dict(signals)
        empty.update({"resource_id": bucket, "telemetry_status": "unknown", "current_storage_cost": None, "current_request_cost": None, "current_retrieval_cost": None})
        result = build_result(empty, price_map, cloudwatch, inventory_meta, tunables, [], (None, "no recommendation: CUR or CloudWatch telemetry is unknown"), pattern, breakdown)
        return result
    current_storage = storage_cost({key: value / BYTES_PER_GIB for key, value in stored.items() if value is not None}, price_map)
    current_request = request_cost(signals, price_map, "STANDARD")
    current_retrieval = retrieval_cost(signals, price_map)
    current_cost = None if current_storage is None or current_request is None or current_retrieval is None else current_storage + current_request + current_retrieval
    state = {
        "signals": signals,
        "pattern": pattern,
        "stored_bytes_by_class": stored,
        "overhead_bytes": overhead,
        "total_stored_gb": total_gb,
        "total_objects": objects,
        "avg_object_bytes": average,
        "object_count_method": next((item.get("object_count_method") for item in breakdown.values() if item.get("object_count_method")), None),
        "current_storage_cost": current_storage,
        "current_cost": current_cost,
        "monthly_data_write_requests": signals.get("monthly_data_write_requests"),
        "monthly_early_delete_gb_hours_by_class": signals.get("monthly_early_delete_gb_hours_by_class"),
        "inventory_meta": inventory_meta,
        "retrieval_assumption": "none_observed" if not signals.get("retrieval_gb_per_month_by_class") else "observed_class_specific",
        "monthly_ingest_gb": ingest_gb_from_size_slope(cloudwatch, bucket),
    }
    class_costs = _per_class_retrieval_signals(signals, price_map, stored)
    policies = applicable_policies(state)
    scenarios = [] if current_cost is None else [_build_scenario(policy, state, price_map, tunables) for policy in policies]
    recommendation = (None, "no recommendation: pricing is unavailable for the current or candidate cost model") if current_cost is None else choose_recommendation(scenarios, pattern, signals, tunables)
    for scenario in scenarios:
        if scenario.get("_burst_volume_unknown"):
            scenario["risks"].extend(
                _risk_dict(risk)
                for risk in evaluate_risks({"burst_volume_unknown": True})
            )
            scenario.pop("_burst_volume_unknown", None)
    result_signals = dict(signals)
    window_totals_with_costs = dict(result_signals.get("window_totals") or {})
    window_totals_with_costs.update(class_costs)
    result_signals.update({"resource_id": bucket, "current_storage_cost": current_storage, "current_request_cost": current_request, "current_retrieval_cost": current_retrieval, "early_delete_cost": None, "requests_per_object_per_month": requests_per_object, "window_totals": window_totals_with_costs})
    return build_result(result_signals, price_map, cloudwatch, inventory_meta, tunables, scenarios, recommendation, pattern, breakdown)
