"""Helpers for maintaining normalized resource tags."""
from __future__ import annotations

import json
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.models.inventory import (
    AsgInventory,
    DynamoDbInventory,
    EbsInventory,
    Ec2Inventory,
    ElasticacheInventory,
    RdsInventory,
    ResourceTag,
    S3Inventory,
    SageMakerInventory,
)

RESOURCE_MODELS = {
    "asg": AsgInventory,
    "ec2": Ec2Inventory,
    "rds": RdsInventory,
    "s3": S3Inventory,
    "dynamodb": DynamoDbInventory,
    "ebs": EbsInventory,
    "elasticache": ElasticacheInventory,
    "sagemaker": SageMakerInventory,
}


def _stringify_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return str(value)


def normalize_tags(raw_tags: Any) -> Dict[str, Dict[str, str]]:
    """Normalize mixed tag shapes into keyed rows by normalized key."""
    normalized: Dict[str, Dict[str, str]] = {}

    if isinstance(raw_tags, dict):
        items = raw_tags.items()
    elif isinstance(raw_tags, list):
        items = []
        for entry in raw_tags:
            if not isinstance(entry, dict):
                continue
            key = entry.get("Key")
            if key is None:
                key = entry.get("key", entry.get("tag_key"))
            value = entry.get("Value")
            if value is None:
                value = entry.get("value", entry.get("tag_value"))
            items.append((key, value))
    else:
        items = []

    for raw_key, raw_value in items:
        key = str(raw_key or "").strip()
        if not key:
            continue
        value = _stringify_value(raw_value).strip()
        key_norm = key.lower()
        normalized[key_norm] = {
            "tag_key": key,
            "tag_value": value,
            "tag_key_normalized": key_norm,
            "tag_value_normalized": value.lower(),
        }
    return normalized


def sync_resource_tags(
    db: Session,
    *,
    resource_type: str,
    inventory_id: int,
    resource_id: str,
    raw_tags: Any,
) -> None:
    """Upsert normalized tags for one resource inventory row."""
    if inventory_id is None:
        return

    normalized = normalize_tags(raw_tags)
    existing_rows = (
        db.query(ResourceTag)
        .filter(
            ResourceTag.resource_type == resource_type,
            ResourceTag.inventory_id == int(inventory_id),
        )
        .all()
    )
    existing_by_key = {row.tag_key_normalized: row for row in existing_rows}

    for key_norm, row in existing_by_key.items():
        if key_norm not in normalized:
            db.delete(row)

    for key_norm, payload in normalized.items():
        current = existing_by_key.get(key_norm)
        if current is None:
            db.add(
                ResourceTag(
                    resource_type=resource_type,
                    inventory_id=int(inventory_id),
                    resource_id=str(resource_id or ""),
                    tag_key=payload["tag_key"],
                    tag_value=payload["tag_value"],
                    tag_key_normalized=payload["tag_key_normalized"],
                    tag_value_normalized=payload["tag_value_normalized"],
                )
            )
            continue
        current.resource_id = str(resource_id or "")
        current.tag_key = payload["tag_key"]
        current.tag_value = payload["tag_value"]
        current.tag_value_normalized = payload["tag_value_normalized"]


def backfill_resource_tags(db: Session) -> int:
    """Populate resource_tags table from existing inventory tables."""
    inserted = 0
    for resource_type, model in RESOURCE_MODELS.items():
        rows = db.query(model.inventory_id, model.resource_id, model.tags_json).all()
        for inventory_id, resource_id, tags_json in rows:
            normalized = normalize_tags(tags_json)
            for payload in normalized.values():
                db.add(
                    ResourceTag(
                        resource_type=resource_type,
                        inventory_id=int(inventory_id),
                        resource_id=str(resource_id or ""),
                        tag_key=payload["tag_key"],
                        tag_value=payload["tag_value"],
                        tag_key_normalized=payload["tag_key_normalized"],
                        tag_value_normalized=payload["tag_value_normalized"],
                    )
                )
                inserted += 1
    return inserted
