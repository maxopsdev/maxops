"""Registry-driven scenario risk rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from .costs import STORAGE_CLASS_RULES
from .object_counts import LIFECYCLE_FLOOR_BYTES


@dataclass(frozen=True)
class Risk:
    """One persisted risk finding attached to a scenario."""

    code: str
    reason: str
    value: Optional[float]


@dataclass(frozen=True)
class RiskRule:
    """A registry entry that can decide whether one risk applies."""

    code: str
    applies_to: Callable[[Mapping[str, Any]], bool]
    evaluate: Callable[[Mapping[str, Any]], Optional[Risk]]


def _always(_: Mapping[str, Any]) -> bool:
    """Apply a rule to every candidate scenario."""

    return True


def _target(target: str) -> Callable[[Mapping[str, Any]], bool]:
    """Create a target-class predicate for registry construction."""

    return lambda state: state.get("target_class") == target


def _min_duration(state: Mapping[str, Any]) -> Optional[Risk]:
    """Flag observed write churn faster than the destination's minimum duration."""

    writes = state.get("monthly_data_write_requests")
    objects = state.get("total_objects")
    target = state.get("target_class")
    if writes in (None, 0) or objects in (None, 0) or target not in STORAGE_CLASS_RULES:
        return None
    turnover = writes / objects
    min_months = STORAGE_CLASS_RULES[target].min_duration_days / 30.0
    if min_months == 0:
        return None
    if turnover <= 1.0 / min_months:
        return None
    lifetime = 30.0 / turnover
    return Risk("MIN_DURATION_PENALTY", f"observed writes imply objects live only {lifetime:.1f} days, shorter than the {STORAGE_CLASS_RULES[target].min_duration_days}-day minimum for {target}", lifetime)


def _small_objects(state: Mapping[str, Any]) -> Optional[Risk]:
    """Flag an average object below AWS's 128 KB lifecycle floor."""

    average = state.get("avg_object_bytes")
    if average is None or average >= LIFECYCLE_FLOOR_BYTES:
        return None
    return Risk("SMALL_OBJECTS_128KB", f"average object size is {average:.1f} bytes, below the 128 KB lifecycle floor", average)


def _gir_frequency(state: Mapping[str, Any]) -> Optional[Risk]:
    """Compute GIR full-read frequency at which storage savings disappear."""

    if state.get("target_class") != "GLACIER_IR":
        return None
    saving = state.get("storage_saving_monthly")
    gb = state.get("total_stored_gb")
    objects = state.get("total_objects")
    retrieval = state.get("retrieval_price")
    request = state.get("retrieval_request_price")
    if saving is None or saving <= 0 or gb is None or retrieval is None or request is None:
        return None
    one_read = gb * retrieval + objects / 1000.0 * request
    if one_read <= 0:
        return None
    value = saving * 12.0 / one_read
    return Risk("GIR_FULL_RETRIEVAL_FREQUENCY", f"about {value:.2f} full-bucket retrievals per year erase the storage saving at the assumed GIR retrieval rate", value)


def _it_monitoring(state: Mapping[str, Any]) -> Optional[Risk]:
    """Flag monitoring that consumes at least the configured share of saving."""

    if state.get("target_class") != "INTELLIGENT_TIERING":
        return None
    monitoring = state.get("it_monitoring_cost")
    saving = state.get("storage_saving_monthly")
    share = state.get("it_monitoring_share", 0.5)
    if monitoring is None or saving is None or saving <= 0 or monitoring < share * saving:
        return None
    return Risk("IT_MONITORING_DOMINATES", f"Intelligent-Tiering monitoring is ${monitoring:.2f}/month, at least {share:.0%} of the ${saving:.2f}/month storage saving", monitoring)


def _it_full_read(state: Mapping[str, Any]) -> Optional[Risk]:
    """Explain the 30-day Frequent Access promotion after a full read."""

    if state.get("target_class") != "INTELLIGENT_TIERING":
        return None
    return Risk("IT_FULL_READ_PROMOTES_TO_FA", "one full read moves touched Intelligent-Tiering objects to Frequent Access for 30 days; that month's tiering saving can be erased", None)


def _restore_latency(state: Mapping[str, Any]) -> Optional[Risk]:
    """Report restore latency for archive candidates."""

    target = state.get("target_class")
    if target == "DEEP_ARCHIVE":
        return Risk("DEEP_ARCHIVE_RESTORE_LATENCY", "Deep Archive restores take about 12 hours at standard speed or 48 hours in bulk, and restored copies are billed at Standard during the restore period", None)
    if target == "GLACIER_FLEXIBLE":
        return Risk("GLACIER_RESTORE_LATENCY", "Glacier Flexible restores take about 3–5 hours at standard speed or 5–12 hours in bulk, and restored copies are billed at Standard during the restore period", None)
    return None


def _early_delete(state: Mapping[str, Any]) -> Optional[Risk]:
    """Disclose an early-delete charge already observed in CUR."""

    early = state.get("monthly_early_delete_gb_hours_by_class") or state.get("monthly_early_delete_gb_by_class") or {}
    if not any(value is not None and value > 0 for value in early.values()):
        return None
    return Risk("EARLY_DELETE_OBSERVED", "CUR shows an early-delete charge for this bucket during the observation window", None)


