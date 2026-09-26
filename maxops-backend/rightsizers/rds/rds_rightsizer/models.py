"""Immutable policy and domain values for RDS rightsizing."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class EvaluationStatus(str, Enum):
    RECOMMENDED = "RECOMMENDED"
    NO_RECOMMENDATION = "NO_RECOMMENDATION"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    DEFERRED = "DEFERRED"


@dataclass(frozen=True)
class RdsRightsizerPolicy:
    conservative_target_util: float = 0.55
    balanced_target_util: float = 0.70
    aggressive_target_util: float = 0.85
    min_observed_days: float = 7.0
    min_required_coverage_ratio: float = 0.90
    storage_min_observed_days: float = 30.0
    storage_min_coverage_ratio: float = 0.95
    network_medium_ratio: float = 0.40
    network_high_ratio: float = 0.70
    ebs_medium_ratio: float = 0.40
    ebs_high_ratio: float = 0.70
    connection_warning_ratio: float = 0.70
    memory_absolute_free_floor_gib: float = 1.0
    sqlserver_memory_absolute_free_floor_gib: float = 2.0
    swap_warning_bytes: int = 52_428_800
    free_storage_warning_ratio: float = 0.15
    replica_lag_warning_seconds: float = 30.0
    latency_warning_seconds_by_engine: dict[str, float] = field(
        default_factory=lambda: {
            "mysql": 0.020,
            "mariadb": 0.020,
            "postgres": 0.020,
            "oracle-ee": 0.020,
            "oracle-se2": 0.020,
            "oracle-se1": 0.020,
            "oracle-se": 0.020,
            "sqlserver-ee": 0.020,
            "sqlserver-se": 0.020,
            "sqlserver-ex": 0.020,
            "sqlserver-web": 0.020,
            "db2-ae": 0.020,
            "db2-se": 0.020,
            "*": 0.020,
        }
    )
    queue_depth_warning_by_storage: dict[str, float] = field(
        default_factory=lambda: {"gp2": 5.0, "gp3": 5.0, "io1": 5.0, "io2": 5.0}
    )
    pi_cpu_trigger_percent: float = 35.0
    pi_latency_trigger_seconds: float = 0.020
    pi_queue_depth_trigger: float = 5.0
    pi_connections_trigger: float = 100.0
    db_load_unattributed_warning_ratio: float = 0.10
    attribution_default_days: int = 7
    attribution_max_days: int = 60
    monthly_hours: Decimal = Decimal("730")
    min_monthly_savings: Decimal = Decimal("0.01")
    gated_previous_generation_families: tuple[str, ...] = ()
    policy_version: str = "rds-v1"

    @property
    def tier_ratios(self) -> tuple[tuple[str, float], ...]:
        return (
            ("conservative", self.conservative_target_util),
            ("balanced", self.balanced_target_util),
            ("aggressive", self.aggressive_target_util),
        )

    def __post_init__(self) -> None:
        ratios = [value for _name, value in self.tier_ratios]
        if any(value <= 0 or value > 1 for value in ratios):
            raise ValueError("tier utilization targets must be in (0, 1]")
        if ratios != sorted(ratios):
            raise ValueError("tier utilization targets must be ordered")
        for medium, high, label in (
            (self.network_medium_ratio, self.network_high_ratio, "network"),
            (self.ebs_medium_ratio, self.ebs_high_ratio, "EBS"),
        ):
            if not 0 <= medium <= high <= 1:
                raise ValueError(f"invalid {label} risk ratios")


CLASS_OUTAGE_NOTE = (
    "Changing an RDS DB instance class causes an outage during the change. "
    "Schedule the modification for a maintenance window, test connection "
    "recovery, and review all pending modifications before applying it."
)
MULTI_AZ_NOTE = (
    "A Multi-AZ modification can trigger failover. Existing connections must "
    "reconnect and DNS caching must allow the application to resolve the current "
    "RDS endpoint."
)
STORAGE_NOTE = (
    "Allocated storage is unchanged because RDS storage can't be reduced. This "
    "recommendation changes only storage type and/or provisioned performance. "
    "Storage modification can temporarily degrade performance and RDS can block "
    "another storage change for six hours after it begins."
)
AVAILABILITY_NOTE = (
    "This recommendation validates the target against current RDS orderable "
    "options. It does not reserve capacity or guarantee that a modification will "
    "succeed at apply time."
)
