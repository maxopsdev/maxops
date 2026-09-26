from __future__ import annotations

from ..models import (
    HardConstraintStatus,
    RecommendationClassification,
    ResourceEvaluation,
    RiskLevel,
)


def classify_recommendation(
    *,
    cpu_passes: bool,
    memory_passes: bool,
    network: ResourceEvaluation,
    storage: ResourceEvaluation,
    migration_required: bool = False,
) -> RecommendationClassification:
    if not cpu_passes or not memory_passes:
        return RecommendationClassification.REJECTED
    if HardConstraintStatus.FAIL in (
        network.hard_constraint_status,
        storage.hard_constraint_status,
    ):
        return RecommendationClassification.REJECTED
    if migration_required:
        return RecommendationClassification.OPPORTUNITY
    known_statuses = {HardConstraintStatus.PASS, HardConstraintStatus.NOT_APPLICABLE}
    fully_known = (
        network.hard_constraint_status in known_statuses
        and storage.hard_constraint_status in known_statuses
    )
    non_blocking_warnings = {"NETWORK_ALLOWANCE_METRICS_MISSING"}
    blocking_warnings = (
        set(network.warnings) | set(storage.warnings)
    ) - non_blocking_warnings
    if (
        fully_known
        and network.risk_level == storage.risk_level == RiskLevel.LOW
        and not blocking_warnings
    ):
        return RecommendationClassification.ACTIONABLE
    return RecommendationClassification.CONDITIONAL
