"""
EBS Underutilized Volumes Check

Flags attached EBS volumes with very low I/O usage over a lookback window.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason
from app.utils.math_utils import avg, roundf


def _metric(u: Dict[str, Any], *keys: str, default: Any = 0) -> Any:
    for k in keys:
        if k in u and u[k] is not None:
            return u[k]
    return default


def check_ebs_underutilized_volume(
    aws_adapter,
    lookback_days: int = 14,
    iops_threshold: float = 5.0,
    throughput_mb_threshold: float = 1.0,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    volumes = aws_adapter.get_resources("ebs", region=region)
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=lookback_days)

    flagged: List[Dict[str, Any]] = []

    for volume in volumes:
        volume_id = volume.get("resource_id")
        if not volume_id:
            continue
        try:
            utilization = aws_adapter.get_resource_utilization(volume_id, "ebs", start_date, end_date, region)
            read_ops = _metric(utilization, "VolumeReadOps", "volumereadops", default=0)
            write_ops = _metric(utilization, "VolumeWriteOps", "volumewriteops", default=0)
            read_bytes = _metric(utilization, "VolumeReadBytes", "volumereadbytes", default=0)
            write_bytes = _metric(utilization, "VolumeWriteBytes", "volumewritebytes", default=0)

            avg_iops = avg(read_ops) + avg(write_ops)
            avg_throughput_mb = (avg(read_bytes) + avg(write_bytes)) / (1024 * 1024)

            if avg_iops <= iops_threshold and avg_throughput_mb <= throughput_mb_threshold:
                md = volume.setdefault("metadata", {})
                md["recommended_action"] = "review"
                md["lookback_days"] = lookback_days
                md["avg_iops"] = roundf(avg_iops, 4)
                md["avg_throughput_mb"] = roundf(avg_throughput_mb, 4)
                md["iops_threshold"] = iops_threshold
                md["throughput_mb_threshold"] = throughput_mb_threshold
                md["check_reason"] = create_check_reason(
                    "underutilized",
                    {
                        "volume_id": volume_id,
                        "lookback_days": lookback_days,
                        "avg_iops": roundf(avg_iops, 4),
                        "avg_throughput_mb": roundf(avg_throughput_mb, 4),
                        "iops_threshold": iops_threshold,
                        "throughput_mb_threshold": throughput_mb_threshold,
                    },
                )
                flagged.append(volume)
        except Exception as exc:
            print(f"Error checking EBS volume utilization for {volume_id}: {exc}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="ebs_underutilized_volume",
    name="EBS Underutilized Volume",
    description="Identifies attached EBS volumes with low I/O usage over a lookback window",
    resource_type="ebs",
    check_function=check_ebs_underutilized_volume,
    default_action="review",
    parameters={
        "lookback_days": 14,
        "iops_threshold": 5.0,
        "throughput_mb_threshold": 1.0,
        "region": None,
    }
))
