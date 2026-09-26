"""Objective EBS configuration and reliable maximum constraints."""

from __future__ import annotations

from dataclasses import dataclass

from ..models import HardConstraintStatus


@dataclass(frozen=True)
class EBSConstraintInput:
    target_supports_ebs: bool | None = True
    attached_volume_count: int | None = None
    target_attachment_limit: int | None = None
    required_features: frozenset[str] = frozenset()
    target_features: frozenset[str] = frozenset()
    observed_combined_iops_p99: float | None = None
    observed_combined_throughput_p99_mibps: float | None = None
    target_max_iops: float | None = None
    target_max_throughput_mibps: float | None = None
    required_provisioned_iops: float | None = None
    required_provisioned_throughput_mibps: float | None = None
    exceeded_or_throttled: bool = False
    target_capacity_relation: str = "UNKNOWN"


def evaluate_ebs_constraints(
    value: EBSConstraintInput,
) -> tuple[HardConstraintStatus, tuple[str, ...], dict[str, object]]:
    failures: list[str] = []
    unknown: list[str] = []
    if value.target_supports_ebs is False:
        failures.append("TARGET_DOES_NOT_SUPPORT_EBS")
    elif value.target_supports_ebs is None:
        unknown.append("TARGET_EBS_SUPPORT_UNKNOWN")
    if (
        value.attached_volume_count is not None
        and value.target_attachment_limit is not None
    ):
        if value.attached_volume_count > value.target_attachment_limit:
            failures.append("TARGET_EBS_ATTACHMENT_LIMIT_TOO_LOW")
    elif value.attached_volume_count:
        unknown.append("TARGET_EBS_ATTACHMENT_LIMIT_UNKNOWN")
    if value.required_features - value.target_features:
        failures.append("REQUIRED_EBS_FEATURE_UNSUPPORTED")
    comparisons = (
        (
            value.observed_combined_iops_p99,
            value.target_max_iops,
            "EBS_MAX_IOPS_EXCEEDED",
        ),
        (
            value.observed_combined_throughput_p99_mibps,
            value.target_max_throughput_mibps,
            "EBS_MAX_THROUGHPUT_EXCEEDED",
        ),
        (
            value.required_provisioned_iops,
            value.target_max_iops,
            "PROVISIONED_IOPS_UNSUPPORTED",
        ),
        (
            value.required_provisioned_throughput_mibps,
            value.target_max_throughput_mibps,
            "PROVISIONED_THROUGHPUT_UNSUPPORTED",
        ),
    )
    for demand, maximum, code in comparisons:
        if demand is not None and maximum is not None and demand > maximum:
            failures.append(code)
    if value.exceeded_or_throttled and value.target_capacity_relation in {
        "LOWER",
        "EQUAL",
        "UNKNOWN",
    }:
        failures.append("EBS_EXCEEDED_EVENTS_WITHOUT_CAPACITY_INCREASE")
    status = (
        HardConstraintStatus.FAIL
        if failures
        else HardConstraintStatus.UNKNOWN
        if unknown
        else HardConstraintStatus.PASS
    )
    return (
        status,
        tuple(failures),
        {
            "attached_volume_count": value.attached_volume_count,
            "target_attachment_limit": value.target_attachment_limit,
            "observed_combined_iops_p99": value.observed_combined_iops_p99,
            "observed_combined_throughput_p99_mibps": value.observed_combined_throughput_p99_mibps,
            "target_max_iops": value.target_max_iops,
            "target_max_throughput_mibps": value.target_max_throughput_mibps,
            "required_provisioned_iops": value.required_provisioned_iops,
            "required_provisioned_throughput_mibps": value.required_provisioned_throughput_mibps,
            "unknown_constraints": tuple(unknown),
        },
    )
