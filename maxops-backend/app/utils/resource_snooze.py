"""Shared helpers for global and check-specific resource snoozes."""
from datetime import datetime, timezone
from typing import Dict, Iterable, Optional, Set

from sqlalchemy.orm import Session

from app.models.settings import ResourceExemption

GLOBAL_RESOURCE_SNOOZE_CHECK_ID = "__global__"


def as_aware_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def is_active_snooze(exemption: ResourceExemption, now: Optional[datetime] = None) -> bool:
    snoozed_until = as_aware_utc(exemption.snoozed_until)
    if snoozed_until is None:
        return False
    return snoozed_until > (now or datetime.now(timezone.utc))


def get_relevant_exemptions(db: Session, check_id: str) -> list[ResourceExemption]:
    return (
        db.query(ResourceExemption)
        .filter(ResourceExemption.check_id.in_([check_id, GLOBAL_RESOURCE_SNOOZE_CHECK_ID]))
        .all()
    )


def get_excluded_resource_ids(db: Session, check_id: str) -> Set[str]:
    now = datetime.now(timezone.utc)
    excluded_ids: Set[str] = set()
    for exemption in get_relevant_exemptions(db, check_id):
        if exemption.exempted:
            excluded_ids.add(exemption.resource_id)
        elif is_active_snooze(exemption, now):
            excluded_ids.add(exemption.resource_id)
    return excluded_ids


def build_exemption_filter_payload(db: Session, check_id: str) -> list[dict]:
    return [
        {
            "resource_id": exemption.resource_id,
            "exempted": exemption.exempted,
            "snoozed_until": exemption.snoozed_until.isoformat() if exemption.snoozed_until else None,
            "snooze_days": exemption.snooze_days,
            "snooze_reason": exemption.snooze_reason,
        }
        for exemption in get_relevant_exemptions(db, check_id)
    ]


def get_global_snooze_map(db: Session, resource_ids: Iterable[str]) -> Dict[str, ResourceExemption]:
    ids = [resource_id for resource_id in set(resource_ids) if resource_id]
    if not ids:
        return {}
    rows = (
        db.query(ResourceExemption)
        .filter(
            ResourceExemption.check_id == GLOBAL_RESOURCE_SNOOZE_CHECK_ID,
            ResourceExemption.resource_id.in_(ids),
        )
        .all()
    )
    return {row.resource_id: row for row in rows}


def serialize_snooze(exemption: Optional[ResourceExemption]) -> Optional[dict]:
    if exemption is None:
        return None
    snoozed_until = as_aware_utc(exemption.snoozed_until)
    return {
        "resource_id": exemption.resource_id,
        "resource_type": exemption.resource_type,
        "snoozed_until": snoozed_until.isoformat() if snoozed_until else None,
        "snooze_days": exemption.snooze_days,
        "reason": exemption.snooze_reason,
        "active": is_active_snooze(exemption),
        "updated_at": exemption.updated_at.isoformat() if exemption.updated_at else None,
    }
