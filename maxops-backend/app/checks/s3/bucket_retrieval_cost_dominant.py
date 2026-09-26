"""S3 optimizer check for IA/archive classes whose retrieval cost dominates."""

from __future__ import annotations

from copy import deepcopy
import logging
from typing import Any, Dict, List, Optional

from app.checks.registry import CheckMetadata, check_registry
from app.checks.s3._optimizer_common import finding_reason, optimizer_result, skip

logger = logging.getLogger(__name__)
LOSING_CLASSES = ("STANDARD_IA", "ONEZONE_IA", "GLACIER_IR", "GLACIER", "DEEP_ARCHIVE")


def check_s3_bucket_retrieval_cost_dominant(
    aws_adapter: Any,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Find individual current classes where observed retrieval erases savings."""

    findings: List[Dict[str, Any]] = []
    for resource in aws_adapter.get_resources("s3", {}, region):
        result = optimizer_result(resource)
        if result is None or result.get("telemetry_status") == "unknown":
            skip(resource, "optimizer telemetry is unknown", logger)
            continue
        signals = result.get("signals") or {}
        totals = signals.get("window_totals") or {}
        retrieval = signals.get("per_class_observed_retrieval_cost") or totals.get("per_class_observed_retrieval_cost")
        standard = signals.get("standard_storage_cost_equivalent") or totals.get("standard_storage_cost_equivalent")
        current = signals.get("current_class_storage_cost") or totals.get("current_class_storage_cost")
        delta = signals.get("request_cost_delta_vs_standard") or totals.get("request_cost_delta_vs_standard")
        if not isinstance(retrieval, dict):
            skip(resource, "retrieval_price_unavailable", logger)
            continue
        for storage_class in LOSING_CLASSES:
            if storage_class not in retrieval:
                continue
            retrieval_cost = retrieval.get(storage_class)
            standard_cost = (standard or {}).get(storage_class)
            current_cost = (current or {}).get(storage_class)
            request_delta = (delta or {}).get(storage_class)
            if any(value is None for value in (retrieval_cost, standard_cost, current_cost, request_delta)):
                skip(resource, "retrieval_price_unavailable", logger)
                continue
            savings = float(current_cost) + float(retrieval_cost) - float(standard_cost)
            dominates = float(retrieval_cost) + float(request_delta) > float(standard_cost) - float(current_cost)
            if not dominates or savings <= 0:
                continue
            finding = deepcopy(resource)
            metadata = finding.setdefault("metadata", {})
            metadata.pop("potential_savings_yearly", None)
            versioned = (
                metadata.get("versioning_status") or resource.get("versioning_status")
            ) in {"Enabled", "Suspended"}
            reason = (
                "versioned/suspended buckets retain the archived object as a noncurrent version; savings are unquantified until noncurrent expiration is addressed"
                if versioned
                else f"observed {storage_class} retrieval and request costs exceed its Standard storage savings"
            )
            metadata.update(
                {
                    "recommended_action": "s3_restore_to_standard",
                    "losing_class": storage_class,
                    "per_class_observed_retrieval_cost": retrieval_cost,
                    "standard_storage_cost_equivalent": standard_cost,
                    "current_class_storage_cost": current_cost,
                    "request_cost_delta_vs_standard": request_delta,
                    "confidence": result.get("confidence"),
                    "potential_savings_monthly": None if versioned else savings,
                    "savings_period": "monthly",
                    "risk_code": "NONCURRENT_VERSIONS_INCLUDED" if versioned else None,
                    "check_reason": finding_reason("cost_efficiency", {"resource": resource.get("resource_id"), "issue": reason}),
                }
            )
            findings.append(finding)
    return findings


check_registry.register(
    CheckMetadata(
        check_id="s3_bucket_retrieval_cost_dominant",
        name="S3 Bucket Retrieval Cost Dominant",
        description="Identifies S3 IA and archive classes whose observed retrieval cost dominates savings",
        resource_type="s3",
        check_function=check_s3_bucket_retrieval_cost_dominant,
        default_action="s3_restore_to_standard",
        parameters={"region": None},
    )
)
