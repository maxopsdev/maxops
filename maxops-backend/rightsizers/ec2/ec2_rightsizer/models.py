"""Shared immutable values for the EC2 rightsizer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class HardConstraintStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class CapacityKind(str, Enum):
    BASELINE = "BASELINE"
    ASSUMED_BASELINE = "ASSUMED_BASELINE"
    BURST_OR_UP_TO = "BURST_OR_UP_TO"
    QUALITATIVE = "QUALITATIVE"
    UNKNOWN = "UNKNOWN"


class RecommendationClassification(str, Enum):
    ACTIONABLE = "ACTIONABLE"
    CONDITIONAL = "CONDITIONAL"
    OPPORTUNITY = "OPPORTUNITY"
    PREVIEW = "PREVIEW"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class ResourceEvaluation:
    hard_constraint_status: HardConstraintStatus
    risk_level: RiskLevel
    warnings: tuple[str, ...] = ()
    evidence: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "warnings", tuple(dict.fromkeys(self.warnings)))
        object.__setattr__(self, "evidence", dict(self.evidence))


@dataclass(frozen=True)
class PerformanceWarningPolicy:
    network_medium_ratio: float = 0.40
    network_high_ratio: float = 0.70
    ebs_medium_ratio: float = 0.40
    ebs_high_ratio: float = 0.70
    require_network_allowance_metrics_for_actionable: bool = False
    require_ebs_exceeded_metrics_for_actionable: bool = False
    unknown_baseline_risk: RiskLevel = RiskLevel.HIGH
    burst_dependent_risk: RiskLevel = RiskLevel.HIGH
    network_assumed_baseline_enabled: bool = True
    network_assumed_baseline_multiplier: float = 1.0
    network_assumed_baseline_floor_mbps: float = 100.0
    policy_version: str = "v1-balanced"

    def __post_init__(self) -> None:
        for name in (
            "network_medium_ratio",
            "network_high_ratio",
            "ebs_medium_ratio",
            "ebs_high_ratio",
        ):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.network_medium_ratio > self.network_high_ratio:
            raise ValueError("network medium threshold cannot exceed high threshold")
        if self.ebs_medium_ratio > self.ebs_high_ratio:
            raise ValueError("EBS medium threshold cannot exceed high threshold")
        if self.network_assumed_baseline_multiplier <= 0:
            raise ValueError("network assumed baseline multiplier must be positive")
        if self.network_assumed_baseline_floor_mbps < 0:
            raise ValueError("network assumed baseline floor must not be negative")
        if not self.policy_version.strip():
            raise ValueError("policy_version must not be empty")


@dataclass(frozen=True)
class RiskAssessment:
    telemetry: RiskLevel
    compute: RiskLevel
    memory: RiskLevel
    network: RiskLevel
    storage: RiskLevel
    compatibility: RiskLevel
    migration: RiskLevel
    overall: RiskLevel
    reason_codes: tuple[str, ...] = ()


RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}


def highest_risk(*levels: RiskLevel) -> RiskLevel:
    return max(levels, key=RISK_ORDER.__getitem__, default=RiskLevel.LOW)
