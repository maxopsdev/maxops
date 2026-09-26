"""Shared helpers for Aurora checks."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def list_aurora_clusters(rds_client) -> List[Dict[str, Any]]:
    clusters: List[Dict[str, Any]] = []
    marker: Optional[str] = None
    while True:
        params: Dict[str, Any] = {}
        if marker:
            params["Marker"] = marker
        response = rds_client.describe_db_clusters(**params)
        for cluster in response.get("DBClusters", []):
            engine = str(cluster.get("Engine") or "").lower()
            if engine.startswith("aurora"):
                clusters.append(cluster)
        marker = response.get("Marker")
        if not marker:
            break
    return clusters


def list_instances_for_cluster(rds_client, cluster_id: str) -> List[Dict[str, Any]]:
    response = rds_client.describe_db_instances(
        Filters=[{"Name": "db-cluster-id", "Values": [cluster_id]}]
    )
    return response.get("DBInstances", [])


def avg_metric(
    cloudwatch_client,
    metric_name: str,
    db_instance_identifier: str,
    start_date: datetime,
    end_date: datetime,
    period_seconds: int = 3600,
) -> Optional[float]:
    response = cloudwatch_client.get_metric_statistics(
        Namespace="AWS/RDS",
        MetricName=metric_name,
        Dimensions=[{"Name": "DBInstanceIdentifier", "Value": db_instance_identifier}],
        StartTime=start_date,
        EndTime=end_date,
        Period=period_seconds,
        Statistics=["Average"],
    )
    datapoints = response.get("Datapoints", [])
    if not datapoints:
        return None
    values = [to_float(dp.get("Average")) for dp in datapoints if dp.get("Average") is not None]
    if not values:
        return None
    return sum(values) / len(values)


def sum_metric(
    cloudwatch_client,
    metric_name: str,
    db_instance_identifier: str,
    start_date: datetime,
    end_date: datetime,
    period_seconds: int = 3600,
) -> float:
    response = cloudwatch_client.get_metric_statistics(
        Namespace="AWS/RDS",
        MetricName=metric_name,
        Dimensions=[{"Name": "DBInstanceIdentifier", "Value": db_instance_identifier}],
        StartTime=start_date,
        EndTime=end_date,
        Period=period_seconds,
        Statistics=["Sum"],
    )
    return sum(to_float(dp.get("Sum")) for dp in response.get("Datapoints", []))


def is_graviton_class(instance_class: str) -> bool:
    s = str(instance_class or "").lower()
    # db.m6g.large, db.r6gd.xlarge, db.t4g.medium
    return ".m" in s and "g." in s or ".r" in s and "g." in s or ".t" in s and "g." in s
