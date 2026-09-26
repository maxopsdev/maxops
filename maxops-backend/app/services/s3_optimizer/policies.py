"""Candidate-policy registry for the V1 ladder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .costs import it_monitoring_cost, price_for, request_cost
from .risks import RISK_RULES, RiskRule


CostModel = Callable[[Mapping[str, Any]], dict[str, Any]]
BucketPredicate = Callable[[Mapping[str, Any]], bool]


@dataclass(frozen=True)
class CandidatePolicy:
    """Describe one policy without embedding it in the orchestration loop."""

    name: str
    target_class: str
    ladder_rank: int | None
    ladder_group: str
    applies_to: BucketPredicate
    cost_model: CostModel
    risk_rules: tuple[RiskRule, ...]


def _has_known_size(state: Mapping[str, Any]) -> bool:
    """Allow candidates only when CloudWatch has at least one size datapoint."""

    return state.get("stored_bytes_by_class") is not None and any(
        value is not None for value in state.get("stored_bytes_by_class", {}).values()
    )


def _transition_applies(_: Mapping[str, Any]) -> bool:
    """All single-hop transition destinations are V1 candidates."""

    return True


def _it_applies(_: Mapping[str, Any]) -> bool:
    """Base Intelligent-Tiering is available for any known-sized bucket."""

    return True


def _back_to_standard_applies(state: Mapping[str, Any]) -> bool:
    """Offer a restore-to-Standard side candidate only for retrieval classes."""

    retrieval_classes = {"STANDARD_IA", "ONEZONE_IA", "GLACIER_IR", "GLACIER", "DEEP_ARCHIVE"}
    stored = state.get("stored_bytes_by_class") or {}
    return any(stored.get(storage_class) not in (None, 0) for storage_class in retrieval_classes)


def _direct_write_applies(state: Mapping[str, Any]) -> bool:
    """Direct-write candidates are only meaningful for write-only buckets."""

    return bool((state.get("pattern") or {}).get("write_only"))


def _transition_model(target_class: str) -> CostModel:
    """Return the transition formula descriptor for one registry entry."""

    return lambda _: {"mode": "transition", "target_class": target_class, "direct_write": False}


def _it_model(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return the registry descriptor for the IT theoretical-bounds model."""

    return {
        "mode": "intelligent_tiering",
        "target_class": "INTELLIGENT_TIERING",
        "direct_write": False,
        "savings_basis": "theoretical_bounds",
        "cost_model": _intelligent_tiering_cost_model,
    }


def _intelligent_tiering_cost_model(
    state: Mapping[str, Any],
    prices: Mapping[str, Any],
    size_allowed: bool,
) -> dict[str, Any]:
    """Price IT's theoretical bounds, or return its explicit unknown status.

    IT has two storage-rate bounds and a monitoring charge rather than the
    transition formula shared by lifecycle classes.  Keeping that model on
    the policy descriptor lets the engine orchestrate registered candidates
    without branching on a storage-class name.
    """

    total_gb = state["total_stored_gb"]
    objects = state["total_objects"]
    current_cost = state["current_cost"]
    eligible = total_gb if size_allowed else 0.0
    small = 0.0 if eligible == total_gb else total_gb - eligible
    base_price = price_for(prices, "storage.INTELLIGENT_TIERING.gb_month")
    fa_price = price_for(prices, "storage.INTELLIGENT_TIERING_FA.gb_month")
    aia_price = price_for(prices, "storage.INTELLIGENT_TIERING_AIA.gb_month")
    fa_price = base_price if fa_price is None else fa_price
    aia_price = base_price if aia_price is None else aia_price
    standard_price = price_for(prices, "storage.STANDARD.gb_month")
    if fa_price is None or aia_price is None or standard_price is None:
        return {"status": "pricing_unavailable"}
    monitoring = it_monitoring_cost(objects, prices)
    low_storage = eligible * fa_price + small * standard_price
    high_storage = eligible * aia_price + small * standard_price
    request = request_cost(state["signals"], prices, "INTELLIGENT_TIERING")
    if monitoring is None or request is None:
        return {"status": "pricing_unavailable"}
    low = -monitoring
    high = current_cost - (high_storage + request + monitoring)
    return {
        "status": "ok",
        "storage": low_storage,
        "request": request,
        "retrieval_observed": 0.0,
        "retrieval_stress": 0.0,
        "it_monitoring": monitoring,
        "savings_low": low,
        "savings_high": high,
    }


def _back_model(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return the restore-to-Standard side-candidate descriptor."""

    return {"mode": "back_to_standard", "target_class": "STANDARD", "direct_write": False}


def _direct_model(target_class: str) -> CostModel:
    """Return the direct-write formula descriptor for one destination."""

    return lambda _: {"mode": "direct_write", "target_class": target_class, "direct_write": True}


def _risk_registry() -> tuple[RiskRule, ...]:
    """Return the complete shared risk registry for each policy entry."""

    return tuple(RISK_RULES)


CANDIDATE_POLICIES = (
    CandidatePolicy("STANDARD_IA", "STANDARD_IA", 1, "main", _transition_applies, _transition_model("STANDARD_IA"), _risk_registry()),
    CandidatePolicy("ONEZONE_IA", "ONEZONE_IA", 1, "main", _transition_applies, _transition_model("ONEZONE_IA"), _risk_registry()),
    CandidatePolicy("GLACIER_IR", "GLACIER_IR", 2, "main", _transition_applies, _transition_model("GLACIER_IR"), _risk_registry()),
    CandidatePolicy("GLACIER_FLEXIBLE", "GLACIER_FLEXIBLE", 3, "main", _transition_applies, _transition_model("GLACIER_FLEXIBLE"), _risk_registry()),
    CandidatePolicy("DEEP_ARCHIVE", "DEEP_ARCHIVE", 4, "main", _transition_applies, _transition_model("DEEP_ARCHIVE"), _risk_registry()),
    CandidatePolicy("INTELLIGENT_TIERING", "INTELLIGENT_TIERING", None, "side", _it_applies, _it_model, _risk_registry()),
    CandidatePolicy("BACK_TO_STANDARD", "STANDARD", None, "side", _back_to_standard_applies, _back_model, _risk_registry()),
    CandidatePolicy("DIRECT_WRITE_STANDARD_IA", "STANDARD_IA", None, "direct_write", _direct_write_applies, _direct_model("STANDARD_IA"), _risk_registry()),
    CandidatePolicy("DIRECT_WRITE_GLACIER_IR", "GLACIER_IR", None, "direct_write", _direct_write_applies, _direct_model("GLACIER_IR"), _risk_registry()),
    CandidatePolicy("DIRECT_WRITE_GLACIER_FLEXIBLE", "GLACIER_FLEXIBLE", None, "direct_write", _direct_write_applies, _direct_model("GLACIER_FLEXIBLE"), _risk_registry()),
    CandidatePolicy("DIRECT_WRITE_DEEP_ARCHIVE", "DEEP_ARCHIVE", None, "direct_write", _direct_write_applies, _direct_model("DEEP_ARCHIVE"), _risk_registry()),
)


def applicable_policies(state: Mapping[str, Any]) -> list[CandidatePolicy]:
    """Return registry entries applicable to one known bucket state."""

    if not _has_known_size(state):
        return []
    return [policy for policy in CANDIDATE_POLICIES if policy.applies_to(state)]
