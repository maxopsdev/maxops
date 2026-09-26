"""Immutable policy values for the ASG rightsizer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ASGCapacityPolicy:
    tier_ratios: tuple[tuple[str, float], ...] = (
        ("conservative", 0.55),
        ("balanced", 0.70),
        ("aggressive", 0.85),
    )
    default_tier: str = "balanced"
    minimum_statistic: str = "p50"
    desired_statistic: str = "p99"
    lookback_days: tuple[int, ...] = (14, 30, 60)
    period_seconds: int = 300
    minimum_observed_days: float = 7.0
    minimum_pairing_ratio: float = 0.90
    availability_floor_per_az: int = 1
    memory_preview_enabled: bool = True
    candidate_limit: int = 10
    instance_optimization_enabled: bool = True
    policy_version: str = "asg-v2-combined"

    def __post_init__(self) -> None:
        names = [name for name, _ in self.tier_ratios]
        if not names or len(names) != len(set(names)):
            raise ValueError("tier names must be non-empty and unique")
        if self.default_tier not in names:
            raise ValueError("default tier must exist")
        if dict(self.tier_ratios).get("balanced") != 0.70:
            raise ValueError("balanced tier must use the 0.70 target")
        if any(not 0 < ratio <= 1 for _, ratio in self.tier_ratios):
            raise ValueError("tier ratios must be in (0, 1]")
        if any(days <= 0 for days in self.lookback_days):
            raise ValueError("lookback days must be positive")
        if self.period_seconds <= 0:
            raise ValueError("period_seconds must be positive")
        if self.minimum_observed_days < 0:
            raise ValueError("minimum_observed_days must not be negative")
        if not 0 <= self.minimum_pairing_ratio <= 1:
            raise ValueError("minimum_pairing_ratio must be between zero and one")
        if self.availability_floor_per_az < 1:
            raise ValueError("availability_floor_per_az must be at least one")
        if self.candidate_limit < 1:
            raise ValueError("candidate_limit must be positive")


@dataclass(frozen=True)
class ASGScopePolicy:
    allow_mixed_instances: bool = False
    allow_weighted_capacity: bool = False
    allow_warm_pool: bool = False
    allow_scheduled_scaling: bool = False
    allow_predictive_scaling: bool = False
    allow_scale_to_zero: bool = False


def asg_scope_reason(metadata: Mapping[str, Any], policy: ASGScopePolicy) -> str | None:
    """Return the first deterministic ASG V1 scope failure."""
    checks = (
        (
            metadata.get("mixed_instances_policy_present")
            and not policy.allow_mixed_instances,
            "MIXED_INSTANCES_POLICY_UNSUPPORTED",
        ),
        (
            metadata.get("weighted_capacity_present")
            and not policy.allow_weighted_capacity,
            "WEIGHTED_CAPACITY_UNSUPPORTED",
        ),
        (
            metadata.get("warm_pool_present") and not policy.allow_warm_pool,
            "WARM_POOL_REQUIRES_SEPARATE_OPTIMIZATION",
        ),
        (
            metadata.get("scheduled_actions_present")
            and not policy.allow_scheduled_scaling,
            "SCHEDULED_SCALING_REQUIRES_SEPARATE_OPTIMIZATION",
        ),
        (
            metadata.get("predictive_scaling_present")
            and not policy.allow_predictive_scaling,
            "PREDICTIVE_SCALING_REQUIRES_SEPARATE_OPTIMIZATION",
        ),
        (
            (
                int(metadata.get("min_size") or 0) == 0
                or int(metadata.get("desired_capacity") or 0) == 0
            )
            and not policy.allow_scale_to_zero,
            "SCALE_TO_ZERO_UNSUPPORTED",
        ),
        (
            len(metadata.get("effective_instance_types") or []) != 1,
            "HETEROGENEOUS_INSTANCE_TYPES_UNSUPPORTED",
        ),
        (
            metadata.get("instance_refresh_in_progress"),
            "INSTANCE_REFRESH_IN_PROGRESS",
        ),
        (
            metadata.get("scale_in_protection_present"),
            "SCALE_IN_PROTECTION_ACTIVE",
        ),
        (
            bool(
                {"Launch", "Terminate", "AlarmNotification", "AZRebalance"}
                & set(metadata.get("suspended_processes") or [])
            ),
            "SCALING_PROCESS_SUSPENDED",
        ),
        (
            any(
                value not in {"", "on-demand"}
                for value in metadata.get("instance_lifecycles") or []
            ),
            "UNSUPPORTED_INSTANCE_LIFECYCLE",
        ),
    )
    return next((reason for condition, reason in checks if condition), None)
