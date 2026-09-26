"""Two-phase, table-driven recommendation selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional


@dataclass(frozen=True)
class DecisionRow:
    """One first-match decision-table row."""

    predicate: Callable[[Mapping[str, Any]], bool]
    outcome: str


@dataclass(frozen=True)
class NoRecommendationRow:
    """One priority-ordered reason for returning no recommendation."""

    predicate: Callable[[Mapping[str, Any]], bool]
    reason: Callable[[Mapping[str, Any]], str]


def _telemetry_unknown(state: Mapping[str, Any]) -> bool:
    """Stop when CUR coverage is absent."""

    return state.get("telemetry_status") == "unknown"


def _low_confidence(state: Mapping[str, Any]) -> bool:
    """Cap the ladder for low-confidence coverage."""

    return state.get("confidence") == "low"


def _observed_bursts(state: Mapping[str, Any]) -> bool:
    """Use an observed class-specific retrieval signal for burst sizing."""

    return state.get("pattern_label") == "periodic_bursts" and bool(state.get("class_specific_retrieval"))


def _unknown_bursts(state: Mapping[str, Any]) -> bool:
    """Use generic stress and attach the burst-volume risk."""

    return state.get("pattern_label") == "periodic_bursts"


def _medium_or_high(state: Mapping[str, Any]) -> bool:
    """Select the normal deepest-tolerated rung."""

    return state.get("confidence") in {"medium", "high"}


def _no_rung(_: Mapping[str, Any]) -> bool:
    """Fallback when no main-ladder candidate tolerates the stress case."""

    return True


DECISION_TABLE = (
    DecisionRow(_telemetry_unknown, "stop_unknown"),
    DecisionRow(_low_confidence, "walk_cap_rank_1"),
    DecisionRow(_observed_bursts, "walk_observed_burst"),
    DecisionRow(_unknown_bursts, "walk_generic_burst"),
    DecisionRow(_medium_or_high, "walk_generic_stress"),
    DecisionRow(_no_rung, "stop_no_rung"),
)


MODIFIER_TABLE = (
    (lambda state: state.get("selected_dominated") or state.get("every_main_dominated"), "dominated"),
    (lambda state: bool(state.get("write_only")), "direct_write"),
    (lambda _: True, "plain"),
)


def _main_ladder_scenarios(scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return every main-ladder rung, including rejected priced candidates."""

    return [
        scenario
        for scenario in scenarios
        if scenario.get("ladder_group") == "main" and scenario.get("ladder_rank") is not None
    ]


