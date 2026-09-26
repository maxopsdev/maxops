"""SageMaker endpoint GPU underutilization check."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.checks.registry import CheckMetadata, check_registry
from app.checks.sagemaker.common import (
    create_sagemaker_price_disclosure,
    endpoint_variants,
    gpu_endpoint_evidence,
    resource_metadata,
    signal_total,
    variant_signal,
)


def check_sagemaker_endpoint_gpu_underutilized(
    aws_adapter: Any,
    lookback_days: int = 14,
    gpu_threshold: float = 5.0,
    gpu_memory_threshold: float = 10.0,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Find traffic-serving GPU endpoint variants with idle compute and memory."""
    resources = aws_adapter.get_resources("sagemaker_endpoint", {"status": "InService"}, region)
    now = datetime.now(timezone.utc)
    flagged: List[Dict[str, Any]] = []
    for endpoint in resources:
        metadata = resource_metadata(endpoint)
        for variant in endpoint_variants(endpoint):
            if variant.get("serverless"):
                continue
            if not variant.get("gpu_device_count"):
                print(f"[SAGEMAKER_ENDPOINT_GPU] Skipping {endpoint.get('resource_id')}: gpu_count_unknown")
                continue
            name = str(variant.get("variant_name") or "")
            try:
                utilization = aws_adapter.get_resource_utilization(
                    endpoint["resource_id"], "sagemaker_endpoint", now - timedelta(days=lookback_days), now, region=endpoint.get("region") or region
                )
            except Exception as exc:
                print(f"[SAGEMAKER_ENDPOINT_GPU] Skipping {endpoint.get('resource_id')}: query_failed ({exc})")
                continue
            invocations = variant_signal(utilization, "invocations", name)
            if invocations.get("status") == "usable" and (signal_total(invocations) or 0) <= 0:
                continue
            if invocations.get("status") != "usable" and utilization.get("invocations_metric_status") != "usable":
                continue
            evidence = gpu_endpoint_evidence(utilization, variant.get("gpu_device_count"), name)
            if evidence["status"] != "usable" or evidence["gpu_p95_per_device"] is None or evidence["gpu_memory_p95_per_device"] is None:
                reason = "gpu_count_unknown" if not variant.get("gpu_device_count") else "no_datapoints"
                print(f"[SAGEMAKER_ENDPOINT_GPU] Skipping {endpoint.get('resource_id')}: {reason}")
                continue
            if evidence["gpu_p95_per_device"] >= gpu_threshold or evidence["gpu_memory_p95_per_device"] >= gpu_memory_threshold:
                continue
            metadata.update(
                {
                    "variant_name": name,
                    "gpu_device_count": variant.get("gpu_device_count"),
                    "gpu_p95_per_device": evidence["gpu_p95_per_device"],
                    "gpu_memory_p95_per_device": evidence["gpu_memory_p95_per_device"],
                    "gpu_aggregation": evidence["gpu_aggregation"],
                    "gpu_metric_status": "usable",
                    "gpu_metric_unavailable_reason": None,
                    "recommended_action": "rightsize",
                    "recommended_actions": ["rightsize"],
                    "check_reason": "GPUs are idle while the endpoint serves traffic; move to CPU or a smaller GPU type.",
                    "pricing_component": "Hosting",
                    "sagemaker_savings_basis": float(variant.get("current_instance_count") or variant.get("initial_instance_count") or 1) * 730.0,
                    "potential_savings_monthly": None,
                    "savings_disclosure": create_sagemaker_price_disclosure("Hosting"),
                }
            )
            if evidence["gpu_aggregation"] == "mean_of_sum":
                metadata["savings_disclosure"] += " GPU_AGGREGATION_MEAN_ONLY."
                metadata["GPU_AGGREGATION_MEAN_ONLY"] = True
            endpoint["metadata"] = metadata
            flagged.append(endpoint)
            break
    return flagged


check_registry.register(CheckMetadata(
    check_id="sagemaker_endpoint_gpu_underutilized",
    name="SageMaker Endpoint GPU Underutilized",
    description="Finds serving GPU endpoints whose compute and GPU memory stay idle",
    resource_type="sagemaker",
    check_function=check_sagemaker_endpoint_gpu_underutilized,
    default_action="rightsize",
    parameters={"lookback_days": 14, "gpu_threshold": 5.0, "gpu_memory_threshold": 10.0, "region": None},
))
