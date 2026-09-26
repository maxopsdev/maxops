"""SageMaker endpoint idle check."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.sagemaker.common import (
    create_sagemaker_price_disclosure,
    endpoint_variants,
    parse_time,
    resource_metadata,
    signal_total,
    variant_signal,
)


def _endpoint_price_metadata(metadata: Dict[str, Any], monthly_basis: float, component: str) -> None:
    """Record a price-independent savings basis for the pricing pipeline."""
    metadata["sagemaker_savings_basis"] = monthly_basis
    metadata["pricing_component"] = component
    metadata.setdefault("potential_savings_monthly", None)
    metadata["savings_disclosure"] = create_sagemaker_price_disclosure(component)


def check_sagemaker_endpoint_idle(
    aws_adapter: Any,
    lookback_days: int = 14,
    minimum_samples: int = 1,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Find old InService endpoints with zero observed invocations."""
    resources = aws_adapter.get_resources("sagemaker_endpoint", {"status": "InService"}, region)
    now = datetime.now(timezone.utc)
    flagged: List[Dict[str, Any]] = []
    for endpoint in resources:
        metadata = resource_metadata(endpoint)
        status = str(metadata.get("endpoint_status") or endpoint.get("status") or endpoint.get("state") or "")
        if status.casefold() != "inservice":
            continue
        variants = [variant for variant in endpoint_variants(endpoint) if not variant.get("serverless")]
        if not variants:
            continue
        created = parse_time(metadata.get("creation_time") or endpoint.get("created_at"))
        age_days = (now - created).total_seconds() / 86400.0 if created else None
        if age_days is None or age_days < lookback_days:
            print(f"[SAGEMAKER_ENDPOINT_IDLE] Skipping {endpoint.get('resource_id')}: endpoint_younger_than_window")
            continue
        try:
            utilization = aws_adapter.get_resource_utilization(
                endpoint["resource_id"], "sagemaker_endpoint", now - timedelta(days=lookback_days), now, region=endpoint.get("region") or region
            )
        except Exception as exc:
            print(f"[SAGEMAKER_ENDPOINT_IDLE] Skipping {endpoint.get('resource_id')}: query_failed ({exc})")
            continue

        variant_evidence = []
        all_zero = True
        usable = True
        for variant in variants:
            name = str(variant.get("variant_name") or "")
            signal = variant_signal(utilization, "invocations", name)
            status_value = signal.get("status")
            if status_value is None:
                status_value = utilization.get("invocations_metric_status")
            if status_value != "usable":
                usable = False
            total = signal_total(signal)
            samples = signal.get("summary", {}).get("sample_count") or signal.get("sample_count")
            if samples is None:
                samples = len(signal.get("values") or [])
            if total is None or total != 0 or int(samples or 0) < minimum_samples:
                all_zero = False
            variant_evidence.append(
                {
                    "variant_name": name,
                    "invocations_total": total,
                    "instance_type": variant.get("instance_type"),
                    "instance_count": variant.get("current_instance_count", variant.get("initial_instance_count")),
                    "invocations_metric_status": status_value or "unavailable",
                    "invocations_metric_unavailable_reason": signal.get("reason") or utilization.get("invocations_metric_unavailable_reason"),
                    "sample_count": int(samples or 0),
                }
            )
        metadata["invocations_metric_status"] = "usable" if usable else "unavailable"
        if not usable:
            metadata["invocations_metric_unavailable_reason"] = "no_datapoints"
            print(f"[SAGEMAKER_ENDPOINT_IDLE] Skipping {endpoint.get('resource_id')}: no_datapoints")
            continue
        if not all_zero:
            continue
        totals = [item["invocations_total"] for item in variant_evidence]
        metadata.update(
            {
                "invocations_total": float(sum(totals)),
                "lookback_days": lookback_days,
                "variants": variant_evidence,
                "endpoint_age_days": round(age_days, 2),
                "recommended_action": "delete_endpoint",
                "recommended_actions": ["delete_endpoint"],
                "check_reason": create_check_reason("unused", {"reason": "zero invocations for the full lookback window"}),
            }
        )
        monthly_basis = sum(float(item.get("instance_count") or 0) for item in variant_evidence) * 730.0
        _endpoint_price_metadata(metadata, monthly_basis, "Hosting")
        endpoint["metadata"] = metadata
        flagged.append(endpoint)
    return flagged


check_registry.register(CheckMetadata(
    check_id="sagemaker_endpoint_idle",
    name="SageMaker Endpoint Idle",
    description="Finds old SageMaker endpoints with no observed traffic",
    resource_type="sagemaker",
    check_function=check_sagemaker_endpoint_idle,
    default_action="delete_endpoint",
    parameters={"lookback_days": 14, "minimum_samples": 1, "region": None},
))
