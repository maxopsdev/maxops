"""S3 optimizer check for buckets with low request and retrieval activity."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.checks.registry import CheckMetadata, check_registry
from app.checks.s3._optimizer_common import finding_reason, optimizer_result, skip, stored_gb

logger = logging.getLogger(__name__)


def _rank_scenarios(result: Dict[str, Any]) -> list[dict[str, Any]]:
    """Return actionable scenarios ordered by projected high savings."""

    scenarios = []
    for scenario in result.get("scenarios", []) or []:
        risk_codes = {risk.get("code") for risk in scenario.get("risks", []) or []}
        if scenario.get("status") == "not_beneficial" or "MIN_DURATION_PENALTY" in risk_codes:
            continue
        high = (scenario.get("savings_monthly") or {}).get("high")
        if high is None:
            continue
        scenarios.append(scenario)
    return sorted(scenarios, key=lambda item: float((item.get("savings_monthly") or {}).get("high") or 0.0), reverse=True)


def check_s3_bucket_low_access(
    aws_adapter: Any,
    region: Optional[str] = None,
    min_bucket_gb: float = 1.0,
    requests_threshold: float = 0.01,
    retrieval_threshold: float = 0.1,
) -> List[Dict[str, Any]]:
    """Find covered buckets below both access thresholds without pricing."""

    findings: List[Dict[str, Any]] = []
    for resource in aws_adapter.get_resources("s3", {}, region):
        result = optimizer_result(resource)
        if result is None or result.get("telemetry_status") == "unknown":
            skip(resource, "optimizer telemetry is unknown", logger)
            continue
        size = stored_gb(result)
        if size is None or size < min_bucket_gb:
            skip(resource, "bucket size is below the configured minimum or unknown", logger)
            continue
        signals = result.get("signals") or {}
        requests_per_object = signals.get("requests_per_object_per_month")
        ratios = signals.get("retrieval_ratio_by_class")
        if requests_per_object is None:
            skip(resource, "object_count_unknown", logger)
            continue
        if ratios is None or any(value is None for value in ratios.values()):
            skip(resource, "retrieval ratio is unknown", logger)
            continue
        if not (
            requests_per_object < requests_threshold
            and all(value < retrieval_threshold for value in ratios.values())
        ):
            skip(resource, "access thresholds were not both met", logger)
            continue
        scenarios = _rank_scenarios(result)
        metadata = resource.setdefault("metadata", {})
        metadata.update(
            {
                "recommended_action": "s3_review_storage_class",
                "requests_per_object_per_month": requests_per_object,
                "retrieval_ratio_by_class": ratios,
                "storage_class_breakdown": (result.get("current") or {}).get("storage_class_breakdown"),
                "scenarios": scenarios,
                "confidence": result.get("confidence"),
                "potential_savings_monthly": max(
                    [float((item.get("savings_monthly") or {}).get("high") or 0.0) for item in scenarios],
                    default=0.0,
                ),
                "check_reason": finding_reason(
                    "underutilized",
                    {"reason": f"{requests_per_object:.6g} requests/object/month and retrieval ratios below {retrieval_threshold}"},
                ),
            }
        )
        findings.append(resource)
    return findings


check_registry.register(
    CheckMetadata(
        check_id="s3_bucket_low_access",
        name="S3 Bucket Low Access",
        description="Identifies S3 buckets with low request and retrieval activity",
        resource_type="s3",
        check_function=check_s3_bucket_low_access,
        default_action="s3_review_storage_class",
        parameters={
            "region": None,
            "min_bucket_gb": 1.0,
            "requests_threshold": 0.01,
            "retrieval_threshold": 0.1,
        },
    )
)
