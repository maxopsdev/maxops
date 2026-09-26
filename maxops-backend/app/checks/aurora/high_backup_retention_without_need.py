"""Aurora high backup retention check."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.aurora._common import list_aurora_clusters


def check_aurora_high_backup_retention_without_need(
    aws_adapter,
    max_retention_days: int = 7,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    rds = aws_adapter.session.client("rds", region_name=region)
    flagged: List[Dict[str, Any]] = []
    for cluster in list_aurora_clusters(rds):
        cluster_id = cluster.get("DBClusterIdentifier")
        if not cluster_id:
            continue
        retention = int(cluster.get("BackupRetentionPeriod") or 0)
        if retention <= max_retention_days:
            continue
        flagged.append(
            {
                "resource_id": cluster_id,
                "resource_type": "aurora_cluster",
                "resource_name": cluster_id,
                "region": region,
                "state": cluster.get("Status", "unknown"),
                "metadata": {
                    "backup_retention_period_days": retention,
                    "max_retention_days": max_retention_days,
                    "recommended_action": "reduce_backup_retention",
                    "check_reason": create_check_reason(
                        "old",
                        {"age_days": retention, "min_age_days": max_retention_days},
                    ),
                },
            }
        )
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="aurora_high_backup_retention_without_need",
        name="Aurora High Backup Retention Without Need",
        description="Identifies Aurora clusters with backup retention above threshold",
        resource_type="aurora_cluster",
        check_function=check_aurora_high_backup_retention_without_need,
        default_action="reduce_backup_retention",
        parameters={
            "max_retention_days": 7,
            "region": None,
        },
    )
)

