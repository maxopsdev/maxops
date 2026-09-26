"""Conservative EC2 network and EBS evaluation primitives.

Metric normalization is deliberately separate from capacity interpretation.
The public exports here form the stable interface for recommendation engines.
"""

from .models import (
    CapacityKind,
    HardConstraintStatus,
    PerformanceWarningPolicy,
    RecommendationClassification,
    ResourceEvaluation,
    RiskAssessment,
    RiskLevel,
)
from .selection.recommendation_classification import classify_recommendation

AVAILABILITY_NOTE = (
    "Instance availability and launch capacity are not validated by this "
    "recommendation. Confirm that the target type is available before "
    "applying the change."
)

__all__ = [
    "AVAILABILITY_NOTE",
    "CapacityKind",
    "HardConstraintStatus",
    "PerformanceWarningPolicy",
    "RecommendationClassification",
    "ResourceEvaluation",
    "RiskAssessment",
    "RiskLevel",
    "classify_recommendation",
]
