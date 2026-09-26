"""SageMaker endpoint instance-count check."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.checks.registry import CheckMetadata, check_registry
from app.checks.sagemaker.common import (
    create_sagemaker_price_disclosure,
    endpoint_variants,
    resource_metadata,
    signal_total,
    variant_signal,
)


def endpoint_target_instance_count(current_count: int, p95_utilization: float, target_utilization: float = 0.6) -> int:
    """Calculate the documented count target from per-instance utilization."""
    fraction = p95_utilization / 100.0 if p95_utilization > 1 else p95_utilization
    return max(1, math.ceil(current_count * fraction / target_utilization))


def check_sagemaker_endpoint_overprovisioned(
    aws_adapter: Any,
    lookback_days: int = 14,
    cpu_threshold: float = 20.0,
    gpu_threshold: float = 10.0,
    target_utilization: float = 0.6,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Find multi-instance variants with low demand and no scaling policy."""
    resources = aws_adapter.get_resources("sagemaker_endpoint", {"status": "InService"}, region)
    now = datetime.now(timezone.utc)
    flagged: List[Dict[str, Any]] = []
    for endpoint in resources:
        metadata = resource_metadata(endpoint)
        variants = endpoint_variants(endpoint)
        for variant in variants:
            current = int(variant.get("current_instance_count") or variant.get("initial_instance_count") or 0)
            if variant.get("serverless") or current <= 1:
                continue
            if variant.get("autoscaling_policy_present") is True:
                print(f"[SAGEMAKER_ENDPOINT_OVERPROVISIONED] Skipping {endpoint.get('resource_id')}: autoscaling_manages_count")
                continue
            if variant.get("autoscaling_policy_present") is None:
                print(f"[SAGEMAKER_ENDPOINT_OVERPROVISIONED] Skipping {endpoint.get('resource_id')}: discovery_unavailable")
                continue
            try:
                utilization = aws_adapter.get_resource_utilization(
                    endpoint["resource_id"], "sagemaker_endpoint", now - timedelta(days=lookback_days), now, region=endpoint.get("region") or region
                )
            except Exception as exc:
                print(f"[SAGEMAKER_ENDPOINT_OVERPROVISIONED] Skipping {endpoint.get('resource_id')}: query_failed ({exc})")
                continue
            name = str(variant.get("variant_name") or "")
            invocations = variant_signal(utilization, "invocations_per_instance", name)
            if invocations.get("status") is None and not invocations.get("values") and invocations.get("total") is None:
                # Older payloads may only contain Invocations. Keep replay
                # compatibility, while native collection prefers the
                # per-instance signal required by this check.
                invocations = variant_signal(utilization, "invocations", name)
            invocations_total = signal_total(invocations)
            if invocations.get("status") != "usable" or invocations_total is None:
                print(f"[SAGEMAKER_ENDPOINT_OVERPROVISIONED] Skipping {endpoint.get('resource_id')}: no_datapoints")
                continue
            if invocations_total == 0:
                print(f"[SAGEMAKER_ENDPOINT_OVERPROVISIONED] Skipping {endpoint.get('resource_id')}: idle_owns_zero_traffic")
                continue
            cpu = variant_signal(utilization, "cpuutilization", name)
            cpu_status = cpu.get("status") or utilization.get("cpuutilization_metric_status")
            cpu_p95 = cpu.get("summary", {}).get("p95")
            if cpu_p95 is None:
                cpu_p95 = utilization.get("cpu_p95")
            resolved = variant.get("vcpus")
            if resolved is None:
                from app.checks.sagemaker.common import resolve_sagemaker_instance_type
                resolved = resolve_sagemaker_instance_type(variant.get("instance_type"))["vcpus"]
            if cpu_status != "usable" or cpu_p95 is None or not resolved:
                print(f"[SAGEMAKER_ENDPOINT_OVERPROVISIONED] Skipping {endpoint.get('resource_id')}: no_datapoints")
                continue
            per_instance_cpu = float(cpu_p95) / float(resolved)
            gpu_info = variant.get("gpu_device_count")
            gpu = variant_signal(utilization, "gpuutilization", name)
            gpu_status = gpu.get("status") or utilization.get("gpuutilization_metric_status")
            gpu_p95 = gpu.get("summary", {}).get("p95")
            if gpu_p95 is None:
                gpu_p95 = utilization.get("gpu_p95")
            per_device_gpu = float(gpu_p95) / gpu_info if gpu_p95 is not None and gpu_info else None
            if gpu_info and (gpu_status != "usable" or per_device_gpu is None):
                print(f"[SAGEMAKER_ENDPOINT_OVERPROVISIONED] Skipping {endpoint.get('resource_id')}: {gpu.get('reason') or 'no_datapoints'}")
                continue
            if per_instance_cpu >= cpu_threshold or (gpu_info and (per_device_gpu is None or per_device_gpu >= gpu_threshold)):
                continue
            target = endpoint_target_instance_count(current, per_instance_cpu, target_utilization)
            if target >= current:
                continue
            metadata.update(
                {
                    "variant_name": name,
                    "current_instance_count": current,
                    "target_instance_count": target,
                    "cpu_p95_per_instance": per_instance_cpu,
                    "gpu_p95_per_device": per_device_gpu,
                    "cpu_metric_status": "usable",
                    "gpu_metric_status": gpu_status if gpu_info else None,
                    "target_utilization": target_utilization,
                    "target_count_formula": "max(1, ceil(current_instance_count × p95_utilization_fraction / target_utilization))",
                    "recommended_action": "reduce_endpoint_instance_count",
                    "recommended_actions": ["reduce_endpoint_instance_count"],
                    "check_reason": f"Variant {name} has {per_instance_cpu:.1f}% p95 CPU per instance and no autoscaling policy.",
                    "pricing_component": "Hosting",
                    "sagemaker_savings_basis": (current - target) * 730.0,
                    "potential_savings_monthly": None,
                    "savings_disclosure": create_sagemaker_price_disclosure("Hosting"),
                }
            )
            endpoint["metadata"] = metadata
            flagged.append(endpoint)
            break
    return flagged


check_registry.register(CheckMetadata(
    check_id="sagemaker_endpoint_overprovisioned",
    name="SageMaker Endpoint Overprovisioned",
    description="Finds multi-instance endpoint variants with low utilization",
    resource_type="sagemaker",
    check_function=check_sagemaker_endpoint_overprovisioned,
    default_action="reduce_endpoint_instance_count",
    parameters={"lookback_days": 14, "cpu_threshold": 20.0, "gpu_threshold": 10.0, "target_utilization": 0.6, "region": None},
))