def _retrieval_exceeds(state: Mapping[str, Any]) -> Optional[Risk]:
    """Flag observed retrieval charges larger than storage savings."""

    observed = state.get("retrieval_observed_cost")
    saving = state.get("storage_saving_monthly")
    if observed is None or saving is None or observed <= saving:
        return None
    return Risk("RETRIEVAL_COST_EXCEEDS_STORAGE_SAVINGS", f"observed retrieval fees are ${observed:.2f}/month, exceeding the ${saving:.2f}/month storage saving", observed)


def _transition_dominates(state: Mapping[str, Any]) -> Optional[Risk]:
    """Name a transition fee that cannot pay back within the configured window."""

    if state.get("direct_write") or state.get("policy") == "BACK_TO_STANDARD":
        return None
    transition = state.get("transition_cost")
    saving_low = state.get("savings_monthly_low")
    storage_saving = state.get("storage_saving_monthly")
    max_months = state.get("transition_payback_months_max", 12)
    if transition is None or saving_low is None or storage_saving is None:
        return None
    dominates = transition > max_months * saving_low or (saving_low <= 0 and storage_saving > 0)
    if not dominates:
        return None
    objects = state.get("total_objects")
    average = state.get("avg_object_bytes")
    rate = state.get("transition_price")
    value = state.get("breakeven_months")
    reason = f"{objects:,.0f} objects × ${rate:.2f} per 1000 = ${transition:,.2f} one-time; average object {average / 1024:.1f} KB" if objects is not None and rate is not None and average is not None else f"one-time transition cost is ${transition:,.2f} and dominates the low savings bound"
    return Risk("TRANSITION_COST_DOMINATES", reason, value)


def _direct_write_restore(state: Mapping[str, Any]) -> Optional[Risk]:
    """Explain why direct archive writes are not immediately readable."""

    if state.get("direct_write") and state.get("target_class") in {"GLACIER_FLEXIBLE", "DEEP_ARCHIVE"}:
        return Risk("DIRECT_WRITE_UNREADABLE_WITHOUT_RESTORE", f"objects written straight to {state['target_class']} are not readable without a RestoreObject call", None)
    return None


def _noncurrent(state: Mapping[str, Any]) -> Optional[Risk]:
    """Disclose the population-wide count limitation for versioned buckets."""

    versioning = (state.get("inventory_meta") or {}).get("versioning_status")
    if versioning not in {"Enabled", "Suspended"}:
        return None
    if state.get("policy") == "BACK_TO_STANDARD":
        return Risk("NONCURRENT_VERSIONS_INCLUDED", "this copy creates a new current Standard version while the archived object remains a billable noncurrent version; savings are null unless noncurrent expiration is handled", None)
    return Risk("NONCURRENT_VERSIONS_INCLUDED", "CloudWatch counts include noncurrent versions, delete markers, and MPU parts; use the existing noncurrent-version lifecycle checks for that accounting", None)


def _burst_unknown(state: Mapping[str, Any]) -> Optional[Risk]:
    """Disclose that periodic bursts lacked class-specific byte volume."""

    if not state.get("burst_volume_unknown"):
        return None
    return Risk("BURST_RETRIEVAL_VOLUME_UNKNOWN", "periodic read bursts had no class-specific retrieval-byte signal, so tolerance uses the generic full-retrieval stress assumption", None)


def _low_confidence(state: Mapping[str, Any]) -> Optional[Risk]:
    """Disclose low CUR coverage without suppressing a scenario."""

    if state.get("confidence") != "low":
        return None
    return Risk("LOW_CONFIDENCE_COVERAGE", "CUR coverage is under 30 days, so the recommendation is capped at Standard-IA", None)


RISK_RULES = (
    RiskRule("MIN_DURATION_PENALTY", _always, _min_duration),
    RiskRule("SMALL_OBJECTS_128KB", _always, _small_objects),
    RiskRule("GIR_FULL_RETRIEVAL_FREQUENCY", _target("GLACIER_IR"), _gir_frequency),
    RiskRule("IT_MONITORING_DOMINATES", _target("INTELLIGENT_TIERING"), _it_monitoring),
    RiskRule("IT_FULL_READ_PROMOTES_TO_FA", _target("INTELLIGENT_TIERING"), _it_full_read),
    RiskRule("DEEP_ARCHIVE_RESTORE_LATENCY", _target("DEEP_ARCHIVE"), _restore_latency),
    RiskRule("GLACIER_RESTORE_LATENCY", _target("GLACIER_FLEXIBLE"), _restore_latency),
    RiskRule("EARLY_DELETE_OBSERVED", _always, _early_delete),
    RiskRule("RETRIEVAL_COST_EXCEEDS_STORAGE_SAVINGS", _always, _retrieval_exceeds),
    RiskRule("TRANSITION_COST_DOMINATES", _always, _transition_dominates),
    RiskRule("DIRECT_WRITE_UNREADABLE_WITHOUT_RESTORE", _always, _direct_write_restore),
    RiskRule("NONCURRENT_VERSIONS_INCLUDED", _always, _noncurrent),
    RiskRule("BURST_RETRIEVAL_VOLUME_UNKNOWN", _always, _burst_unknown),
    RiskRule("LOW_CONFIDENCE_COVERAGE", _always, _low_confidence),
)


def evaluate_risks(state: Mapping[str, Any]) -> list[Risk]:
    """Evaluate the risk registry in declaration order."""

    risks = []
    for rule in RISK_RULES:
        if rule.applies_to(state):
            risk = rule.evaluate(state)
            if risk is not None:
                risks.append(risk)
    return risks
