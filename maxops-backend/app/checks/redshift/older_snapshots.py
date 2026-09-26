"""Redshift old snapshots check."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry


def _to_utc(dt: Any) -> Optional[datetime]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    if isinstance(dt, str):
        s = dt.strip()
        if not s:
            return None
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            parsed = datetime.fromisoformat(s)
            return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def check_redshift_older_snapshots(
    aws_adapter,
    min_age_days: int = 30,
    snapshot_type: str = "manual",
    include_statuses: Optional[List[str]] = None,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Identify Redshift cluster snapshots older than min_age_days.
    """
    if min_age_days < 0:
        return []

    redshift = aws_adapter.session.client("redshift", region_name=region)
    statuses = {s.lower() for s in (include_statuses or ["available"])}
    now = datetime.now(timezone.utc)

    flagged: List[Dict[str, Any]] = []
    marker: Optional[str] = None

    while True:
        params: Dict[str, Any] = {}
        if snapshot_type:
            params["SnapshotType"] = snapshot_type
        if marker:
            params["Marker"] = marker

        response = redshift.describe_cluster_snapshots(**params)
        snapshots = response.get("Snapshots", [])

        for snapshot in snapshots:
            try:
                snapshot_id = snapshot.get("SnapshotIdentifier")
                if not snapshot_id:
                    continue

                status = str(snapshot.get("Status") or "").lower()
                if statuses and status not in statuses:
                    continue

                create_time = _to_utc(snapshot.get("SnapshotCreateTime"))
                if create_time is None:
                    continue

                age_days = (now - create_time).days
                if age_days <= min_age_days:
                    continue

                cluster_id = snapshot.get("ClusterIdentifier")
                node_type = snapshot.get("NodeType")
                number_of_nodes = snapshot.get("NumberOfNodes")
                snapshot_type_value = snapshot.get("SnapshotType")

                flagged.append(
                    {
                        "resource_id": snapshot_id,
                        "resource_type": "redshift_snapshot",
                        "resource_name": snapshot_id,
                        "region": region,
                        "state": snapshot.get("Status", "unknown"),
                        "metadata": {
                            "snapshot_id": snapshot_id,
                            "cluster_id": cluster_id,
                            "snapshot_type": snapshot_type_value,
                            "node_type": node_type,
                            "number_of_nodes": number_of_nodes,
                            "snapshot_create_time_utc": create_time.isoformat(),
                            "age_days": age_days,
                            "min_age_days": min_age_days,
                            "recommended_action": "delete",
                            "check_reason": create_check_reason(
                                "old",
                                {
                                    "resource": "redshift_snapshot",
                                    "snapshot_id": snapshot_id,
                                    "age_days": age_days,
                                    "min_age_days": min_age_days,
                                },
                            ),
                        },
                    }
                )
            except Exception as exc:
                print(f"Error evaluating Redshift snapshot {snapshot.get('SnapshotIdentifier')}: {exc}")
                continue

        marker = response.get("Marker")
        if not marker:
            break

    return flagged


check_registry.register(
    CheckMetadata(
        check_id="redshift_older_snapshots",
        name="Redshift Older Snapshots",
        description="Identifies Redshift snapshots older than the configured age window",
        resource_type="redshift_snapshot",
        check_function=check_redshift_older_snapshots,
        default_action="delete",
        parameters={
            "min_age_days": 30,
            "snapshot_type": "manual",
            "include_statuses": ["available"],
            "region": None,
        },
    )
)
