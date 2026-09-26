"""S3 optimizer check for buckets with no observed data access."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.checks.registry import CheckMetadata, check_registry
from app.checks.s3._optimizer_common import (
    bucket_age_days,
    coverage_complete,
    finding_reason,
    optimizer_result,
    request_total,
    skip,
    stored_gb,
)

logger = logging.getLogger(__name__)


def _deep_archive_scenario(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the retain-it Deep Archive scenario, or ``None`` if unavailable."""

    for scenario in result.get("scenarios", []) or []:
        if isinstance(scenario, dict) and scenario.get("policy") == "DEEP_ARCHIVE":
            return scenario
    return None


def check_s3_bucket_unused(
    aws_adapter: Any,
    region: Optional[str] = None,
    window_days: int = 90,
) -> List[Dict[str, Any]]:
    """Find fully covered buckets with zero data-family activity.

    Unknown telemetry, incomplete coverage, ambiguous Tier3/Tier4 activity,
    and any disambiguated restore activity produce no finding.
    """

    findings: List[Dict[str, Any]] = []
    for resource in aws_adapter.get_resources("s3", {}, region):
        result = optimizer_result(resource)
        if result is None or result.get("telemetry_status") == "unknown":
            skip(resource, "optimizer telemetry is unknown", logger)
            continue
        if not coverage_complete(result, window_days):
            skip(resource, "window_not_fully_covered", logger)
            continue
        signals = result.get("signals") or {}
        totals = signals.get("window_totals") or {}
        if signals.get("ambiguous_tier3_activity") or totals.get("ambiguous_tier3_activity"):
            skip(resource, "ambiguous_tier3_activity", logger)
            continue
        if totals.get("restore_requests"):
            skip(resource, "restore activity proves the bucket was used", logger)
            continue
        access_counts = [
            signals.get(name)
            for name in (
                "monthly_data_read_requests",
                "monthly_data_write_requests",
                "monthly_list_requests",
            )
        ]
        if any(value is None for value in access_counts):
            skip(resource, "access_signal_unknown", logger)
            continue
        if any(value != 0 for value in access_counts):
            skip(resource, "data-family activity was observed", logger)
            continue
        metadata = resource.setdefault("metadata", {})
        current = result.get("current") or {}
        monthly_storage_cost = current.get("monthly_storage_cost")
        retain_alternative = _deep_archive_scenario(result)
        metadata.update(
            {
                "recommended_action": "s3_review_unused_bucket",
                "potential_savings_monthly": monthly_storage_cost,
                "potential_savings_yearly": (
                    None if monthly_storage_cost is None else monthly_storage_cost * 12
                ),
                "requests_tier1_total": request_total(result, "tier1"),
                "requests_tier2_total": request_total(result, "tier2"),
                "window_days": window_days,
                "stored_gb": stored_gb(result),
                "bucket_age_days": bucket_age_days(resource),
                "confidence": result.get("confidence"),
                "check_reason": finding_reason(
                    "unused",
                    {"reason": f"no data-family requests in a fully covered {window_days}-day window"},
                ),
            }
        )
        if retain_alternative is not None:
            metadata["retain_alternative"] = retain_alternative
        findings.append(resource)
    return findings


check_registry.register(
    CheckMetadata(
        check_id="s3_bucket_unused",
        name="S3 Bucket Unused",
        description="Identifies fully covered S3 buckets with no observed data-family activity",
        resource_type="s3",
        check_function=check_s3_bucket_unused,
        default_action="s3_review_unused_bucket",
        parameters={"region": None, "window_days": 90},
    )
)
