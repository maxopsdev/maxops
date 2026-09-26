"""Aurora overprovisioned instance class check."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.aurora._common import avg_metric, list_aurora_clusters, list_instances_for_cluster


def check_aurora_overprovisioned_instance_class(
    aws_adapter,
    lookback_days: int = 14,
    cpu_threshold: float = 25.0,
    freeable_memory_threshold_gb: float = 2.0,
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
        for inst in list_instances_for_cluster(rds, cluster_id):
            instance_id = inst.get("DBInstanceIdentifier")
            instance_class = inst.get("DBInstanceClass")
            if not instance_id or not instance_class:
                continue
            try:
                avg_cpu = avg_metric(cloudwatch, "CPUUtilization", instance_id, start_date, end_date)
                avg_freeable_mem = avg_metric(cloudwatch, "FreeableMemory", instance_id, start_date, end_date)
                if avg_cpu is None or avg_freeable_mem is None:
                    continue
                avg_freeable_gb = avg_freeable_mem / (1024 * 1024 * 1024)
                if avg_cpu > cpu_threshold or avg_freeable_gb < freeable_memory_threshold_gb:
                    continue
                flagged.append(
                    {
                        "resource_id": instance_id,
                        "resource_type": "aurora_instance",
                        "resource_name": instance_id,
                        "region": region,
                        "state": inst.get("DBInstanceStatus", "unknown"),
                        "metadata": {
                            "cluster_id": cluster_id,
                            "db_instance_class": instance_class,
                            "lookback_days": lookback_days,
                            "avg_cpu_utilization": round(avg_cpu, 2),
                            "avg_freeable_memory_gb": round(avg_freeable_gb, 2),
                            "cpu_threshold": cpu_threshold,
                            "freeable_memory_threshold_gb": freeable_memory_threshold_gb,
                            "recommended_action": "downsize_instance_class",
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
                print(f"Error checking Aurora instance class sizing for {instance_id}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="aurora_overprovisioned_instance_class",
        name="Aurora Overprovisioned Instance Class",
        description="Identifies Aurora instances with low CPU and high freeable memory",
        resource_type="aurora_instance",
        check_function=check_aurora_overprovisioned_instance_class,
        default_action="downsize_instance_class",
        parameters={
            "lookback_days": 14,
            "cpu_threshold": 25.0,
            "freeable_memory_threshold_gb": 2.0,
            "region": None,
        },
    )
)

