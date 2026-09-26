"""Shared SageMaker resolution, telemetry, and evidence helpers."""

from __future__ import annotations

import base64
import binascii
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

from app.utils.ec2_gpu_info import gpu_info_for_instance_type
from app.utils.sagemaker_instance_types import seeded_gpu_info


TELEMETRY_UNAVAILABLE_REASONS = frozenset(
    {
        "no_candidates",
        "discovery_unavailable",
        "query_failed",
        "no_datapoints",
        "gpu_count_unknown",
    }
)


def _bare_instance_type(instance_type: Any) -> str:
    """Remove SageMaker's namespace prefix while preserving the type spelling."""
    value = str(instance_type or "").strip()
    return value[3:] if value.lower().startswith("ml.") else value


def _catalog_vcpus(instance_type: str) -> Optional[float]:
    """Read vCPU capacity from the packaged EC2 catalog when available."""
    try:
        from app.services.ec2_instance_catalog import EC2InstanceCatalog

        entry = EC2InstanceCatalog().get("us-east-1", instance_type)
        return float(entry.vcpus) if entry and entry.vcpus is not None else None
    except Exception:
        return None


def resolve_sagemaker_instance_type(instance_type: Any) -> Dict[str, Any]:
    """Resolve a SageMaker type without guessing GPU capability.

    The returned dictionary is stable evidence for inventory and checks.  An
    unknown type has ``known=False`` and ``gpu_count=None``; a known CPU type
    has ``known=True`` and ``is_gpu=False``.
    """
    original = str(instance_type or "").strip()
    bare = _bare_instance_type(original)
    gpu_info = gpu_info_for_instance_type(bare) if bare else None
    if gpu_info is None and bare:
        gpu_info = seeded_gpu_info(bare)

    vcpus = _catalog_vcpus(bare) if bare else None
    # The fallback is deliberately only a capacity lookup for common catalog
    # sizes.  It never changes the GPU decision.
    if vcpus is None and re.match(r"^[a-z]\d", bare):
        size_vcpus = {
            "nano": 1,
            "micro": 1,
            "small": 1,
            "medium": 2,
            "large": 2,
            "xlarge": 4,
            "2xlarge": 8,
            "4xlarge": 16,
            "8xlarge": 32,
            "12xlarge": 48,
            "16xlarge": 64,
            "24xlarge": 96,
            "48xlarge": 192,
        }
        vcpus = size_vcpus.get(bare.rsplit(".", 1)[-1]) if bare else None

    known = bool(gpu_info or vcpus is not None)
    result = {
        "instance_type": original,
        "bare_instance_type": bare,
        "known": known,
        "is_gpu": gpu_info is not None,
        "gpu_info": gpu_info,
        "gpu_count": gpu_info.device_count if gpu_info else None,
        "device_count": gpu_info.device_count if gpu_info else None,
        "gpu_device_count": gpu_info.device_count if gpu_info else None,
        "gpu_model": gpu_info.model if gpu_info else None,
        "gpu_memory_mib": gpu_info.total_memory_mib if gpu_info else None,
        "vcpus": vcpus,
    }
    return result


