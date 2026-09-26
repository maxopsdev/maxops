"""
Redshift Check - Idle Clusters (No Query Activity)

Flags Redshift clusters with no query activity for a configurable lookback window.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _get_clusters(redshift_client) -> List[Dict[str, Any]]:
    clusters: List[Dict[str, Any]] = []
    marker: Optional[str] = None
    while True:
        params: Dict[str, Any] = {}
        if marker:
            params["Marker"] = marker
        response = redshift_client.describe_clusters(**params)
        clusters.extend(response.get("Clusters", []))
        marker = response.get("Marker")
        if not marker:
            break
    return clusters


def _estimate_total_queries(datapoints: List[Dict[str, Any]], period_seconds: int) -> float:
    # QueriesCompletedPerSecond is a rate metric. Approximate total queries by
    # sum(avg_qps * period_seconds) over the window.
    total_queries = 0.0
    for point in datapoints:
        avg_qps = _to_float(point.get("Average"))
        total_queries += avg_qps * period_seconds
    return total_queries


def check_redshift_idle_clusters_no_query_activity(
    aws_adapter,
    lookback_days: int = 7,
    max_total_queries: float = 0.0,
    cluster_statuses: Optional[List[str]] = None,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Identify Redshift clusters with no/near-zero query activity.
    """
    if lookback_days <= 0:
        return []

    redshift = aws_adapter.session.client("redshift", region_name=region)
    cloudwatch = aws_adapter.session.client("cloudwatch", region_name=region)

    statuses = {s.upper() for s in (cluster_statuses or ["available"])}
    period_seconds = 3600
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=lookback_days)

    flagged: List[Dict[str, Any]] = []
    clusters = _get_clusters(redshift)

    for cluster in clusters:
        cluster_id = cluster.get("ClusterIdentifier")
        if not cluster_id:
            continue

        status = str(cluster.get("ClusterStatus") or "").upper()
        if statuses and status not in statuses:
            continue

        try:
            metric_response = cloudwatch.get_metric_statistics(
                Namespace="AWS/Redshift",
                MetricName="QueriesCompletedPerSecond",
                Dimensions=[{"Name": "ClusterIdentifier", "Value": cluster_id}],
                StartTime=start_date,
                EndTime=end_date,
                Period=period_seconds,
                Statistics=["Average"],
            )
            datapoints = metric_response.get("Datapoints", [])
            total_queries = _estimate_total_queries(datapoints, period_seconds)

            if total_queries <= max_total_queries:
                cluster_resource: Dict[str, Any] = {
                    "resource_id": cluster_id,
                    "resource_type": "redshift_cluster",
                    "resource_name": cluster_id,
                    "region": region,
                    "state": cluster.get("ClusterStatus", "unknown"),
                    "metadata": {
                        "ClusterIdentifier": cluster_id,
                        "NodeType": cluster.get("NodeType"),
                        "NumberOfNodes": cluster.get("NumberOfNodes"),
                        "ClusterType": cluster.get("ClusterType"),
                        "recommended_action": "rightsize_or_pause",
                        "lookback_days": lookback_days,
                        "max_total_queries": max_total_queries,
                        "estimated_total_queries": round(total_queries, 2),
                        "data_points": len(datapoints),
                        "check_reason": create_check_reason(
                            "idle",
                            {
                                "resource": "redshift_cluster",
                                "cluster_id": cluster_id,
                                "lookback_days": lookback_days,
                                "estimated_total_queries": round(total_queries, 2),
                                "max_total_queries": max_total_queries,
                            },
                        ),
                    },
                }
                flagged.append(cluster_resource)
        except Exception as exc:
            print(f"Error checking Redshift cluster query activity for {cluster_id}: {exc}")
            continue

    return flagged


check_registry.register(
    CheckMetadata(
        check_id="redshift_idle_clusters_no_query_activity",
        name="Redshift Idle Clusters (No Query Activity)",
        description="Identifies Redshift clusters with no query activity over a lookback window",
        resource_type="redshift_cluster",
        check_function=check_redshift_idle_clusters_no_query_activity,
        default_action="rightsize_or_pause",
        parameters={
            "lookback_days": 7,
            "max_total_queries": 0.0,
            "cluster_statuses": ["available"],
            "region": None,
        },
    )
)
