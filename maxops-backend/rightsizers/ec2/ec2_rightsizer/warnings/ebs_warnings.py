"""Approximate EBS pressure and operational-signal evaluation."""

from __future__ import annotations

from dataclasses import dataclass

from ..models import CapacityKind, PerformanceWarningPolicy, RiskLevel


@dataclass(frozen=True)
class EBSWarningInput:
    observed_combined_iops_p99: float | None
    observed_combined_throughput_p99_mibps: float | None
    target_baseline_iops: float | None
    target_baseline_throughput_mibps: float | None
    capacity_kind: CapacityKind = CapacityKind.UNKNOWN
    exceeded_or_throttled: bool = False
    exceeded_metrics_collected: bool = False
    high_queue_length: bool = False
    high_latency: bool = False
    burst_balance_depleted: bool = False
    directional_metrics_incomplete: bool = False


def evaluate_ebs_warnings(
    value: EBSWarningInput, policy: PerformanceWarningPolicy
) -> tuple[RiskLevel, tuple[str, ...], dict[str, object]]:
    warnings: list[str] = []
    ratios: list[float] = []
    if value.target_baseline_iops and value.observed_combined_iops_p99 is not None:
        ratios.append(value.observed_combined_iops_p99 / value.target_baseline_iops)
    if (
        value.target_baseline_throughput_mibps
        and value.observed_combined_throughput_p99_mibps is not None
    ):
        ratios.append(
            value.observed_combined_throughput_p99_mibps
            / value.target_baseline_throughput_mibps
        )
    if ratios:
        ratio = max(ratios)
        if ratio > policy.ebs_high_ratio:
            risk = RiskLevel.HIGH
            warnings.append("HIGH_EBS_USAGE_REVIEW_REQUIRED")
        elif ratio >= policy.ebs_medium_ratio:
            risk = RiskLevel.MEDIUM
            warnings.append("EBS_USAGE_REVIEW_REQUIRED")
        else:
            risk = RiskLevel.LOW
    elif value.capacity_kind == CapacityKind.BURST_OR_UP_TO:
        risk = policy.burst_dependent_risk
        warnings.extend(
            ("EBS_BURST_CAPACITY_REQUIRES_REVIEW", "RESOURCE_BASELINE_CAPACITY_UNKNOWN")
        )
    else:
        risk = policy.unknown_baseline_risk
        warnings.extend(
            ("EBS_BASELINE_CAPABILITY_UNKNOWN", "RESOURCE_BASELINE_CAPACITY_UNKNOWN")
        )
    if value.exceeded_or_throttled:
        risk = RiskLevel.HIGH
        warnings.append("EBS_THROTTLING_OR_EXCEEDED")
    if value.high_queue_length:
        risk = RiskLevel.HIGH
        warnings.append("EBS_QUEUE_REVIEW_REQUIRED")
    if value.high_latency:
        risk = RiskLevel.HIGH
        warnings.append("EBS_LATENCY_REVIEW_REQUIRED")
    if value.burst_balance_depleted:
        risk = RiskLevel.HIGH
        warnings.append("EBS_BURST_BALANCE_DEPLETED")
    if value.directional_metrics_incomplete:
        if risk == RiskLevel.LOW:
            risk = RiskLevel.MEDIUM
        warnings.append("EBS_DIRECTIONAL_METRICS_INCOMPLETE")
    if (
        policy.require_ebs_exceeded_metrics_for_actionable
        and not value.exceeded_metrics_collected
    ):
        warnings.append("EBS_EXCEEDED_METRICS_MISSING")
    return (
        risk,
        tuple(warnings),
        {
            "ebs_iops_utilization_ratio": (
                value.observed_combined_iops_p99 / value.target_baseline_iops
                if value.target_baseline_iops
                and value.observed_combined_iops_p99 is not None
                else None
            ),
            "ebs_throughput_utilization_ratio": (
                value.observed_combined_throughput_p99_mibps
                / value.target_baseline_throughput_mibps
                if value.target_baseline_throughput_mibps
                and value.observed_combined_throughput_p99_mibps is not None
                else None
            ),
            "ebs_utilization_ratio": max(ratios) if ratios else None,
            "policy_version": policy.policy_version,
        },
    )
