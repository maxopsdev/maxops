"""Shared GPU gating and metadata helpers for EC2 checks."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.utils.ec2_gpu_info import EC2GpuInfo


def gpu_unavailable_reason(utilization: Dict[str, Any]) -> str:
    """Return the recorded reason for unusable GPU telemetry."""
    return str(utilization.get("gpu_metric_unavailable_reason") or "unavailable")


def gpu_is_idle(
    utilization: Dict[str, Any],
    gpu_threshold: float = 5.0,
    gpu_max_threshold: float = 15.0,
) -> bool:
    """Return true only when usable GPU telemetry passes both idle thresholds.

    The p95 and maximum are calculated over the across-device maximum series,
    so every discovered device must remain quiet for the instance to qualify.
    Missing telemetry or summary values returns ``False``.
    """
    if utilization.get("gpu_metric_status") != "usable":
        return False
    summary = utilization.get("metric_summary", {}).get("gpuutilization", {})
    p95 = summary.get("p95")
    maximum = summary.get("maximum")
    return (
        p95 is not None
        and maximum is not None
        and float(p95) < gpu_threshold
        and float(maximum) < gpu_max_threshold
    )


def busy_device_count(
    utilization: Dict[str, Any], gpu_threshold: float
) -> Optional[int]:
    """Count devices whose window mean exceeds ``gpu_threshold``.

    Returns ``None`` when no device has GPU telemetry; zero is a valid count
    when devices reported data and all stayed below the threshold.
    """
    devices = utilization.get("metric_history", {}).get("gpu_devices")
    if not isinstance(devices, dict):
        return None

    device_means = []
    for device in devices.values():
        values = device.get("average") if isinstance(device, dict) else None
        if not values:
            continue
        try:
            device_means.append(float(sum(values) / len(values)))
        except (TypeError, ValueError):
            continue
    if not device_means:
        return None
    return sum(mean > gpu_threshold for mean in device_means)


def gpu_metadata(
    info: EC2GpuInfo,
    utilization: Dict[str, Any],
    gpu_threshold: float,
) -> Dict[str, Any]:
    """Build stable GPU evidence using the same threshold as the idle gate."""
    summary = utilization.get("metric_summary", {}).get("gpuutilization", {})
    return {
        "gpu_device_count": info.device_count,
        "gpu_fractional": info.fractional,
        "gpu_max_utilization": summary.get("maximum"),
        "gpu_p95_utilization": summary.get("p95"),
        "gpu_mean_utilization": summary.get("average"),
        "gpu_busy_device_count": busy_device_count(utilization, gpu_threshold),
        "gpu_metric_status": utilization.get("gpu_metric_status", "unavailable"),
        "gpu_metric_source": utilization.get("gpu_metric_source"),
    }
