"""EBS Underutilized Provisioned IOPS Check."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason
from app.utils.math_utils import avg, roundf, safe_div


def _metric(u: Dict[str, Any], *keys: str, default: Any = 0) -> Any:
    for k in keys:
        if k in u and u[k] is not None:
            return u[k]
    return default


def check_ebs_underutilized_provisioned_iops(
    aws_adapter,
    lookback_days: int = 14,
    utilization_threshold_pct: float = 30.0,
    min_provisioned_iops: int = 3000,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Flag io1/io2 volumes where provisioned IOPS greatly exceeds observed usage."""
    volumes = aws_adapter.get_resources("ebs", region=region)
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=lookback_days)

    flagged: List[Dict[str, Any]] = []

    for volume in volumes:
        volume_id = volume.get("resource_id")
        if not volume_id:
            continue

        md = volume.get("metadata", {})
        volume_type = (md.get("volume_type") or md.get("VolumeType") or "").lower()
        if volume_type not in {"io1", "io2"}:
            continue

        provisioned_iops = md.get("iops") or md.get("Iops") or 0
        if provisioned_iops < min_provisioned_iops:
            continue

        try:
            utilization = aws_adapter.get_resource_utilization(volume_id, "ebs", start_date, end_date, region)
            read_ops = _metric(utilization, "VolumeReadOps", "volumereadops", default=0)
            write_ops = _metric(utilization, "VolumeWriteOps", "volumewriteops", default=0)
            avg_iops = avg(read_ops) + avg(write_ops)

            utilization_pct = safe_div(avg_iops, provisioned_iops, default=0.0) * 100.0
            if utilization_pct < utilization_threshold_pct:
                md = volume.setdefault("metadata", {})
                md["recommended_action"] = "ebs_reduce_iops"
                md["recommended_actions"] = ["ebs_reduce_iops"]
                md["lookback_days"] = lookback_days
                md["avg_iops"] = roundf(avg_iops, 4)
                md["provisioned_iops"] = int(provisioned_iops)
                md["iops_utilization_pct"] = roundf(utilization_pct, 2)
                md["iops_utilization_threshold_pct"] = utilization_threshold_pct
                md["check_reason"] = create_check_reason(
                    "underutilized_provisioned_iops",
                    {
                        "volume_id": volume_id,
                        "volume_type": volume_type,
                        "avg_iops": roundf(avg_iops, 4),
                        "provisioned_iops": int(provisioned_iops),
                        "utilization_pct": roundf(utilization_pct, 2),
                        "threshold_pct": utilization_threshold_pct,
                    },
                )
                flagged.append(volume)
        except Exception as exc:
            print(f"Error checking EBS provisioned IOPS utilization for {volume_id}: {exc}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="ebs_underutilized_provisioned_iops",
    name="EBS Underutilized Provisioned IOPS",
    description="Identifies io1/io2 EBS volumes with provisioned IOPS far above actual usage",
    resource_type="ebs",
    check_function=check_ebs_underutilized_provisioned_iops,
    default_action="ebs_reduce_iops",
    parameters={
        "lookback_days": 14,
        "utilization_threshold_pct": 30.0,
        "min_provisioned_iops": 3000,
        "region": None,
    },
))
