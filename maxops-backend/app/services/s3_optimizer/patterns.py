"""Access-pattern history and deterministic classification."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Callable, Mapping, Optional

from .object_counts import LIFECYCLE_FLOOR_BYTES


@dataclass(frozen=True)
class PatternRule:
    """One ordered pattern label predicate."""

    label: str
    matches: Callable[[Mapping[str, Any]], bool]


def _history_value(history: Mapping[str, Any], name: str) -> list[float]:
    """Return a numeric history array, with no unknown-to-zero conversion."""

    values = history.get(name, [])
    return [float(value) for value in values if value is not None]


def derive_pattern_fields(
    signals: Mapping[str, Any],
    history: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Derive §3.5 fields from monthly history and Phase-1 signals.

    Missing history remains unknown.  A history entry is expected to be a
    whole calendar month, so no daily CUR boundary interpolation is attempted.
    """

    history = history or signals.get("history") or signals.get("monthly_history") or {}
    reads = _history_value(history, "data_read")
    tier2 = _history_value(history, "tier2")
    writes = _history_value(history, "data_write")
    stored = _history_value(history, "stored_gb")
    egress = _history_value(history, "egress_gb")
    months = list(history.get("months", []))
    monthly_write = signals.get("monthly_data_write_requests")
    objects = signals.get("total_objects")
    if objects is None:
        objects = signals.get("object_count")
    lifetime = None
    if objects is not None and monthly_write not in (None, 0):
        lifetime = float(objects) / float(monthly_write) * 30.0
    peak_month = None
    if len(reads) >= 2 and max(reads, default=0) > 0:
        peak = max(reads)
        if peak > 0:
            peak_index = reads.index(peak)
            peak_month = months[peak_index] if peak_index < len(months) else None
    peak_tier2_month = peak_month
    if len(tier2) >= 2 and max(tier2, default=0) > 0:
        tier2_index = tier2.index(max(tier2))
        peak_tier2_month = months[tier2_index] if tier2_index < len(months) else None
    first_stored = stored[0] if stored else None
    last_stored = stored[-1] if stored else None
    growth = None
    if len(stored) >= 2 and first_stored not in (None, 0):
        growth = (last_stored - first_stored) / first_stored * 100.0
    total_stored = last_stored if last_stored is not None else sum(
        value for value in (signals.get("monthly_storage_gb_by_class") or {}).values() if value is not None
    )
    total_egress = egress[-1] if egress else signals.get("monthly_data_transfer_out_gb")
    egress_share = None if total_stored in (None, 0) or total_egress is None else total_egress / total_stored
    avg_object = signals.get("avg_object_bytes")
    if avg_object is None:
        breakdown = signals.get("storage_class_breakdown") or {}
        byte_values = [item.get("bytes") for item in breakdown.values() if item.get("bytes") is not None]
        count_values = [item.get("objects") for item in breakdown.values() if item.get("objects") not in (None, 0)]
        if byte_values and count_values:
            avg_object = sum(byte_values) / sum(count_values)
    small_share = None if avg_object is None else (1.0 if avg_object < LIFECYCLE_FLOOR_BYTES else None)
    data_write = writes[-1] if writes else signals.get("monthly_data_write_requests")
    data_read = reads[-1] if reads else signals.get("monthly_data_read_requests")
    request_per_object = signals.get("requests_per_object_per_month")
    if request_per_object is None and objects not in (None, 0) and data_read is not None and data_write is not None:
        monthly_signal_read = signals.get("monthly_data_read_requests", data_read)
        monthly_signal_write = signals.get("monthly_data_write_requests", data_write)
        request_per_object = (monthly_signal_read + monthly_signal_write) / objects
    zero_reads = sum(1 for value in reads if value == 0)
    if not reads and signals.get("telemetry_status") == "unknown":
        zero_reads = None
    write_only = bool(reads) and bool(writes) and any(value > 0 for value in writes) and all(value == 0 for value in reads)
    return {
        "history": {
            "months": months,
            "data_write": history.get("data_write", []),
            "data_read": history.get("data_read", []),
            "config": history.get("config", []),
            "retrieval_gb": history.get("retrieval_gb", []),
            "egress_gb": history.get("egress_gb", []),
            "stored_gb": history.get("stored_gb", []),
        },
        "peak_data_read_month": peak_month,
        "peak_tier2_month": peak_tier2_month,
        "zero_read_months": zero_reads,
        "implied_object_lifetime_days": lifetime,
        "growth_pct_over_window": growth,
        "small_object_share_estimate": small_share,
        "egress_share_of_bucket": egress_share,
        "write_only": write_only,
        "_reads": reads,
        "_writes": writes,
        "_request_per_object": request_per_object,
        "_avg_object_bytes": avg_object,
    }


