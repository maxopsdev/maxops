"""Combine independent hard-constraint and warning results."""

from __future__ import annotations

from .constraints.ebs_hard_constraints import (
    EBSConstraintInput,
    evaluate_ebs_constraints,
)
from .constraints.network_hard_constraints import (
    NetworkConstraintInput,
    evaluate_network_constraints,
)
from .models import PerformanceWarningPolicy, ResourceEvaluation
from .warnings.ebs_warnings import EBSWarningInput, evaluate_ebs_warnings
from .warnings.network_warnings import NetworkWarningInput, evaluate_network_warnings


def evaluate_network(
    constraints: NetworkConstraintInput,
    warnings: NetworkWarningInput,
    policy: PerformanceWarningPolicy,
) -> ResourceEvaluation:
    status, failures, constraint_evidence = evaluate_network_constraints(constraints)
    risk, review_warnings, warning_evidence = evaluate_network_warnings(
        warnings, policy
    )
    return ResourceEvaluation(
        status,
        risk,
        failures + review_warnings,
        {**constraint_evidence, **warning_evidence},
    )


def evaluate_ebs(
    constraints: EBSConstraintInput,
    warnings: EBSWarningInput,
    policy: PerformanceWarningPolicy,
) -> ResourceEvaluation:
    status, failures, constraint_evidence = evaluate_ebs_constraints(constraints)
    risk, review_warnings, warning_evidence = evaluate_ebs_warnings(warnings, policy)
    return ResourceEvaluation(
        status,
        risk,
        failures + review_warnings,
        {**constraint_evidence, **warning_evidence},
    )
