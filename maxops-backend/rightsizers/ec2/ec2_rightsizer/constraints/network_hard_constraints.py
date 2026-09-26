"""Objective EC2 networking incompatibilities only."""

from __future__ import annotations

from dataclasses import dataclass

from ..models import HardConstraintStatus


@dataclass(frozen=True)
class NetworkConstraintInput:
    attached_eni_count: int | None = None
    target_eni_limit: int | None = None
    requires_efa: bool = False
    target_supports_efa: bool | None = None
    required_features: frozenset[str] = frozenset()
    target_features: frozenset[str] = frozenset()
    observed_in_p99_mbps: float | None = None
    observed_out_p99_mbps: float | None = None
    target_reliable_max_mbps: float | None = None
    allowance_exceeded: bool = False
    target_capacity_relation: str = "UNKNOWN"


def evaluate_network_constraints(
    value: NetworkConstraintInput,
) -> tuple[HardConstraintStatus, tuple[str, ...], dict[str, object]]:
    failures: list[str] = []
    unknown: list[str] = []
    if value.attached_eni_count is not None and value.target_eni_limit is not None:
        if value.attached_eni_count > value.target_eni_limit:
            failures.append("TARGET_ENI_LIMIT_TOO_LOW")
    elif value.attached_eni_count:
        unknown.append("TARGET_ENI_LIMIT_UNKNOWN")
    if value.requires_efa and value.target_supports_efa is False:
        failures.append("EFA_NOT_SUPPORTED")
    elif value.requires_efa and value.target_supports_efa is None:
        unknown.append("EFA_SUPPORT_UNKNOWN")
    missing_features = value.required_features - value.target_features
    if missing_features:
        failures.append("REQUIRED_NETWORK_FEATURE_UNSUPPORTED")
    demands = [
        item
        for item in (value.observed_in_p99_mbps, value.observed_out_p99_mbps)
        if item is not None
    ]
    if (
        demands
        and value.target_reliable_max_mbps is not None
        and max(demands) >= value.target_reliable_max_mbps
    ):
        failures.append("NETWORK_RELIABLE_MAX_EXCEEDED")
    if value.allowance_exceeded and value.target_capacity_relation in {
        "LOWER",
        "EQUAL",
        "UNKNOWN",
    }:
        failures.append("NETWORK_ALLOWANCE_EVENTS_WITHOUT_CAPACITY_INCREASE")
    status = (
        HardConstraintStatus.FAIL
        if failures
        else HardConstraintStatus.UNKNOWN
        if unknown
        else HardConstraintStatus.PASS
    )
    evidence = {
        "attached_eni_count": value.attached_eni_count,
        "target_eni_limit": value.target_eni_limit,
        "observed_network_in_p99_mbps": value.observed_in_p99_mbps,
        "observed_network_out_p99_mbps": value.observed_out_p99_mbps,
        "target_reliable_max_mbps": value.target_reliable_max_mbps,
        "unknown_constraints": tuple(unknown),
    }
    return status, tuple(failures), evidence
