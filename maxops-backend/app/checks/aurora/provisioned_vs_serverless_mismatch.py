"""Aurora provisioned vs serverless mismatch check."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.aurora._common import avg_metric, list_aurora_clusters, list_instances_for_cluster


def check_aurora_provisioned_vs_serverless_mismatch(
    aws_adapter,
    lookback_days: int = 14,
    cpu_threshold: float = 15.0,
    connections_threshold: float = 10.0,
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
        # Skip clusters that are already serverless v2 configured.
        if cluster.get("ServerlessV2ScalingConfiguration"):
            continue
        try:
            instances = list_instances_for_cluster(rds, cluster_id)
            if not instances:
                continue
            cpu_vals: List[float] = []
            conn_vals: List[float] = []
            for inst in instances:
                iid = inst.get("DBInstanceIdentifier")
                if not iid:
                    continue
                cpu = avg_metric(cloudwatch, "CPUUtilization", iid, start_date, end_date)
                conn = avg_metric(cloudwatch, "DatabaseConnections", iid, start_date, end_date)
                if cpu is not None:
                    cpu_vals.append(cpu)
                if conn is not None:
                    conn_vals.append(conn)
            if not cpu_vals or not conn_vals:
                continue
            avg_cpu = sum(cpu_vals) / len(cpu_vals)
            avg_conn = sum(conn_vals) / len(conn_vals)
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
                        "recommended_action": "evaluate_serverless_v2",
                        "check_reason": create_check_reason(
                            "underutilized",
                            {"avg_cpu": round(avg_cpu, 2), "cpu_threshold": cpu_threshold},
                        ),
                    },
                }
            )
        except Exception as exc:
            print(f"Error checking provisioned/serverless mismatch for {cluster_id}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="aurora_provisioned_vs_serverless_mismatch",
        name="Aurora Provisioned vs Serverless Mismatch",
        description="Identifies provisioned Aurora clusters that may be better suited for Serverless v2",
        resource_type="aurora_cluster",
        check_function=check_aurora_provisioned_vs_serverless_mismatch,
        default_action="evaluate_serverless_v2",
        parameters={
            "lookback_days": 14,
            "cpu_threshold": 15.0,
            "connections_threshold": 10.0,
            "region": None,
        },
    )
)

