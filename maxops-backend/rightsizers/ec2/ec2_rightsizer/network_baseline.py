"""Shared numeric network-baseline policy for EC2-derived catalogs."""

from __future__ import annotations

from .models import CapacityKind, PerformanceWarningPolicy


def assumed_network_baseline_mbps(
    *,
    published_baseline_mbps: float | None,
    reliable_max_mbps: float | None,
    vcpus: float | int | None,
    family_max_vcpus: float | None,
    policy: PerformanceWarningPolicy,
) -> float | None:
    """Conservatively estimate sustained bandwidth for numeric up-to types."""
    if not policy.network_assumed_baseline_enabled:
        return None
    if published_baseline_mbps is not None:
        return None
    if reliable_max_mbps is None or vcpus is None or not family_max_vcpus:
        return None
    share = min(float(vcpus) / family_max_vcpus, 1.0)
    value = reliable_max_mbps * share * policy.network_assumed_baseline_multiplier
    return max(value, policy.network_assumed_baseline_floor_mbps)


def effective_network_baseline(
    *,
    published_baseline_mbps: float | None,
    reliable_max_mbps: float | None,
    vcpus: float | int | None,
    family_max_vcpus: float | None,
    capacity_kind: str,
    policy: PerformanceWarningPolicy,
) -> tuple[float | None, CapacityKind, bool]:
    """Return published/assumed baseline, normalized kind, and assumption flag."""
    if published_baseline_mbps is not None:
        return published_baseline_mbps, CapacityKind.BASELINE, False
    assumed = assumed_network_baseline_mbps(
        published_baseline_mbps=published_baseline_mbps,
        reliable_max_mbps=reliable_max_mbps,
        vcpus=vcpus,
        family_max_vcpus=family_max_vcpus,
        policy=policy,
    )
    if assumed is not None:
        return assumed, CapacityKind.ASSUMED_BASELINE, True
    kind = (
        CapacityKind(capacity_kind)
        if capacity_kind in CapacityKind._value2member_map_
        else CapacityKind.UNKNOWN
    )
    return None, kind, False
