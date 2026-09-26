"""Aurora cross-region replication unused check."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.aurora._common import avg_metric, list_aurora_clusters


def _cluster_id_from_arn(arn: str) -> str:
    # arn:aws:rds:region:account:cluster:cluster-id
    return arn.split(":")[-1]


def _writer_instance_id(cluster: Dict[str, Any]) -> Optional[str]:
    for member in cluster.get("DBClusterMembers", []) or []:
        if member.get("IsClusterWriter"):
            return member.get("DBInstanceIdentifier")
    return None


def check_aurora_cross_region_replication_unused(
    aws_adapter,
    lookback_days: int = 14,
    cpu_threshold: float = 10.0,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    rds = aws_adapter.session.client("rds", region_name=region)
    cloudwatch = aws_adapter.session.client("cloudwatch", region_name=region)
    start_date = datetime.utcnow() - timedelta(days=lookback_days)
    end_date = datetime.utcnow()

    clusters_by_id: Dict[str, Dict[str, Any]] = {
        c.get("DBClusterIdentifier"): c for c in list_aurora_clusters(rds) if c.get("DBClusterIdentifier")
    }
    flagged: List[Dict[str, Any]] = []

    marker: Optional[str] = None
    while True:
        params: Dict[str, Any] = {}
        if marker:
            params["Marker"] = marker
        response = rds.describe_global_clusters(**params)
        for gc in response.get("GlobalClusters", []):
            global_id = gc.get("GlobalClusterIdentifier")
            members = gc.get("GlobalClusterMembers", [])
            if not global_id or len(members) <= 1:
                continue
            # Evaluate writer cluster in current region context when available.
            writer_member = next((m for m in members if m.get("IsWriter")), None)
            if not writer_member:
                continue
            writer_cluster_id = _cluster_id_from_arn(str(writer_member.get("DBClusterArn") or ""))
            cluster = clusters_by_id.get(writer_cluster_id)
            if not cluster:
                continue
            writer_instance = _writer_instance_id(cluster)
            if not writer_instance:
                continue
            try:
                avg_cpu = avg_metric(cloudwatch, "CPUUtilization", writer_instance, start_date, end_date)
                if avg_cpu is None or avg_cpu > cpu_threshold:
                    continue
                secondary_regions = sorted(
                    {
                        str(m.get("Readers", [None])[0] or "").split(":")[3]
                        for m in members
                        if not m.get("IsWriter")
                    }
                )
                flagged.append(
                    {
                        "resource_id": global_id,
                        "resource_type": "aurora_global_cluster",
                        "resource_name": global_id,
                        "region": region,
                        "state": "available",
                        "metadata": {
                            "writer_cluster_id": writer_cluster_id,
                            "writer_instance_id": writer_instance,
                            "global_members_count": len(members),
                            "secondary_regions": [r for r in secondary_regions if r],
                            "lookback_days": lookback_days,
                            "avg_writer_cpu_utilization": round(avg_cpu, 2),
                            "cpu_threshold": cpu_threshold,
                            "recommended_action": "review_cross_region_replication_need",
                            "check_reason": create_check_reason(
                                "unused",
                                {"reason": "Global cluster replication present with low observed utilization"},
                            ),
                        },
                    }
                )
            except Exception as exc:
                print(f"Error checking Aurora global cluster usage for {global_id}: {exc}")
        marker = response.get("Marker")
        if not marker:
            break
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="aurora_cross_region_replication_unused",
        name="Aurora Cross-Region Replication Unused",
        description="Identifies Aurora Global Database setups that may be unnecessary for current load",
        resource_type="aurora_global_cluster",
        check_function=check_aurora_cross_region_replication_unused,
        default_action="review_cross_region_replication_need",
        parameters={
            "lookback_days": 14,
            "cpu_threshold": 10.0,
            "region": None,
        },
    )
)

