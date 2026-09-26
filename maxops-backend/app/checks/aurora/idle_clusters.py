"""Aurora idle clusters check."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.aurora._common import avg_metric, list_aurora_clusters, list_instances_for_cluster


def check_aurora_idle_clusters(
    aws_adapter,
    lookback_days: int = 14,
    cpu_threshold: float = 10.0,
    connections_threshold: float = 5.0,
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
        try:
            instances = list_instances_for_cluster(rds, cluster_id)
            if not instances:
                continue
            cpu_values: List[float] = []
            conn_values: List[float] = []
            for inst in instances:
                instance_id = inst.get("DBInstanceIdentifier")
                if not instance_id:
                    continue
                cpu = avg_metric(cloudwatch, "CPUUtilization", instance_id, start_date, end_date)
                conn = avg_metric(cloudwatch, "DatabaseConnections", instance_id, start_date, end_date)
                if cpu is not None:
                    cpu_values.append(cpu)
                if conn is not None:
                    conn_values.append(conn)
            if not cpu_values or not conn_values:
                continue
            avg_cpu = sum(cpu_values) / len(cpu_values)
            avg_conn = sum(conn_values) / len(conn_values)
            if avg_cpu > cpu_threshold or avg_conn > connections_threshold:
                continue

            flagged.append(
                {
                    "resource_id": cluster_id,
                    "resource_type": "aurora_cluster",
                    "resource_name": cluster_id,
                    "region": region,
                    "state": cluster.get("Status", "unknown"),
                    "metadata": {
                        "lookback_days": lookback_days,
                        "avg_cpu_utilization": round(avg_cpu, 2),
                        "avg_database_connections": round(avg_conn, 2),
                        "cpu_threshold": cpu_threshold,
                        "connections_threshold": connections_threshold,
                        "recommended_action": "downsize_or_pause_nonprod",
                        "check_reason": create_check_reason(
                            "idle",
                            {
                                "idle_days": lookback_days,
                                "avg_cpu": round(avg_cpu, 2),
                                "cpu_threshold": cpu_threshold,
                            },
                        ),
                    },
                }
            )
        except Exception as exc:
            print(f"Error checking Aurora idle cluster {cluster_id}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="aurora_idle_clusters",
        name="Aurora Idle Clusters",
        description="Identifies Aurora clusters with low CPU and low connections",
        resource_type="aurora_cluster",
        check_function=check_aurora_idle_clusters,
        default_action="downsize_or_pause_nonprod",
        parameters={
            "lookback_days": 14,
            "cpu_threshold": 10.0,
            "connections_threshold": 5.0,
            "region": None,
        },
    )
)

