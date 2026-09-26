from __future__ import annotations

from ..models import ResourceEvaluation, RiskAssessment, RiskLevel, highest_risk


def build_risk_assessment(
    network: ResourceEvaluation,
    storage: ResourceEvaluation,
    *,
    telemetry: RiskLevel = RiskLevel.LOW,
    compute: RiskLevel = RiskLevel.LOW,
    memory: RiskLevel = RiskLevel.LOW,
    compatibility: RiskLevel = RiskLevel.LOW,
    migration: RiskLevel = RiskLevel.LOW,
    reason_codes: tuple[str, ...] = (),
) -> RiskAssessment:
    dimensions = (
        telemetry,
        compute,
        memory,
        network.risk_level,
        storage.risk_level,
        compatibility,
        migration,
    )
    return RiskAssessment(
        telemetry=telemetry,
        compute=compute,
        memory=memory,
        network=network.risk_level,
        storage=storage.risk_level,
        compatibility=compatibility,
        migration=migration,
        overall=highest_risk(*dimensions),
        reason_codes=tuple(
            dict.fromkeys(reason_codes + network.warnings + storage.warnings)
        ),
    )
