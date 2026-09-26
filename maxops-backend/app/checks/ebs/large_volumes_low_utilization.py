"""EBS Large Volumes with Low Utilization Check."""
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


def check_ebs_large_volumes_low_utilization(
    aws_adapter,
    lookback_days: int = 14,
    min_size_gb: int = 500,
    iops_threshold: float = 5.0,
    throughput_mb_threshold: float = 1.0,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Flag large attached EBS volumes with very low observed I/O usage."""
    volumes = aws_adapter.get_resources("ebs", region=region)
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=lookback_days)

    flagged: List[Dict[str, Any]] = []

    for volume in volumes:
        volume_id = volume.get("resource_id")
        if not volume_id:
            continue

        metadata = volume.get("metadata", {})
        try:
            size_gb = float(
                metadata.get("size")
                or metadata.get("Size")
                or metadata.get("size_gb")
                or 0
            )
        except (TypeError, ValueError):
            continue

        if size_gb < min_size_gb:
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
                # Only flag resources where the configured action can make a valid change.
                # gp3 volumes already at baseline (3000 IOPS / 125 MB/s) cannot be tuned lower.
                volume_type = str(
                    metadata.get("volume_type")
                    or metadata.get("VolumeType")
                    or ""
                ).lower()
                try:
                    current_iops = int(metadata.get("iops") or metadata.get("Iops") or 0)
                except (TypeError, ValueError):
                    current_iops = 0
                try:
                    current_throughput = int(metadata.get("throughput") or metadata.get("Throughput") or 0)
                except (TypeError, ValueError):
                    current_throughput = 0

                if volume_type == "gp3" and current_iops <= 3000 and current_throughput <= 125:
                    continue

                md = volume.setdefault("metadata", {})
                md["recommended_action"] = "downsize_volume"
                md["lookback_days"] = lookback_days
                md["size_gb"] = round(size_gb, 2)
                md["min_size_gb"] = min_size_gb
                md["avg_iops"] = roundf(avg_iops, 4)
                md["avg_throughput_mb"] = roundf(avg_throughput_mb, 4)
                md["iops_threshold"] = iops_threshold
                md["throughput_mb_threshold"] = throughput_mb_threshold
                md["check_reason"] = create_check_reason(
                    "large_volume_low_utilization",
                    {
                        "volume_id": volume_id,
                        "size_gb": round(size_gb, 2),
                        "avg_iops": roundf(avg_iops, 4),
                        "avg_throughput_mb": roundf(avg_throughput_mb, 4),
                        "iops_threshold": iops_threshold,
                        "throughput_mb_threshold": throughput_mb_threshold,
                    },
                )
                flagged.append(volume)
        except Exception as exc:
            print(f"Error checking large EBS volume utilization for {volume_id}: {exc}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="ebs_large_volumes_low_utilization",
    name="EBS Large Volumes with Low Utilization",
    description=(
        "Identifies large EBS volumes with low throughput/IOPS that may be "
        "downsized. EBS cannot shrink in place: downsizing means a snapshot and "
        "restore onto a smaller volume, with a filesystem resize first."
    ),
    resource_type="ebs",
    check_function=check_ebs_large_volumes_low_utilization,
    default_action="ebs_downsize_volume",
    parameters={
        "lookback_days": 14,
        "min_size_gb": 500,
        "iops_threshold": 5.0,
        "throughput_mb_threshold": 1.0,
        "region": None,
    },
))
