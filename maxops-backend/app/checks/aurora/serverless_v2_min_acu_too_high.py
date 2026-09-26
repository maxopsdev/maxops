"""Aurora Serverless v2 minimum ACU too high check."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.aurora._common import list_aurora_clusters, to_float


def _avg_cluster_metric(
    cloudwatch_client,
    cluster_id: str,
    metric_name: str,
    start_date: datetime,
    end_date: datetime,
) -> Optional[float]:
    response = cloudwatch_client.get_metric_statistics(
        Namespace="AWS/RDS",
        MetricName=metric_name,
        Dimensions=[{"Name": "DBClusterIdentifier", "Value": cluster_id}],
        StartTime=start_date,
        EndTime=end_date,
        Period=3600,
        Statistics=["Average"],
    )
    datapoints = response.get("Datapoints", [])
    if not datapoints:
        return None
    values = [to_float(dp.get("Average")) for dp in datapoints if dp.get("Average") is not None]
    if not values:
        return None
    return sum(values) / len(values)


def check_aurora_serverless_v2_min_acu_too_high(
    aws_adapter,
    lookback_days: int = 14,
    min_acu_threshold: float = 2.0,
    avg_capacity_ratio_threshold: float = 0.5,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    rds = aws_adapter.session.client("rds", region_name=region)
    cloudwatch = aws_adapter.session.client("cloudwatch", region_name=region)
    start_date = datetime.utcnow() - timedelta(days=lookback_days)
    end_date = datetime.utcnow()

    flagged: List[Dict[str, Any]] = []
    for cluster in list_aurora_clusters(rds):
        cluster_id = cluster.get("DBClusterIdentifier")
        scaling = cluster.get("ServerlessV2ScalingConfiguration") or {}
        min_acu = scaling.get("MinCapacity")
        if not cluster_id or min_acu is None:
            continue
        min_acu_f = float(min_acu)
        if min_acu_f < min_acu_threshold:
            continue
        try:
            avg_capacity = _avg_cluster_metric(
                cloudwatch,
                cluster_id,
                "ServerlessDatabaseCapacity",
                start_date,
                end_date,
            )
            if avg_capacity is None:
                continue
            ratio = avg_capacity / max(min_acu_f, 0.01)
            if ratio > avg_capacity_ratio_threshold:
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
                        "min_acu": min_acu_f,
                        "avg_serverless_capacity_acu": round(avg_capacity, 2),
                        "avg_capacity_to_min_ratio": round(ratio, 3),
                        "min_acu_threshold": min_acu_threshold,
                        "avg_capacity_ratio_threshold": avg_capacity_ratio_threshold,
                        "recommended_action": "lower_min_acu",
                        "check_reason": create_check_reason(
                            "underutilized",
                            {"avg_cpu": round(ratio * 100.0, 2), "cpu_threshold": avg_capacity_ratio_threshold * 100.0},
                        ),
                    },
                }
            )
        except Exception as exc:
            print(f"Error checking Aurora Serverless v2 min ACU for {cluster_id}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="aurora_serverless_v2_min_acu_too_high",
        name="Aurora Serverless v2 Min ACU Too High",
        description="Identifies Aurora Serverless v2 clusters with high minimum ACU relative to observed load",
        resource_type="aurora_cluster",
        check_function=check_aurora_serverless_v2_min_acu_too_high,
        default_action="lower_min_acu",
        parameters={
            "lookback_days": 14,
            "min_acu_threshold": 2.0,
            "avg_capacity_ratio_threshold": 0.5,
            "region": None,
        },
    )
)

