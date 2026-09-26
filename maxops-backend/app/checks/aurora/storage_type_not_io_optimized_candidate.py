"""Aurora storage type I/O-optimized mismatch check."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.aurora._common import avg_metric, list_aurora_clusters


def _writer_instance_id(cluster: Dict[str, Any]) -> Optional[str]:
    for member in cluster.get("DBClusterMembers", []) or []:
        if member.get("IsClusterWriter"):
            return member.get("DBInstanceIdentifier")
    return None


def check_aurora_storage_type_not_io_optimized_candidate(
    aws_adapter,
    lookback_days: int = 14,
    max_read_iops: float = 1000.0,
    max_write_iops: float = 500.0,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    rds = aws_adapter.session.client("rds", region_name=region)
    cloudwatch = aws_adapter.session.client("cloudwatch", region_name=region)
    start_date = datetime.utcnow() - timedelta(days=lookback_days)
    end_date = datetime.utcnow()

    flagged: List[Dict[str, Any]] = []
    for cluster in list_aurora_clusters(rds):
        cluster_id = cluster.get("DBClusterIdentifier")
        storage_type = str(cluster.get("StorageType") or "").lower()
        if not cluster_id:
            continue
        # aurora i/o optimized cluster storage type
        if "iopt" not in storage_type:
            continue
        writer = _writer_instance_id(cluster)
        if not writer:
            continue
        try:
            read_iops = avg_metric(cloudwatch, "ReadIOPS", writer, start_date, end_date)
            write_iops = avg_metric(cloudwatch, "WriteIOPS", writer, start_date, end_date)
            if read_iops is None or write_iops is None:
                continue
            if read_iops > max_read_iops or write_iops > max_write_iops:
                continue
            flagged.append(
                {
                    "resource_id": cluster_id,
                    "resource_type": "aurora_cluster",
                    "resource_name": cluster_id,
                    "region": region,
                    "state": cluster.get("Status", "unknown"),
                    "metadata": {
                        "storage_type": storage_type,
                        "writer_instance_id": writer,
                        "lookback_days": lookback_days,
                        "avg_read_iops": round(read_iops, 2),
                        "avg_write_iops": round(write_iops, 2),
                        "max_read_iops": max_read_iops,
                        "max_write_iops": max_write_iops,
                        "recommended_action": "evaluate_standard_storage_type",
                        "check_reason": create_check_reason(
                            "underutilized",
                            {"avg_iops": round(read_iops + write_iops, 2)},
                        ),
                    },
                }
            )
        except Exception as exc:
            print(f"Error checking Aurora storage type for {cluster_id}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="aurora_storage_type_not_io_optimized_candidate",
        name="Aurora Storage Type Not I/O-Optimized Candidate",
        description="Identifies Aurora I/O-Optimized clusters with low I/O patterns",
        resource_type="aurora_cluster",
        check_function=check_aurora_storage_type_not_io_optimized_candidate,
        default_action="evaluate_standard_storage_type",
        parameters={
            "lookback_days": 14,
            "max_read_iops": 1000.0,
            "max_write_iops": 500.0,
            "region": None,
        },
    )
)

