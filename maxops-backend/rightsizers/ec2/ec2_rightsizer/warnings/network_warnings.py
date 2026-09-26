"""Approximate network pressure evaluation."""

from __future__ import annotations

from dataclasses import dataclass

from ..models import CapacityKind, PerformanceWarningPolicy, RiskLevel

NETWORK_CLASSES = {"LOW": 0, "MODERATE": 1, "HIGH": 2, "VERY_HIGH": 3, "EXTREME": 4}


@dataclass(frozen=True)
class NetworkWarningInput:
    observed_in_p99_mbps: float | None
    observed_out_p99_mbps: float | None
    target_baseline_mbps: float | None
    capacity_kind: CapacityKind = CapacityKind.UNKNOWN
    current_class: str = "UNKNOWN"
    target_class: str = "UNKNOWN"
    bandwidth_allowance_events: bool = False
    pps_allowance_events: bool = False
    conntrack_allowance_events: bool = False
    allowance_metrics_collected: bool = False
    target_peak_mbps: float | None = None
    baseline_is_assumed: bool = False
    bandwidth_weighting_default: bool = True


def evaluate_network_warnings(
    value: NetworkWarningInput, policy: PerformanceWarningPolicy
) -> tuple[RiskLevel, tuple[str, ...], dict[str, object]]:
    warnings: list[str] = []
    risk = RiskLevel.LOW
    ratios: list[float] = []
    inbound_ratio = None
    outbound_ratio = None
    inbound_peak_ratio = (
        value.observed_in_p99_mbps / value.target_peak_mbps
        if value.observed_in_p99_mbps is not None
        and value.target_peak_mbps is not None
        and value.target_peak_mbps > 0
        else None
    )
    outbound_peak_ratio = (
        value.observed_out_p99_mbps / value.target_peak_mbps
        if value.observed_out_p99_mbps is not None
        and value.target_peak_mbps is not None
        and value.target_peak_mbps > 0
        else None
    )
    has_baseline = bool(
        value.target_baseline_mbps and value.target_baseline_mbps > 0
    )
    if has_baseline:
        inbound_ratio = (
            value.observed_in_p99_mbps / value.target_baseline_mbps
            if value.observed_in_p99_mbps is not None
            else None
        )
        outbound_ratio = (
            value.observed_out_p99_mbps / value.target_baseline_mbps
            if value.observed_out_p99_mbps is not None
            else None
        )
        ratios = [
            item for item in (inbound_ratio, outbound_ratio) if item is not None
        ]
    demands = [
        item
        for item in (value.observed_in_p99_mbps, value.observed_out_p99_mbps)
        if item is not None
    ]

    if not value.bandwidth_weighting_default:
        risk = RiskLevel.HIGH
        warnings.append("NON_DEFAULT_NETWORK_BANDWIDTH_WEIGHTING_REQUIRES_REVIEW")
    elif has_baseline:
        ratio = max(ratios, default=0.0)
        demand = max(demands, default=0.0)
        if value.target_peak_mbps is not None and demand >= value.target_peak_mbps:
            # The hard-constraint evaluator supplies the rejection reason.  Keep
            # risk HIGH without also describing this as a burst-zone pass.
            risk = RiskLevel.HIGH
        elif ratio >= 1.0:
            risk = RiskLevel.HIGH
            warnings.append("NETWORK_SUSTAINED_ABOVE_BASELINE")
        elif ratio > policy.network_high_ratio:
            risk = RiskLevel.HIGH
            warnings.append("HIGH_NETWORK_USAGE_REVIEW_REQUIRED")
        elif ratio >= policy.network_medium_ratio:
            risk = RiskLevel.MEDIUM
            warnings.append("NETWORK_USAGE_REVIEW_REQUIRED")
    elif value.capacity_kind == CapacityKind.BURST_OR_UP_TO or value.target_peak_mbps:
        risk = policy.burst_dependent_risk
        warnings.extend(
            (
                "NETWORK_BURST_CAPACITY_REQUIRES_REVIEW",
                "RESOURCE_BASELINE_CAPACITY_UNKNOWN",
            )
        )
    else:
        current = NETWORK_CLASSES.get(value.current_class.upper())
        target = NETWORK_CLASSES.get(value.target_class.upper())
        risk = policy.unknown_baseline_risk
        warnings.append("RESOURCE_BASELINE_CAPACITY_UNKNOWN")
        if target is None:
            warnings.append("NETWORK_CAPABILITY_UNKNOWN")
        elif current is not None and target < current:
            warnings.append("NETWORK_CAPABILITY_REDUCTION")
        elif current is not None and target == current:
            warnings.append("NETWORK_CAPACITY_APPROXIMATE")
    if value.baseline_is_assumed:
        warnings.append("NETWORK_BASELINE_ASSUMED")
    if value.bandwidth_allowance_events:
        warnings.append("NETWORK_ALLOWANCE_EXCEEDED")
        risk = RiskLevel.HIGH
    if value.pps_allowance_events:
        warnings.append("PPS_ALLOWANCE_EXCEEDED")
        risk = RiskLevel.HIGH
    if value.conntrack_allowance_events:
        warnings.append("CONNTRACK_ALLOWANCE_EXCEEDED")
        risk = RiskLevel.HIGH
    if not value.allowance_metrics_collected:
        warnings.append("NETWORK_ALLOWANCE_METRICS_MISSING")
        if policy.require_network_allowance_metrics_for_actionable:
            warnings.append("NETWORK_ALLOWANCE_METRICS_REQUIRED")
    return (
        risk,
        tuple(warnings),
        {
            "network_in_utilization_ratio": inbound_ratio,
            "network_out_utilization_ratio": outbound_ratio,
            "network_utilization_ratio": max(ratios) if ratios else None,
            "network_in_peak_ratio": inbound_peak_ratio,
            "network_out_peak_ratio": outbound_peak_ratio,
            "baseline_mbps": value.target_baseline_mbps,
            "baseline_is_assumed": value.baseline_is_assumed,
            "policy_version": policy.policy_version,
        },
    )
