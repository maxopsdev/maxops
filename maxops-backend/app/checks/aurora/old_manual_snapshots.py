"""Aurora old manual snapshots check."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry


def check_aurora_old_manual_snapshots(
    aws_adapter,
    min_age_days: int = 30,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    rds = aws_adapter.session.client("rds", region_name=region)
    now = datetime.now(timezone.utc)
    flagged: List[Dict[str, Any]] = []

    marker: Optional[str] = None
    while True:
        params: Dict[str, Any] = {"SnapshotType": "manual"}
        if marker:
            params["Marker"] = marker
        response = rds.describe_db_cluster_snapshots(**params)
        for snap in response.get("DBClusterSnapshots", []):
            cluster_id = snap.get("DBClusterIdentifier")
            snapshot_id = snap.get("DBClusterSnapshotIdentifier")
            create_time = snap.get("SnapshotCreateTime")
            if not cluster_id or not snapshot_id or not create_time:
                continue
            if create_time.tzinfo is None:
                create_time = create_time.replace(tzinfo=timezone.utc)
            age_days = (now - create_time).days
            if age_days <= min_age_days:
                continue
            flagged.append(
                {
                    "resource_id": snapshot_id,
                    "resource_type": "aurora_cluster_snapshot",
                    "resource_name": snapshot_id,
                    "region": region,
                    "state": snap.get("Status", "unknown"),
                    "metadata": {
                        "cluster_id": cluster_id,
                        "snapshot_create_time_utc": create_time.isoformat(),
                        "age_days": age_days,
                        "min_age_days": min_age_days,
                        "recommended_action": "delete_old_manual_snapshot",
                        "check_reason": create_check_reason(
                            "old",
                            {"age_days": age_days, "min_age_days": min_age_days},
                        ),
                    },
                }
            )
        marker = response.get("Marker")
        if not marker:
            break
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="aurora_old_manual_snapshots",
        name="Aurora Old Manual Snapshots",
        description="Identifies Aurora manual snapshots older than threshold",
        resource_type="aurora_cluster_snapshot",
        check_function=check_aurora_old_manual_snapshots,
        default_action="delete_old_manual_snapshot",
        parameters={
            "min_age_days": 30,
            "region": None,
        },
    )
)

