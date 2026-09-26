"""Immutable ElastiCache rightsizer policy and risk values."""

from __future__ import annotations

from dataclasses import dataclass

from rightsizers.ec2.ec2_rightsizer.models import (
    CapacityKind,
    HardConstraintStatus,
    RecommendationClassification,
    ResourceEvaluation,
    RiskLevel,
)


@dataclass(frozen=True)
class ElastiCachePerformanceWarningPolicy:
    network_medium_ratio: float = 0.40
    network_high_ratio: float = 0.70
    memory_medium_ratio: float = 0.40
    memory_high_ratio: float = 0.70
    swap_warning_bytes: int = 52_428_800
    replication_lag_warning_seconds: float = 1.0
    connection_warning_ratio: float = 0.70
    idle_replica_read_ops_threshold: float = 5.0
    replica_actionable_min_coverage_ratio: float = 0.95
    replica_actionable_min_observed_days: float = 30.0
    replica_actionable_max_sample_age_seconds: float = 600.0
    min_read_ops_for_cpu_rate: float = 50.0
    cpu_credit_balance_low_watermark: float = 0.10
    network_assumed_baseline_enabled: bool = True
    network_assumed_baseline_multiplier: float = 1.0
    network_assumed_baseline_floor_mbps: float = 100.0
    trend_primary_context_limit: int = 8
    policy_version: str = "elasticache-v1-balanced"

    def __post_init__(self) -> None:
        for name in (
            "network_medium_ratio",
            "network_high_ratio",
            "memory_medium_ratio",
            "memory_high_ratio",
            "replica_actionable_min_coverage_ratio",
            "connection_warning_ratio",
            "cpu_credit_balance_low_watermark",
        ):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.network_medium_ratio > self.network_high_ratio:
            raise ValueError("network medium threshold cannot exceed high threshold")
        if self.memory_medium_ratio > self.memory_high_ratio:
            raise ValueError("memory medium threshold cannot exceed high threshold")
        for name in (
            "swap_warning_bytes",
            "replication_lag_warning_seconds",
            "idle_replica_read_ops_threshold",
            "replica_actionable_min_observed_days",
            "replica_actionable_max_sample_age_seconds",
            "min_read_ops_for_cpu_rate",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")
        if (
            not isinstance(self.trend_primary_context_limit, int)
            or self.trend_primary_context_limit < 0
        ):
            raise ValueError(
                "trend_primary_context_limit must be a non-negative integer"
            )
        if self.network_assumed_baseline_multiplier <= 0:
            raise ValueError("network_assumed_baseline_multiplier must be positive")
        if self.network_assumed_baseline_floor_mbps < 0:
            raise ValueError("network_assumed_baseline_floor_mbps must not be negative")


@dataclass(frozen=True)
class ElastiCacheRiskAssessment:
    telemetry: RiskLevel
    compute: RiskLevel
    memory: RiskLevel
    network: RiskLevel
    cache_health: RiskLevel
    compatibility: RiskLevel
    migration: RiskLevel
    overall: RiskLevel
    reason_codes: tuple[str, ...] = ()


__all__ = [
    "CapacityKind",
    "HardConstraintStatus",
    "RecommendationClassification",
    "ResourceEvaluation",
    "RiskLevel",
    "ElastiCachePerformanceWarningPolicy",
    "ElastiCacheRiskAssessment",
]