def normalize_sagemaker_gpu_value(
    summed_value: Any, device_count: Optional[int]
) -> Optional[float]:
    """Convert SageMaker's summed GPU percentage to a per-device percentage."""
    if device_count is None or device_count <= 0:
        return None
    try:
        value = float(summed_value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value / device_count


def telemetry_status(
    usable: bool, unavailable_reason: Optional[str] = None
) -> Tuple[str, Optional[str]]:
    """Return the contract status pair, preserving ``None`` for usable data."""
    if usable:
        return "usable", None
    reason = unavailable_reason if unavailable_reason in TELEMETRY_UNAVAILABLE_REASONS else "no_datapoints"
    return "unavailable", reason


def percentile_summary(values: Iterable[Any]) -> Dict[str, Any]:
    """Compute local window statistics; an empty series remains unknown."""
    numbers: List[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            numbers.append(number)
    if not numbers:
        return {
            "average": None,
            "maximum": None,
            "p90": None,
            "p95": None,
            "p99": None,
            "sample_count": 0,
        }
    return {
        "average": float(np.mean(numbers)),
        "maximum": float(max(numbers)),
        "p90": float(np.percentile(numbers, 90)),
        "p95": float(np.percentile(numbers, 95)),
        "p99": float(np.percentile(numbers, 99)),
        "sample_count": len(numbers),
    }


def history_values(utilization: Dict[str, Any], metric: str) -> List[Any]:
    """Return a metric's average history, accepting common adapter shapes."""
    history = utilization.get("metric_history") or {}
    entry = history.get(metric) if isinstance(history, dict) else None
    if isinstance(entry, dict) and isinstance(entry.get("average"), list):
        return entry["average"]
    values = utilization.get(f"{metric}_values")
    return values if isinstance(values, list) else []


def metric_summary(
    utilization: Dict[str, Any], metric: str, values: Optional[Iterable[Any]] = None
) -> Dict[str, Any]:
    """Read a supplied summary or compute one from local window history."""
    summary = (utilization.get("metric_summary") or {}).get(metric)
    if isinstance(summary, dict):
        return summary
    return percentile_summary(values if values is not None else history_values(utilization, metric))


def signal_status(
    utilization: Dict[str, Any], signal: str
) -> Tuple[str, Optional[str]]:
    """Read a signal's status vocabulary without treating a missing field as usable."""
    status = utilization.get(f"{signal}_metric_status")
    reason = utilization.get(f"{signal}_metric_unavailable_reason")
    if status == "usable":
        return status, None
    return telemetry_status(False, reason)


def endpoint_variants(resource: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize endpoint variant metadata to one list, including empty lists."""
    metadata = resource.get("metadata") or {}
    variants = resource.get("variants") or metadata.get("variants") or []
    normalized: List[Dict[str, Any]] = []
    for raw_variant in variants:
        if not isinstance(raw_variant, dict):
            continue
        variant = dict(raw_variant)
        if "current_instance_count" not in variant and "instance_count" in variant:
            variant["current_instance_count"] = variant["instance_count"]
        if "initial_instance_count" not in variant and "instance_count" in variant:
            variant["initial_instance_count"] = variant["instance_count"]
        if "gpu_device_count" not in variant:
            resolved = resolve_sagemaker_instance_type(variant.get("instance_type"))
            variant["gpu_device_count"] = resolved["gpu_device_count"]
            variant.setdefault("vcpus", resolved["vcpus"])
        normalized.append(variant)
    return normalized


def variant_telemetry(
    utilization: Dict[str, Any], variant_name: str
) -> Dict[str, Any]:
    """Flatten an adapter's per-variant response while accepting direct stubs."""
    variants = utilization.get("variants")
    if isinstance(variants, dict) and variant_name in variants:
        value = variants[variant_name]
        return value if isinstance(value, dict) else {}
    return utilization


def variant_signal(
    utilization: Dict[str, Any], signal: str, variant_name: str = ""
) -> Dict[str, Any]:
    """Return normalized evidence for one signal from native adapter shapes."""
    value = variant_telemetry(utilization, variant_name)
    direct = value.get(signal)
    if isinstance(direct, dict):
        history = direct.get("metric_history") or {}
        summary = direct.get("metric_summary") or {}
        status = direct.get("metric_status")
        reason = direct.get("metric_unavailable_reason")
        return {
            "history": history,
            "summary": summary,
            "status": status,
            "reason": reason,
            "values": direct.get("values") or history.get("average") or [],
            "sample_count": (
                summary.get("sample_count")
                if summary.get("sample_count") is not None
                else direct.get("sample_count")
            ),
            "total": direct.get("total"),
        }
    history = value.get("metric_history") or {}
    summary = value.get("metric_summary") or {}
    history_entry = history.get(signal) if isinstance(history, dict) else {}
    summary_entry = summary.get(signal) if isinstance(summary, dict) else {}
    return {
        "history": history_entry if isinstance(history_entry, dict) else {},
        "summary": summary_entry if isinstance(summary_entry, dict) else {},
        "status": value.get(f"{signal}_metric_status"),
        "reason": value.get(f"{signal}_metric_unavailable_reason"),
        "values": value.get(f"{signal}_values")
        or (history_entry.get("average", []) if isinstance(history_entry, dict) else []),
        "total": value.get(f"{signal}_total"),
        "sample_count": (
            summary_entry.get("sample_count")
            if isinstance(summary_entry, dict)
            and summary_entry.get("sample_count") is not None
            else value.get(f"{signal}_sample_count")
            if value.get(f"{signal}_sample_count") is not None
            else value.get("sample_count")
        ),
    }


def signal_total(signal: Dict[str, Any]) -> Optional[float]:
    """Return a counter total, preserving ``None`` when no samples exist."""
    if signal.get("total") is not None:
        try:
            return float(signal["total"])
        except (TypeError, ValueError):
            return None
    values = signal.get("values") or []
    if not values:
        return None
    try:
        return float(sum(float(value) for value in values))
    except (TypeError, ValueError):
        return None


def gpu_endpoint_evidence(
    utilization: Dict[str, Any],
    device_count: Optional[int],
    variant_name: str = "",
) -> Dict[str, Any]:
    """Normalize endpoint GPU evidence and select normalized/enhanced forms."""
    value = variant_telemetry(utilization, variant_name)
    normalized = variant_signal(value, "gpuutilization_normalized")
    gpu = variant_signal(value, "gpuutilization")
    memory = variant_signal(value, "gpumemoryutilization")
    devices = value.get("accelerator_devices") or value.get("gpu_devices")
    if isinstance(devices, dict) and devices:
        device_p95: List[float] = []
        memory_p95: List[float] = []
        for device in devices.values():
            if not isinstance(device, dict):
                continue
            if device.get("p95") is not None:
                device_p95.append(float(device["p95"]))
            if device.get("memory_p95") is not None:
                memory_p95.append(float(device["memory_p95"]))
            util_values = device.get("utilization") or device.get("values") or device.get("average") or []
            mem_values = device.get("memory") or device.get("memory_values") or []
            util_summary = percentile_summary(util_values)
            mem_summary = percentile_summary(mem_values)
            if util_summary["p95"] is not None:
                device_p95.append(util_summary["p95"])
            if mem_summary["p95"] is not None:
                memory_p95.append(mem_summary["p95"])
        if device_p95:
            return {
                "gpu_p95_per_device": max(device_p95),
                "gpu_memory_p95_per_device": max(memory_p95) if memory_p95 else None,
                "gpu_aggregation": "busiest_accelerator",
                "status": "usable",
            }
    normalized_p95 = normalized["summary"].get("p95")
    if normalized_p95 is None:
        normalized_p95 = value.get("gpu_p95_normalized")
    memory_p95 = memory["summary"].get("p95") or value.get("gpu_memory_p95")
    if normalized_p95 is not None:
        return {
            "gpu_p95_per_device": float(normalized_p95),
            "gpu_memory_p95_per_device": float(memory_p95) if memory_p95 is not None else None,
            "gpu_aggregation": "normalized",
            "status": normalized.get("status") or "usable",
        }
    raw_p95 = gpu["summary"].get("p95") or value.get("gpu_p95")
    raw_memory_p95 = memory["summary"].get("p95") or value.get("gpu_memory_p95")
    if raw_p95 is None or device_count is None:
        return {
            "gpu_p95_per_device": None,
            "gpu_memory_p95_per_device": None,
            "gpu_aggregation": "mean_of_sum",
            "status": "unavailable",
        }
    return {
        "gpu_p95_per_device": normalize_sagemaker_gpu_value(raw_p95, device_count),
        "gpu_memory_p95_per_device": normalize_sagemaker_gpu_value(raw_memory_p95, device_count),
        "gpu_aggregation": "mean_of_sum",
        "status": gpu.get("status") or "usable",
    }


def resource_metadata(resource: Dict[str, Any]) -> Dict[str, Any]:
    """Return mutable resource metadata without replacing an existing mapping."""
    metadata = resource.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        resource["metadata"] = metadata
    return metadata


def lifecycle_auto_stop_match(
    content: Any, patterns: Iterable[str]
) -> Optional[str]:
    """Decode lifecycle content and return the first matching auto-stop pattern."""
    if not content:
        return None
    try:
        decoded = base64.b64decode(str(content)).decode("utf-8", errors="ignore")
    except (ValueError, binascii.Error, UnicodeError):
        return None
    lowered = decoded.casefold()
    for pattern in patterns:
        if str(pattern).casefold() in lowered:
            return str(pattern)
    return None


def training_job_family(name: Any, patterns: Optional[Iterable[str]] = None) -> str:
    """Remove the configured trailing run timestamp from a training job name."""
    value = str(name or "").strip()
    expressions = list(patterns or (r"[-_]\d{4,}.*$", r"[-_](\d{4}-\d{2}-\d{2}.*)$"))
    for expression in expressions:
        value = re.sub(expression, "", value)
    return value or str(name or "unknown")


def parse_time(value: Any) -> Optional[datetime]:
    """Parse an AWS timestamp or return ``None`` for an absent/invalid value."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def now_utc() -> datetime:
    """Return an aware UTC clock value suitable for age calculations."""
    return datetime.now(timezone.utc)


def create_sagemaker_price_disclosure(component: str) -> str:
    """Describe why a finding has no savings when the public price is absent."""
    return (
        f"Amazon SageMaker {component} price was unavailable; monthly savings are not estimated."
    )


__all__ = [
    "create_sagemaker_price_disclosure",
    "endpoint_variants",
    "history_values",
    "lifecycle_auto_stop_match",
    "metric_summary",
    "normalize_sagemaker_gpu_value",
    "parse_time",
    "percentile_summary",
    "resolve_sagemaker_instance_type",
    "resource_metadata",
    "signal_status",
    "telemetry_status",
    "training_job_family",
]