def _main_scenarios(scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return priced main-ladder scenarios in rank order."""

    return [scenario for scenario in _main_ladder_scenarios(scenarios) if scenario.get("status") == "ok"]


def _no_recommendation_state(
    scenarios: list[dict[str, Any]], params: Mapping[str, Any]
) -> dict[str, Any]:
    """Build the facts consumed by the ordered no-recommendation table."""

    return {
        "statuses": {scenario.get("status") for scenario in scenarios},
        "has_ok_scenario": any(scenario.get("status") == "ok" for scenario in scenarios),
        "main_scenarios": _main_ladder_scenarios(scenarios),
        "priced_main_scenarios": _main_scenarios(scenarios),
        "transition_payback_months_max": params.get("transition_payback_months_max", 12),
    }


def _size_distribution_blocks_all_candidates(state: Mapping[str, Any]) -> bool:
    """Identify an unpriced ladder blocked by unknown object-size distribution."""

    return "size_distribution_unavailable" in state["statuses"] and not state["has_ok_scenario"]


def _pricing_blocks_a_candidate(state: Mapping[str, Any]) -> bool:
    """Identify candidate pricing gaps before reporting access-tolerance failure."""

    return "pricing_unavailable" in state["statuses"]


def _every_main_rung_is_transition_dominated(state: Mapping[str, Any]) -> bool:
    """Identify tiny-bucket candidates rejected by transition fees at every rung."""

    main = state["main_scenarios"]
    return bool(main) and all(
        "TRANSITION_COST_DOMINATES" in {risk["code"] for risk in scenario.get("risks", [])}
        for scenario in main
    )


def _retrieval_tolerance_exhausted(state: Mapping[str, Any]) -> bool:
    """Identify priced main rungs that fail only the assumed retrieval tolerance."""

    return bool(state["priced_main_scenarios"])


def _size_distribution_reason(_: Mapping[str, Any]) -> str:
    """Explain why transition candidates could not be priced safely."""

    return "no recommendation: size_distribution_unavailable for transition candidates"


def _pricing_reason(_: Mapping[str, Any]) -> str:
    """Explain why at least one candidate's cost model is unresolved."""

    return "no recommendation: pricing_unavailable for one or more candidate costs"


def _transition_dominance_reason(state: Mapping[str, Any]) -> str:
    """Explain that every candidate fails the configured transition payback."""

    months = float(state["transition_payback_months_max"])
    return f"no recommendation: one-time transition fee exceeds {months:g} months of savings on every candidate"


def _retrieval_tolerance_reason(_: Mapping[str, Any]) -> str:
    """Explain that no priced rung covers the assumed access pattern."""

    return "no recommendation: no candidate's retrieval tolerance covers the assumed access pattern"


def _generic_no_recommendation_reason(_: Mapping[str, Any]) -> str:
    """Explain an unexpected empty or non-main candidate set."""

    return "no recommendation: no eligible main-ladder candidate was available"


NO_RECOMMENDATION_TABLE = (
    NoRecommendationRow(_size_distribution_blocks_all_candidates, _size_distribution_reason),
    NoRecommendationRow(_pricing_blocks_a_candidate, _pricing_reason),
    NoRecommendationRow(_every_main_rung_is_transition_dominated, _transition_dominance_reason),
    NoRecommendationRow(_retrieval_tolerance_exhausted, _retrieval_tolerance_reason),
    NoRecommendationRow(lambda _: True, _generic_no_recommendation_reason),
)


def _no_recommendation_reason(
    scenarios: list[dict[str, Any]], params: Mapping[str, Any]
) -> str:
    """Return the first applicable cause from the no-recommendation table."""

    state = _no_recommendation_state(scenarios, params)
    row = next(row for row in NO_RECOMMENDATION_TABLE if row.predicate(state))
    return row.reason(state)


def _walk(
    scenarios: list[dict[str, Any]],
    maximum_rank: int,
    tolerance: float,
) -> Optional[dict[str, Any]]:
    """Select the deepest scenario whose tolerance strictly exceeds stress."""

    eligible = []
    for scenario in _main_scenarios(scenarios):
        if scenario["ladder_rank"] > maximum_rank:
            continue
        if scenario.get("tolerated_full_retrievals_per_year") is None:
            continue
        risk_codes = {risk["code"] for risk in scenario.get("risks", [])}
        if risk_codes.intersection({"MIN_DURATION_PENALTY", "SMALL_OBJECTS_128KB"}):
            continue
        if scenario["tolerated_full_retrievals_per_year"] > tolerance:
            eligible.append(scenario)
    if not eligible:
        return None
    def sort_key(item: Mapping[str, Any]) -> tuple[int, float]:
        """Order by deepest rung, then by the known lowest transition fee."""

        transition = item.get("transition_cost")
        return (-item["ladder_rank"], float("inf") if transition is None else transition)

    return sorted(eligible, key=sort_key)[0]


def _peak_retrieval_tolerance(
    pattern: Mapping[str, Any],
    signals: Mapping[str, Any],
) -> Optional[float]:
    """Convert the observed peak retrieval month into annual full reads."""

    history = pattern.get("history") or {}
    history_values = [value for value in history.get("retrieval_gb", []) if value is not None]
    peak_gb = max(history_values, default=None)
    if peak_gb is None or peak_gb <= 0:
        current = signals.get("retrieval_gb_per_month_by_class") or {}
        peak_gb = sum(value for value in current.values() if isinstance(value, (int, float)))
    stored_gb = history.get("stored_gb", [])
    stored = next((value for value in reversed(stored_gb) if value is not None), None)
    if stored is None:
        stored = signals.get("stored_gb")
    if stored is None:
        stored = sum(value for value in (signals.get("monthly_storage_gb_by_class") or {}).values() if value is not None)
    if peak_gb is None or stored in (None, 0) or peak_gb <= 0:
        return None
    return float(peak_gb) / float(stored) * 12.0


def choose_recommendation(
    scenarios: list[dict[str, Any]],
    pattern: Mapping[str, Any],
    signals: Mapping[str, Any],
    params: Mapping[str, Any],
) -> tuple[Optional[str], str]:
    """Apply the literal §4.9 phase-1 table and independent modifiers."""

    state = {
        "telemetry_status": signals.get("telemetry_status"),
        "confidence": signals.get("confidence"),
        "pattern_label": pattern.get("pattern_label"),
        "class_specific_retrieval": any(
            value not in (None, 0) for value in (signals.get("retrieval_gb_per_month_by_class") or {}).values()
        ),
    }
    row = next(row for row in DECISION_TABLE if row.predicate(state))
    if row.outcome == "stop_unknown":
        return None, "no recommendation: CUR telemetry is unknown"
    maximum_rank = 1 if row.outcome == "walk_cap_rank_1" else 4
    tolerance = float(params.get("full_retrievals_per_year", 1))
    if row.outcome == "walk_observed_burst":
        tolerance = _peak_retrieval_tolerance(pattern, signals)
        if tolerance is None:
            return None, "no recommendation: observed burst volume could not be normalized to stored bytes"
    selected = _walk(scenarios, maximum_rank, tolerance)
    if selected is None:
        return None, _no_recommendation_reason(scenarios, params)
    risk_codes = {risk["code"] for risk in selected.get("risks", [])}
    main = _main_ladder_scenarios(scenarios)
    every_dominated = bool(main) and all("TRANSITION_COST_DOMINATES" in {risk["code"] for risk in item.get("risks", [])} for item in main)
    modifier_state = {
        "selected_dominated": "TRANSITION_COST_DOMINATES" in risk_codes,
        "every_main_dominated": every_dominated,
        "write_only": bool(pattern.get("write_only")),
    }
    modifier = next(predicate_state for predicate_state in MODIFIER_TABLE if predicate_state[0](modifier_state))[1]
    if modifier == "dominated":
        if pattern.get("write_only"):
            recommendation = f"DIRECT_WRITE_{selected['policy']}"
            reason = f"phase-1 selected {selected['policy']}; no reads observed, so direct writes avoid the dominated backlog transition"
        else:
            return None, "existing objects too small/numerous to move economically; consider aggregating objects"
    elif modifier == "direct_write":
        recommendation = f"DIRECT_WRITE_{selected['policy']}"
        reason = f"phase-1 selected {selected['policy']}; no reads observed, so writing new objects directly avoids the transition fee"
    else:
        recommendation = selected["policy"]
        reason = f"phase-1 selected {selected['policy']} as the deepest candidate within the assumed retrieval tolerance"
    if row.outcome == "walk_generic_burst" and not state["class_specific_retrieval"]:
        state["burst_volume_unknown"] = True
        recommendation_scenario = next((item for item in scenarios if item.get("policy") == recommendation), None)
        if recommendation_scenario is not None:
            recommendation_scenario.setdefault("_burst_volume_unknown", True)
    for scenario in scenarios:
        scenario["recommended"] = scenario.get("policy") == recommendation
        scenario["recommendation_reason"] = reason if scenario["recommended"] else ""
    return recommendation, reason