def _unknown(state: Mapping[str, Any]) -> bool:
    """Return whether coverage is insufficient for a reliable pattern."""

    covered_days = state.get("covered_days")
    return state.get("telemetry_status") == "unknown" or covered_days is None or int(covered_days) < 30


def _write_once(state: Mapping[str, Any]) -> bool:
    """Match a one-time load followed by months with no data reads."""

    reads = state.get("_reads", [])
    writes = state.get("_writes", [])
    if not reads or not writes or any(value != 0 for value in reads):
        return False
    positive = [index for index, value in enumerate(writes) if value > 0]
    # A load that predates the observation window has no positive write month
    # in the window, but is still a cold one-time population under §3.5.
    return not positive or positive[-1] <= max(0, len(writes) // 3)


def _log_sink(state: Mapping[str, Any]) -> bool:
    """Match a steady write-heavy destination with no reads."""

    reads = state.get("_reads", [])
    writes = state.get("_writes", [])
    return bool(writes) and bool(reads) and all(value == 0 for value in reads) and sum(writes) >= 10 * max(sum(reads), 1)


def _periodic_bursts(state: Mapping[str, Any]) -> bool:
    """Match occasional large read months separated by quiet months."""

    reads = state.get("_reads", [])
    if len(reads) < 3 or not reads:
        return False
    typical = median(reads)
    if typical == 0:
        return max(reads) > 0 and sum(value == 0 for value in reads) >= 2
    return max(reads) >= 5 * typical and sum(value == 0 for value in reads) >= 2


def _actively_read(state: Mapping[str, Any]) -> bool:
    """Match data request volume above the active-object threshold."""

    value = state.get("_request_per_object")
    return value is not None and value >= 0.1


def _steady_low_reads(state: Mapping[str, Any]) -> bool:
    """Match nonzero, roughly consistent reads after higher-priority labels."""

    reads = state.get("_reads", [])
    return bool(reads) and all(value > 0 for value in reads)


PATTERN_RULES = (
    PatternRule("unknown", _unknown),
    PatternRule("write_once_cold", _write_once),
    PatternRule("log_sink", _log_sink),
    PatternRule("periodic_bursts", _periodic_bursts),
    PatternRule("actively_read", _actively_read),
    PatternRule("steady_low_reads", _steady_low_reads),
)


def classify_pattern(
    signals: Mapping[str, Any],
    history: Optional[Mapping[str, Any]] = None,
) -> tuple[str, str]:
    """Return the first matching pattern label and its display sentence."""

    fields = derive_pattern_fields(signals, history)
    state = dict(fields)
    state["telemetry_status"] = signals.get("telemetry_status")
    state["covered_days"] = signals.get("cur_days_covered", signals.get("covered_days"))
    for rule in PATTERN_RULES:
        if rule.matches(state):
            label = rule.label
            break
    else:
        label = "unknown"
    if label == "unknown":
        summary = "Access pattern is unknown because coverage is absent or shorter than 30 days."
    elif label == "write_once_cold":
        summary = "No reads observed in any covered month; objects are written once and never read back."
    elif label == "log_sink":
        summary = "Write-heavy destination: data writes are at least 10× reads and no reads were observed."
    elif label == "periodic_bursts":
        reads = fields["_reads"]
        typical = median(reads) if reads else 0
        ratio = max(reads) / typical if typical else None
        burst_size = f"{ratio:.1f}×" if ratio is not None else "a burst after zero-read months"
        if ratio is None:
            summary = f"Recurring read bursts: the busiest month contained the observed burst, with {fields['zero_read_months']} zero-read months in between."
        else:
            summary = f"Recurring read bursts: the busiest month saw {burst_size} the typical read volume, with {fields['zero_read_months']} zero-read months in between."
    elif label == "actively_read":
        summary = f"Actively read: {fields['_request_per_object']:.3g} data requests per object per month."
    else:
        summary = "Steady low reads: every covered month has nonzero, relatively consistent data reads."
    return label, summary


def build_pattern(signals: Mapping[str, Any], history: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """Build the persisted pattern block, excluding internal classifier fields."""

    fields = derive_pattern_fields(signals, history)
    label, summary = classify_pattern(signals, history)
    return {key: value for key, value in fields.items() if not key.startswith("_")} | {
        "pattern_label": label,
        "pattern_summary": summary,
    }
