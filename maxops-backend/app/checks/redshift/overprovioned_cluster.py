"""Redshift overprovisioned clusters check."""
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


def _compute_node_ids(cluster: Dict[str, Any]) -> List[str]:
    node_ids: List[str] = []
    for node in cluster.get("ClusterNodes", []) or []:
        node_id = node.get("NodeID")
        if not node_id:
            continue

        role = str(node.get("NodeRole") or "").upper()
        if role == "LEADER":
            continue
        if str(node_id).lower() == "leader":
            continue
        node_ids.append(str(node_id))
    return node_ids


def _metric_average(
    cloudwatch_client,
    cluster_id: str,
    node_id: str,
    metric_name: str,
    start_date: datetime,
    end_date: datetime,
    period_seconds: int,
) -> Optional[float]:
    response = cloudwatch_client.get_metric_statistics(
        Namespace="AWS/Redshift",
        MetricName=metric_name,
        Dimensions=[
            {"Name": "ClusterIdentifier", "Value": cluster_id},
            {"Name": "NodeID", "Value": node_id},
        ],
        StartTime=start_date,
        EndTime=end_date,
        Period=period_seconds,
        Statistics=["Average"],
    )
    datapoints = response.get("Datapoints", [])
    if not datapoints:
        return None
    values = [_to_float(dp.get("Average")) for dp in datapoints if dp.get("Average") is not None]
    if not values:
        return None
    return sum(values) / len(values)


def check_redshift_overprovisioned_cluster(
    aws_adapter,
    lookback_days: int = 14,
    cpu_threshold: float = 20.0,
    disk_usage_threshold: float = 50.0,
    cluster_statuses: Optional[List[str]] = None,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Identify Redshift clusters with low compute-node CPU and disk usage.
    Leader node is excluded from utilization analysis.
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
    for cluster in _get_clusters(redshift):
        cluster_id = cluster.get("ClusterIdentifier")
        if not cluster_id:
            continue

        status = str(cluster.get("ClusterStatus") or "").upper()
        if statuses and status not in statuses:
            continue

        compute_nodes = _compute_node_ids(cluster)
        if not compute_nodes:
            # Skip if we cannot isolate compute nodes.
            continue

        cpu_by_node: Dict[str, float] = {}
        disk_by_node: Dict[str, float] = {}

        try:
            for node_id in compute_nodes:
                cpu_avg = _metric_average(
                    cloudwatch, cluster_id, node_id, "CPUUtilization", start_date, end_date, period_seconds
                )
                disk_avg = _metric_average(
                    cloudwatch, cluster_id, node_id, "PercentageDiskSpaceUsed", start_date, end_date, period_seconds
                )
                if cpu_avg is not None:
                    cpu_by_node[node_id] = cpu_avg
                if disk_avg is not None:
                    disk_by_node[node_id] = disk_avg

            # Need both signals on at least one compute node to assess overprovisioning.
            common_nodes = sorted(set(cpu_by_node.keys()) & set(disk_by_node.keys()))
            if not common_nodes:
                continue

            cpu_values = [cpu_by_node[n] for n in common_nodes]
            disk_values = [disk_by_node[n] for n in common_nodes]

            max_cpu = max(cpu_values)
            avg_cpu = sum(cpu_values) / len(cpu_values)
            max_disk = max(disk_values)
            avg_disk = sum(disk_values) / len(disk_values)

            # Flag only when all observed compute nodes stay below thresholds.
            if max_cpu <= cpu_threshold and max_disk <= disk_usage_threshold:
                flagged.append(
                    {
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
                            "compute_nodes_evaluated": common_nodes,
                            "lookback_days": lookback_days,
                            "avg_cpu_utilization": round(avg_cpu, 2),
                            "max_cpu_utilization": round(max_cpu, 2),
                            "cpu_threshold": cpu_threshold,
                            "avg_disk_space_used_pct": round(avg_disk, 2),
                            "max_disk_space_used_pct": round(max_disk, 2),
                            "disk_usage_threshold": disk_usage_threshold,
                            "recommended_action": "rightsize_cluster",
                            "check_reason": create_check_reason(
                                "unused",
                                {
                                    "reason": (
                                        f"Compute-node CPU <= {cpu_threshold}% and disk usage <= "
                                        f"{disk_usage_threshold}% over last {lookback_days} days"
                                    )
                                },
                            ),
                        },
                    }
                )
        except Exception as exc:
            print(f"Error checking Redshift cluster utilization for {cluster_id}: {exc}")
            continue

    return flagged


check_registry.register(
    CheckMetadata(
        check_id="redshift_overprovisioned_cluster",
        name="Redshift Overprovisioned Cluster",
        description=(
            "Identifies Redshift clusters with low compute-node CPU and disk usage "
            "(leader node excluded) over a lookback window"
        ),
        resource_type="redshift_cluster",
        check_function=check_redshift_overprovisioned_cluster,
        default_action="rightsize_cluster",
        parameters={
            "lookback_days": 14,
            "cpu_threshold": 20.0,
            "disk_usage_threshold": 50.0,
            "cluster_statuses": ["available"],
            "region": None,
        },
    )
)
