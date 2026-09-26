"""Aurora excess reader instances check."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.aurora._common import avg_metric, list_aurora_clusters


def check_aurora_excess_reader_instances(
    aws_adapter,
    lookback_days: int = 14,
    min_readers: int = 2,
    cpu_threshold: float = 20.0,
    replica_lag_threshold_ms: float = 100.0,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    rds = aws_adapter.session.client("rds", region_name=region)
    cloudwatch = aws_adapter.session.client("cloudwatch", region_name=region)
    start_date = datetime.utcnow() - timedelta(days=lookback_days)
    end_date = datetime.utcnow()

    flagged: List[Dict[str, Any]] = []
    for cluster in list_aurora_clusters(rds):
        cluster_id = cluster.get("DBClusterIdentifier")
        if not cluster_id:
            continue
        readers = [m.get("DBInstanceIdentifier") for m in cluster.get("DBClusterMembers", []) if not m.get("IsClusterWriter")]
        readers = [r for r in readers if r]
        if len(readers) < min_readers:
            continue
        try:
            cpu_values: List[float] = []
            lag_values: List[float] = []
            for rid in readers:
                cpu = avg_metric(cloudwatch, "CPUUtilization", rid, start_date, end_date)
                lag = avg_metric(cloudwatch, "AuroraReplicaLag", rid, start_date, end_date)
                if cpu is not None:
                    cpu_values.append(cpu)
                if lag is not None:
                    lag_values.append(lag)
            if not cpu_values:
                continue
            avg_cpu = sum(cpu_values) / len(cpu_values)
            avg_lag = (sum(lag_values) / len(lag_values)) if lag_values else 0.0
            if avg_cpu > cpu_threshold or avg_lag > replica_lag_threshold_ms:
                continue

            flagged.append(
                {
                    "resource_id": cluster_id,
                    "resource_type": "aurora_cluster",
                    "resource_name": cluster_id,
                    "region": region,
                    "state": cluster.get("Status", "unknown"),
                    "metadata": {
                        "reader_instances": readers,
                        "reader_count": len(readers),
                        "min_readers": min_readers,
                        "lookback_days": lookback_days,
                        "avg_reader_cpu_utilization": round(avg_cpu, 2),
                        "avg_replica_lag_ms": round(avg_lag, 2),
                        "cpu_threshold": cpu_threshold,
                        "replica_lag_threshold_ms": replica_lag_threshold_ms,
                        "recommended_action": "reduce_reader_count",
                        "check_reason": create_check_reason(
                            "underutilized",
                            {
                                "avg_cpu": round(avg_cpu, 2),
                                "cpu_threshold": cpu_threshold,
                            },
                        ),
                    },
                }
            )
        except Exception as exc:
            print(f"Error checking Aurora excess readers for {cluster_id}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="aurora_excess_reader_instances",
        name="Aurora Excess Reader Instances",
        description="Identifies Aurora clusters with more reader instances than needed",
        resource_type="aurora_cluster",
        check_function=check_aurora_excess_reader_instances,
        default_action="reduce_reader_count",
        parameters={
            "lookback_days": 14,
            "min_readers": 2,
            "cpu_threshold": 20.0,
            "replica_lag_threshold_ms": 100.0,
            "region": None,
        },
    )
)

