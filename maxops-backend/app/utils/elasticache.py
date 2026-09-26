"""Shared ElastiCache inventory identity helpers."""

from __future__ import annotations

from typing import Any


def is_elasticache_member_cluster(resource: dict[str, Any]) -> bool:
    metadata = (
        resource.get("metadata") if isinstance(resource.get("metadata"), dict) else {}
    )
    return bool(
        metadata.get("ReplicationGroupId") or metadata.get("replication_group_id")
    )


def purge_legacy_elasticache_member_rows(db: Any) -> int:
    """Remove old member inventory rows and move their snoozes to the group."""
    from app.models.inventory import ElasticacheInventory, MaxOpsInventory, ResourceTag
    from app.models.settings import ResourceExemption

    rows = db.query(ElasticacheInventory).all()
    removed = 0
    migrated_exemptions: dict[tuple[str, str], Any] = {}
    for row in rows:
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        group_id = metadata.get("ReplicationGroupId") or metadata.get(
            "replication_group_id"
        )
        if not group_id:
            continue

        exemptions = (
            db.query(ResourceExemption)
            .filter(ResourceExemption.resource_id == row.resource_id)
            .all()
        )
        for source in exemptions:
            key = (source.check_id, str(group_id))
            destination = migrated_exemptions.get(key)
            if destination is None:
                destination = (
                    db.query(ResourceExemption)
                    .filter(
                        ResourceExemption.check_id == source.check_id,
                        ResourceExemption.resource_id == str(group_id),
                    )
                    .first()
                )
            if destination and destination.id != source.id:
                destination.exempted = bool(destination.exempted or source.exempted)
                dates = [
                    value
                    for value in (destination.snoozed_until, source.snoozed_until)
                    if value is not None
                ]
                destination.snoozed_until = max(dates) if dates else None
                destination.snooze_days = (
                    max(destination.snooze_days or 0, source.snooze_days or 0) or None
                )
                destination.snooze_reason = (
                    destination.snooze_reason or source.snooze_reason
                )
                db.delete(source)
            else:
                source.resource_id = str(group_id)
                source.resource_type = "elasticache"
                migrated_exemptions[key] = source

        db.query(MaxOpsInventory).filter(
            MaxOpsInventory.resource_type == "elasticache",
            MaxOpsInventory.inventory_id == row.inventory_id,
        ).delete(synchronize_session=False)
        db.query(ResourceTag).filter(
            ResourceTag.resource_type == "elasticache",
            ResourceTag.inventory_id == row.inventory_id,
        ).delete(synchronize_session=False)
        db.delete(row)
        removed += 1
    return removed
