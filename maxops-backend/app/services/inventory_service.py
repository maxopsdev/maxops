"""Services for importing and reading split inventory tables."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from app.models.inventory import (
    AsgInventory,
    DynamoDbInventory,
    EbsInventory,
    Ec2Inventory,
    ElasticacheInventory,
    MaxOpsInventory,
    RdsInventory,
    S3Inventory,
    SageMakerInventory,
)
from app.pricing.baseline import estimate_baseline_monthly_cost
from app.models.settings import OnboardingCheckResult, OnboardingExecution
from app.utils.resource_snooze import get_excluded_resource_ids
from app.utils.elasticache import (
    is_elasticache_member_cluster,
    purge_legacy_elasticache_member_rows,
)
from app.utils.resource_tags import sync_resource_tags


RESOURCE_MODELS = {
    "asg": AsgInventory,
    "ec2": Ec2Inventory,
    "rds": RdsInventory,
    "elasticache": ElasticacheInventory,
    "s3": S3Inventory,
    "ebs": EbsInventory,
    "dynamodb": DynamoDbInventory,
    "sagemaker": SageMakerInventory,
}


def _find_asg_aws_payloads_by_inventory_id(
    aws_payload: Dict[str, Any], flattened_rows: Iterable[Dict[str, Any]]
) -> Dict[int, Dict[str, Any]]:
    groups = aws_payload.get("AutoScalingGroups") or []
    by_name = {
        str(group.get("AutoScalingGroupName") or ""): group
        for group in groups
        if group.get("AutoScalingGroupName")
    }
    payloads: Dict[int, Dict[str, Any]] = {}
    for row in flattened_rows:
        inventory_id = row.get("inventory_id")
        resource_id = row.get("resource_id") or row.get("resource_name")
        if inventory_id is not None:
            payloads[int(inventory_id)] = by_name.get(str(resource_id), row)
    return payloads


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _find_aws_payloads_by_inventory_id(
    aws_payload: Dict[str, Any]
) -> Dict[int, Dict[str, Any]]:
    payloads: Dict[int, Dict[str, Any]] = {}
    for reservation in aws_payload.get("Reservations", []):
        for instance in reservation.get("Instances", []):
            inventory_id = instance.get("InventoryId")
            if inventory_id is None:
                continue
            payloads[int(inventory_id)] = instance
    return payloads


def _find_s3_aws_payloads_by_inventory_id(
    aws_payload: Dict[str, Any], flattened_rows: Iterable[Dict[str, Any]]
) -> Dict[int, Dict[str, Any]]:
    bucket_details = aws_payload.get("BucketDetails", {})
    payloads: Dict[int, Dict[str, Any]] = {}
    for row in flattened_rows:
        inventory_id = row.get("inventory_id")
        bucket_name = row.get("resource_name") or row.get("resource_id")
        if inventory_id is None or not bucket_name:
            continue
        payloads[int(inventory_id)] = {
            "bucket": {
                "Name": bucket_name,
                "CreationDate": row.get("creation_date")
                or (row.get("metadata") or {}).get("creation_date"),
            },
            "details": bucket_details.get(bucket_name, {}),
        }
    return payloads


def _find_rds_aws_payloads_by_inventory_id(
    aws_payload: Dict[str, Any]
) -> Dict[int, Dict[str, Any]]:
    db_instances = aws_payload.get("DBInstances", [])
    tag_details = aws_payload.get("TagDetails", {})
    metric_details = aws_payload.get("MetricDetails", {})
    payloads: Dict[int, Dict[str, Any]] = {}
    for instance in db_instances:
        inventory_id = instance.get("InventoryId")
        if inventory_id is None:
            continue
        resource_id = instance.get("DBInstanceIdentifier")
        payloads[int(inventory_id)] = {
            "db_instance": instance,
            "tags": tag_details.get(resource_id, {}),
            "metrics": metric_details.get(resource_id, {}),
        }
    return payloads


def _find_elasticache_aws_payloads_by_inventory_id(
    aws_payload: Dict[str, Any]
) -> Dict[int, Dict[str, Any]]:
    payloads: Dict[int, Dict[str, Any]] = {}
    metric_details = aws_payload.get("MetricDetails", {})
    for cluster in aws_payload.get("CacheClusters", []):
        inventory_id = cluster.get("InventoryId")
        if inventory_id is None:
            continue
        resource_id = cluster.get("CacheClusterId")
        payloads[int(inventory_id)] = {
            "resource": cluster,
            "metrics": metric_details.get(resource_id, {}),
        }
    for replication_group in aws_payload.get("ReplicationGroups", []):
        inventory_id = replication_group.get("InventoryId")
        if inventory_id is None:
            continue
        resource_id = replication_group.get("ReplicationGroupId")
        payloads[int(inventory_id)] = {
            "resource": replication_group,
            "metrics": metric_details.get(resource_id, {}),
        }
    return payloads


def _find_ebs_aws_payloads_by_inventory_id(
    aws_payload: Dict[str, Any]
) -> Dict[int, Dict[str, Any]]:
    payloads: Dict[int, Dict[str, Any]] = {}
    metric_details = aws_payload.get("MetricDetails", {})
    for volume in aws_payload.get("Volumes", []):
        inventory_id = volume.get("InventoryId")
        if inventory_id is None:
            continue
        resource_id = volume.get("VolumeId")
        payloads[int(inventory_id)] = {
            "volume": volume,
            "metrics": metric_details.get(resource_id, {}),
        }
    return payloads


def _find_dynamodb_aws_payloads_by_inventory_id(
    aws_payload: Dict[str, Any], flattened_rows: Iterable[Dict[str, Any]]
) -> Dict[int, Dict[str, Any]]:
    payloads: Dict[int, Dict[str, Any]] = {}
    table_details = aws_payload.get("TableDetails", {})
    tag_details = aws_payload.get("TagDetails", {})
    metric_details = aws_payload.get("MetricDetails", {})
    for row in flattened_rows:
        inventory_id = row.get("inventory_id")
        if inventory_id is None:
            continue
        metadata = row.get("metadata") or {}
        table_name = (
            row.get("table_name")
            or metadata.get("TableName")
            or row.get("resource_name")
        )
        resource_id = row.get("resource_id")
        payloads[int(inventory_id)] = {
            "table": table_details.get(table_name),
            "tags": tag_details.get(resource_id),
            "metrics": metric_details.get(resource_id, {}),
        }
    return payloads


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_optional_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_graviton_family(value: Optional[str]) -> bool:
    text = str(value or "").lower()
    return (
        ".t4g." in text
        or ".m6g." in text
        or ".r6g." in text
        or ".c6g." in text
        or ".x2gd." in text
    )


def _build_simple_metric_trend(
    metric_history: Dict[str, Any], metric_key: str
) -> Dict[str, Any]:
    series = metric_history.get(metric_key) or {}
    timestamps = series.get("timestamps") or []
    averages = series.get("average") or []
    points = []
    for index, timestamp in enumerate(timestamps):
        points.append(
            {
                "timestamp": timestamp,
                "average": _coerce_float(
                    averages[index] if index < len(averages) else None
                ),
            }
        )
    return {
        "points": points,
        "latest": points[0] if points else None,
    }


def _build_metric_trend(
    metric_history: Dict[str, Any], metric_key: str
) -> Dict[str, Any]:
    series = metric_history.get(metric_key) or {}
    timestamps = series.get("timestamps") or []
    average = series.get("average") or []
    maximum = series.get("maximum") or []
    p90 = series.get("p90") or []
    p95 = series.get("p95") or []
    p99 = series.get("p99") or []
    points = []
    for index, timestamp in enumerate(timestamps):
        points.append(
            {
                "timestamp": timestamp,
                "average": _coerce_float(
                    average[index] if index < len(average) else None
                ),
                "maximum": _coerce_float(
                    maximum[index] if index < len(maximum) else None
                ),
                "p90": _coerce_float(p90[index] if index < len(p90) else None),
                "p95": _coerce_float(p95[index] if index < len(p95) else None),
                "p99": _coerce_float(p99[index] if index < len(p99) else None),
            }
        )
    return {
        "points": points,
        "latest": points[0] if points else None,
    }


def get_resource_model(resource_type: str):
    if resource_type == "ecs":
        return MaxOpsInventory
    model = RESOURCE_MODELS.get(resource_type)
    if not model:
        raise ValueError(f"Unsupported resource type '{resource_type}'")
    return model


@dataclass
class ImportedCheckSummary:
    check_id: str
    name: str
    description: Optional[str]
    resource_type: str
    resources_found: int
    potential_savings_yearly: float
    execution_time: Optional[datetime]
    status: str


class SyntheticInventoryImporter:
    """Import generated synthetic inventory into split and legacy tables."""

    def __init__(self, db: Session):
        self.db = db

    def import_directory(
        self, resource_type: str, input_dir: str | Path
    ) -> Dict[str, Any]:
        input_path = Path(input_dir)
        if not input_path.exists():
            raise FileNotFoundError(f"Input directory not found: {input_path}")

        inventory_path = input_path / "inventory.json"
        maxops_path = input_path / f"{resource_type}_maxops.json"
        if not maxops_path.exists():
            matches = sorted(input_path.glob("*_maxops.json"))
            if len(matches) == 1:
                maxops_path = matches[0]

        if not inventory_path.exists():
            raise FileNotFoundError(f"inventory.json not found under {input_path}")
        if not maxops_path.exists():
            raise FileNotFoundError(
                f"{resource_type}_maxops.json not found under {input_path}"
            )

        with inventory_path.open("r", encoding="utf-8") as handle:
            inventory_doc = json.load(handle)
        with maxops_path.open("r", encoding="utf-8") as handle:
            maxops_rows = json.load(handle)

        generated_at = _parse_datetime(inventory_doc.get("generated_at"))
        account_id = inventory_doc.get("account_id")
        flattened_rows = inventory_doc.get("instances", [])
        aws_payloads = _find_aws_payloads_by_inventory_id(
            inventory_doc.get("aws_payload", {})
        )
        if resource_type == "s3":
            flattened_rows = inventory_doc.get("buckets", [])
            aws_payloads = _find_s3_aws_payloads_by_inventory_id(
                inventory_doc.get("aws_payload", {}),
                flattened_rows,
            )
        elif resource_type == "rds":
            flattened_rows = inventory_doc.get("instances", [])
            aws_payloads = _find_rds_aws_payloads_by_inventory_id(
                inventory_doc.get("aws_payload", {})
            )
        elif resource_type == "elasticache":
            flattened_rows = inventory_doc.get("resources", [])
            aws_payloads = _find_elasticache_aws_payloads_by_inventory_id(
                inventory_doc.get("aws_payload", {})
            )
        elif resource_type == "ebs":
            flattened_rows = inventory_doc.get("volumes", [])
            aws_payloads = _find_ebs_aws_payloads_by_inventory_id(
                inventory_doc.get("aws_payload", {})
            )
        elif resource_type == "dynamodb":
            flattened_rows = inventory_doc.get("resources", [])
            aws_payloads = _find_dynamodb_aws_payloads_by_inventory_id(
                inventory_doc.get("aws_payload", {}),
                flattened_rows,
            )
        elif resource_type == "asg":
            flattened_rows = inventory_doc.get("resources", [])
            aws_payloads = _find_asg_aws_payloads_by_inventory_id(
                inventory_doc.get("aws_payload", {}), flattened_rows
            )
        elif resource_type == "ecs":
            flattened_rows = inventory_doc.get("resources", [])
            aws_payloads = {
                int(row["inventory_id"]): row.get("aws_payload")
                for row in flattened_rows
                if row.get("inventory_id") is not None
            }
        maxops_by_inventory_id = {
            int(row["inventory_id"]): row
            for row in maxops_rows
            if row.get("inventory_id") is not None
        }

        resource_model = get_resource_model(resource_type)

        imported = 0
        skipped_member_rows = 0
        for row in flattened_rows:
            if resource_type == "elasticache" and is_elasticache_member_cluster(row):
                skipped_member_rows += 1
                continue
            effective_resource_type = (
                row.get("resource_type") or resource_type
                if resource_type == "ecs"
                else resource_type
            )
            inventory_id = int(row["inventory_id"])
            maxops_row = maxops_by_inventory_id.get(inventory_id)
            if not maxops_row:
                raise ValueError(f"Missing MaxOps row for inventory_id={inventory_id}")

            aws_payload = aws_payloads.get(inventory_id)
            if resource_type == "ec2":
                self._upsert_ec2_inventory(
                    model=resource_model,
                    row=row,
                    account_id=account_id,
                    generated_at=generated_at,
                    aws_payload=aws_payload,
                    source_path=str(inventory_path),
                )
            elif resource_type == "s3":
                self._upsert_s3_inventory(
                    model=resource_model,
                    row=row,
                    account_id=account_id,
                    generated_at=generated_at,
                    aws_payload=aws_payload,
                    source_path=str(inventory_path),
                )
            elif resource_type == "rds":
                self._upsert_rds_inventory(
                    model=resource_model,
                    row=row,
                    account_id=account_id,
                    generated_at=generated_at,
                    aws_payload=aws_payload,
                    source_path=str(inventory_path),
                )
            elif resource_type == "elasticache":
                self._upsert_elasticache_inventory(
                    model=resource_model,
                    row=row,
                    account_id=account_id,
                    generated_at=generated_at,
                    aws_payload=aws_payload,
                    source_path=str(inventory_path),
                )
            elif resource_type == "ebs":
                self._upsert_ebs_inventory(
                    model=resource_model,
                    row=row,
                    account_id=account_id,
                    generated_at=generated_at,
                    aws_payload=aws_payload,
                    source_path=str(inventory_path),
                )
            elif resource_type == "dynamodb":
                self._upsert_dynamodb_inventory(
                    model=resource_model,
                    row=row,
                    account_id=account_id,
                    generated_at=generated_at,
                    aws_payload=aws_payload,
                    source_path=str(inventory_path),
                )
            elif resource_type == "asg":
                self._upsert_asg_inventory(
                    model=resource_model,
                    row=row,
                    account_id=account_id,
                    generated_at=generated_at,
                    aws_payload=aws_payload,
                    source_path=str(inventory_path),
                )

            self._sync_resource_tags(
                resource_type=effective_resource_type,
                inventory_id=inventory_id,
                resource_id=row.get("resource_id"),
                tags=row.get("tags"),
            )

            self._upsert_maxops_inventory(
                resource_type=effective_resource_type,
                row=row,
                maxops_row=maxops_row,
                generated_at=generated_at,
                source_path=str(maxops_path),
            )
            imported += 1

        if resource_type == "elasticache":
            purge_legacy_elasticache_member_rows(self.db)
        self.db.commit()
        return {
            "resource_type": resource_type,
            "input_dir": str(input_path),
            "inventory_rows": len(flattened_rows),
            "maxops_rows": len(maxops_rows),
            "imported_rows": imported,
            "skipped_member_rows": skipped_member_rows,
            "generated_at": generated_at.isoformat() if generated_at else None,
        }

    def _sync_resource_tags(
        self,
        *,
        resource_type: str,
        inventory_id: int,
        resource_id: Any,
        tags: Any,
    ) -> None:
        sync_resource_tags(
            self.db,
            resource_type=resource_type,
            inventory_id=int(inventory_id),
            resource_id=str(resource_id or ""),
            raw_tags=tags,
        )

    def _upsert_ec2_inventory(
        self,
        *,
        model,
        row: Dict[str, Any],
        account_id: Optional[str],
        generated_at: Optional[datetime],
        aws_payload: Optional[Dict[str, Any]],
        source_path: str,
    ) -> None:
        existing = (
            self.db.query(model)
            .filter(model.inventory_id == int(row["inventory_id"]))
            .first()
        )
        if not existing:
            existing = model(inventory_id=int(row["inventory_id"]))
            self.db.add(existing)

        existing.resource_id = row.get("resource_id")
        existing.resource_name = row.get("resource_name")
        existing.resource_type = row.get("resource_type", "ec2")
        existing.account_id = row.get("account_id") or account_id
        existing.region = row.get("region")
        existing.availability_zone = row.get("availability_zone")
        existing.state = row.get("state")
        existing.instance_type = row.get("instance_type")
        existing.usage_profile = row.get("metadata", {}).get("usage_profile")
        existing.launch_time = _parse_datetime(row.get("launch_time"))
        existing.generated_at = generated_at
        existing.avg_cpu_utilization = row.get("metadata", {}).get(
            "avg_cpu_utilization"
        )
        existing.avg_memory_utilization = row.get("metadata", {}).get(
            "avg_memory_utilization"
        )
        existing.avg_network_in = row.get("metadata", {}).get("avg_network_in")
        existing.avg_network_out = row.get("metadata", {}).get("avg_network_out")
        existing.monthly_cost_estimate = row.get("metadata", {}).get(
            "monthly_cost_estimate"
        )
        existing.metric_history_json = row.get("metadata", {}).get("metric_history")
        existing.tags_json = row.get("tags")
        existing.metadata_json = row.get("metadata")
        existing.aws_payload_json = aws_payload
        existing.source_path = source_path

    def _upsert_asg_inventory(
        self,
        *,
        model,
        row: Dict[str, Any],
        account_id: Optional[str],
        generated_at: Optional[datetime],
        aws_payload: Optional[Dict[str, Any]],
        source_path: str,
    ) -> None:
        resolved_account_id = row.get("account_id") or account_id
        resolved_region = row.get("region")
        if not str(resolved_account_id or "").strip() or not str(
            resolved_region or ""
        ).strip():
            raise ValueError(
                "Synthetic ASG inventory requires non-empty account_id and region"
            )

        existing = (
            self.db.query(model)
            .filter(model.inventory_id == int(row["inventory_id"]))
            .first()
        )
        if not existing:
            existing = model(inventory_id=int(row["inventory_id"]))
            self.db.add(existing)

        metadata = row.get("metadata") or {}
        existing.resource_id = row.get("resource_id") or row.get("resource_name")
        existing.resource_name = row.get("resource_name") or existing.resource_id
        existing.resource_type = "asg"
        existing.account_id = resolved_account_id
        existing.region = resolved_region
        existing.state = row.get("state") or "active"
        existing.min_size = int(row.get("min_size") or metadata.get("min_size") or 0)
        existing.desired_capacity = int(
            row.get("desired_capacity") or metadata.get("desired_capacity") or 0
        )
        existing.max_size = int(row.get("max_size") or metadata.get("max_size") or 0)
        existing.instance_type = row.get("instance_type") or metadata.get(
            "instance_type"
        )
        existing.platform_normalized = row.get(
            "platform_normalized"
        ) or metadata.get("platform_normalized")
        existing.generated_at = generated_at
        existing.tags_json = row.get("tags")
        existing.metadata_json = metadata
        existing.aws_payload_json = aws_payload
        existing.source_path = source_path

    def _upsert_s3_inventory(
        self,
        *,
        model,
        row: Dict[str, Any],
        account_id: Optional[str],
        generated_at: Optional[datetime],
        aws_payload: Optional[Dict[str, Any]],
        source_path: str,
    ) -> None:
        existing = (
            self.db.query(model)
            .filter(model.inventory_id == int(row["inventory_id"]))
            .first()
        )
        if not existing:
            existing = model(inventory_id=int(row["inventory_id"]))
            self.db.add(existing)

        metadata = row.get("metadata", {})
        existing.resource_id = row.get("resource_id")
        existing.resource_name = row.get("resource_name")
        existing.resource_type = row.get("resource_type", "s3")
        existing.account_id = row.get("account_id") or account_id
        existing.region = row.get("region")
        existing.state = row.get("state")
        existing.creation_date = _parse_datetime(
            row.get("creation_date") or metadata.get("creation_date")
        )
        existing.generated_at = generated_at
        existing.versioning_status = metadata.get("versioning_status")
        existing.logging_enabled = str(bool(metadata.get("logging_enabled"))).lower()
        existing.inventory_configuration_count = metadata.get(
            "inventory_configuration_count"
        )
        existing.replication_rule_count = metadata.get("replication_rule_count")
        existing.bucket_size_gb = metadata.get("bucket_size_gb")
        existing.object_count = metadata.get("object_count")
        existing.estimated_monthly_cost = metadata.get("estimated_monthly_cost")
        existing.tags_json = row.get("tags")
        existing.metadata_json = metadata
        existing.aws_payload_json = aws_payload
        existing.source_path = source_path

    def _upsert_rds_inventory(
        self,
        *,
        model,
        row: Dict[str, Any],
        account_id: Optional[str],
        generated_at: Optional[datetime],
        aws_payload: Optional[Dict[str, Any]],
        source_path: str,
    ) -> None:
        existing = (
            self.db.query(model)
            .filter(model.inventory_id == int(row["inventory_id"]))
            .first()
        )
        if not existing:
            existing = model(inventory_id=int(row["inventory_id"]))
            self.db.add(existing)

        metadata = row.get("metadata", {})
        existing.resource_id = row.get("resource_id")
        existing.resource_name = row.get("resource_name")
        existing.resource_type = row.get("resource_type", "rds")
        existing.account_id = row.get("account_id") or account_id
        existing.region = row.get("region")
        existing.availability_zone = row.get("availability_zone")
        existing.state = row.get("state")
        existing.engine = row.get("engine") or metadata.get("engine")
        existing.db_instance_class = row.get("db_instance_class") or metadata.get(
            "instance_class"
        )
        existing.created_at_source = _parse_datetime(
            row.get("created_at") or metadata.get("created_at")
        )
        existing.generated_at = generated_at
        existing.avg_cpu_utilization = metadata.get("avg_cpu_utilization")
        existing.avg_connections = metadata.get("avg_connections")
        existing.avg_read_iops = metadata.get("avg_read_iops")
        existing.avg_write_iops = metadata.get("avg_write_iops")
        existing.monthly_cost_estimate = metadata.get("monthly_cost_estimate")
        existing.metric_history_json = metadata.get("metric_history")
        existing.tags_json = row.get("tags")
        existing.metadata_json = metadata
        existing.aws_payload_json = aws_payload
        existing.source_path = source_path

    def _upsert_elasticache_inventory(
        self,
        *,
        model,
        row: Dict[str, Any],
        account_id: Optional[str],
        generated_at: Optional[datetime],
        aws_payload: Optional[Dict[str, Any]],
        source_path: str,
    ) -> None:
        existing = (
            self.db.query(model)
            .filter(model.inventory_id == int(row["inventory_id"]))
            .first()
        )
        if not existing:
            existing = model(inventory_id=int(row["inventory_id"]))
            self.db.add(existing)

        metadata = row.get("metadata", {})
        existing.resource_id = row.get("resource_id")
        existing.resource_name = row.get("resource_name")
        existing.resource_type = row.get("resource_type", "elasticache")
        existing.account_id = row.get("account_id") or account_id
        existing.region = row.get("region")
        existing.availability_zone = row.get("availability_zone")
        existing.state = row.get("state")
        existing.engine = metadata.get("engine")
        existing.engine_version = metadata.get("engine_version")
        existing.cache_node_type = metadata.get("cache_node_type")
        existing.generated_at = generated_at
        existing.monthly_cost_estimate = metadata.get("monthly_cost_estimate")
        existing.avg_curritems = metadata.get("avg_curritems")
        existing.avg_keycount = metadata.get("avg_keycount")
        existing.metric_history_json = metadata.get("metric_history")
        existing.tags_json = row.get("tags")
        existing.metadata_json = metadata
        existing.aws_payload_json = aws_payload
        existing.source_path = source_path

    def _upsert_ebs_inventory(
        self,
        *,
        model,
        row: Dict[str, Any],
        account_id: Optional[str],
        generated_at: Optional[datetime],
        aws_payload: Optional[Dict[str, Any]],
        source_path: str,
    ) -> None:
        existing = (
            self.db.query(model)
            .filter(model.inventory_id == int(row["inventory_id"]))
            .first()
        )
        if not existing:
            existing = model(inventory_id=int(row["inventory_id"]))
            self.db.add(existing)

        metadata = row.get("metadata", {})
        existing.resource_id = row.get("resource_id")
        existing.resource_name = row.get("resource_name")
        existing.resource_type = row.get("resource_type", "ebs")
        existing.account_id = row.get("account_id") or account_id
        existing.region = row.get("region")
        existing.availability_zone = row.get("availability_zone")
        existing.state = row.get("state")
        existing.volume_type = metadata.get("volume_type")
        existing.attached = str(bool(row.get("attached"))).lower()
        existing.size_gb = metadata.get("size")
        existing.iops = metadata.get("iops")
        existing.throughput = metadata.get("throughput")
        existing.generated_at = generated_at
        existing.avg_iops = metadata.get("avg_iops")
        existing.avg_throughput_mb = metadata.get("avg_throughput_mb")
        existing.monthly_cost_estimate = metadata.get("monthly_cost_estimate")
        existing.metric_history_json = metadata.get("metric_history")
        existing.tags_json = row.get("tags")
        existing.metadata_json = metadata
        existing.aws_payload_json = aws_payload
        existing.source_path = source_path

    def _upsert_dynamodb_inventory(
        self,
        *,
        model,
        row: Dict[str, Any],
        account_id: Optional[str],
        generated_at: Optional[datetime],
        aws_payload: Optional[Dict[str, Any]],
        source_path: str,
    ) -> None:
        existing = (
            self.db.query(model)
            .filter(model.inventory_id == int(row["inventory_id"]))
            .first()
        )
        if not existing:
            existing = model(inventory_id=int(row["inventory_id"]))
            self.db.add(existing)

        metadata = row.get("metadata", {})
        existing.resource_id = row.get("resource_id")
        existing.resource_name = row.get("resource_name")
        existing.resource_type = row.get("resource_type", "dynamodb")
        existing.account_id = row.get("account_id") or account_id
        existing.region = row.get("region")
        existing.state = row.get("state")
        existing.billing_mode = metadata.get("BillingMode") or metadata.get(
            "billing_mode"
        )
        existing.table_name = metadata.get("TableName")
        existing.table_class = metadata.get("table_class")
        existing.generated_at = generated_at
        existing.item_count = metadata.get("item_count")
        existing.read_capacity_units = metadata.get("read_capacity_units")
        existing.write_capacity_units = metadata.get("write_capacity_units")
        existing.monthly_cost_estimate = metadata.get("monthly_cost_estimate")
        existing.metric_history_json = metadata.get("metric_history")
        existing.tags_json = row.get("tags")
        existing.metadata_json = metadata
        existing.aws_payload_json = aws_payload
        existing.source_path = source_path

    def _upsert_maxops_inventory(
        self,
        *,
        resource_type: str,
        row: Dict[str, Any],
        maxops_row: Dict[str, Any],
        generated_at: Optional[datetime],
        source_path: str,
    ) -> None:
        existing = (
            self.db.query(MaxOpsInventory)
            .filter(
                MaxOpsInventory.resource_type == resource_type,
                MaxOpsInventory.inventory_id == int(row["inventory_id"]),
            )
            .first()
        )
        if not existing:
            existing = MaxOpsInventory(
                resource_type=resource_type,
                inventory_id=int(row["inventory_id"]),
            )
            self.db.add(existing)

        existing.resource_id = row.get("resource_id")
        existing.resource_name = row.get("resource_name")
        existing.account_id = row.get("account_id")
        existing.region = row.get("region")
        existing.generated_at = generated_at
        existing.check_id = maxops_row.get("check_id")
        existing.finding_type = maxops_row.get("finding_type")
        existing.title = maxops_row.get("title")
        existing.description = maxops_row.get("description")
        existing.severity = maxops_row.get("severity")
        existing.confidence_score = maxops_row.get("confidence_score")
        existing.risk_score = maxops_row.get("risk_score")
        existing.recommended_action = maxops_row.get("recommended_action")
        existing.recommended_actions_json = maxops_row.get("recommended_actions")
        existing.available_actions_json = maxops_row.get("available_actions")
        existing.potential_savings_monthly = maxops_row.get("potential_savings_monthly")
        existing.potential_savings_yearly = maxops_row.get("potential_savings_yearly")
        existing.evidence_json = maxops_row.get("evidence")
        existing.current_config_json = maxops_row.get("current_config")
        existing.target_config_json = maxops_row.get("target_config")
        existing.metadata_json = maxops_row.get("metadata")
        existing.source_path = source_path


def get_imported_check_summaries(db: Session) -> Dict[str, ImportedCheckSummary]:
    rows = db.query(MaxOpsInventory).filter(MaxOpsInventory.check_id.isnot(None)).all()
    summaries: Dict[str, ImportedCheckSummary] = {}
    excluded_by_check: Dict[str, set[str]] = {}
    for row in rows:
        if not row.check_id:
            continue
        if row.check_id not in excluded_by_check:
            excluded_by_check[row.check_id] = get_excluded_resource_ids(
                db, row.check_id
            )
        excluded_ids = excluded_by_check[row.check_id]
        if row.resource_id in excluded_ids:
            continue
        summary = summaries.get(row.check_id)
        row_savings = float(row.potential_savings_yearly or 0.0)
        row_time = row.generated_at
        if not summary:
            summaries[row.check_id] = ImportedCheckSummary(
                check_id=row.check_id,
                name=row.title or row.check_id,
                description=row.description,
                resource_type=row.resource_type,
                resources_found=1,
                potential_savings_yearly=row_savings,
                execution_time=row_time,
                status="completed",
            )
            continue
        summary.resources_found += 1
        summary.potential_savings_yearly += row_savings
        if row_time and (
            summary.execution_time is None or row_time > summary.execution_time
        ):
            summary.execution_time = row_time
            summary.name = row.title or summary.name
            summary.description = row.description or summary.description
    return summaries


def get_imported_check_resources(db: Session, check_id: str) -> Dict[str, Any]:
    rows = (
        db.query(MaxOpsInventory)
        .filter(MaxOpsInventory.check_id == check_id)
        .order_by(MaxOpsInventory.generated_at.desc(), MaxOpsInventory.id.asc())
        .all()
    )
    excluded_ids = get_excluded_resource_ids(db, check_id)
    rows = [row for row in rows if row.resource_id not in excluded_ids]
    if not rows:
        return {
            "check_id": check_id,
            "resources": [],
            "resources_found": 0,
            "execution_time": None,
        }

    grouped_ids: Dict[str, List[int]] = {}
    for row in rows:
        grouped_ids.setdefault(row.resource_type, []).append(row.inventory_id)

    resource_rows: Dict[tuple[str, int], Any] = {}
    for resource_type, inventory_ids in grouped_ids.items():
        model = get_resource_model(resource_type)
        results = db.query(model).filter(model.inventory_id.in_(inventory_ids)).all()
        for result in results:
            resource_rows[(resource_type, result.inventory_id)] = result

    resources = []
    latest_time: Optional[datetime] = None
    for row in rows:
        resource_row = resource_rows.get((row.resource_type, row.inventory_id))
        tags = getattr(resource_row, "tags_json", None) or {}
        inventory_metadata = getattr(resource_row, "metadata_json", None) or {}
        resource_metadata = {
            **inventory_metadata,
            "inventory_id": row.inventory_id,
            "check_id": row.check_id,
            "finding_type": row.finding_type,
            "title": row.title,
            "description": row.description,
            "severity": row.severity,
            "confidence_score": row.confidence_score,
            "risk_score": row.risk_score,
            "recommended_action": row.recommended_action,
            "recommended_actions": row.recommended_actions_json or [],
            "available_actions": row.available_actions_json or [],
            "potential_savings_monthly": row.potential_savings_monthly,
            "potential_savings_yearly": row.potential_savings_yearly,
            "evidence": row.evidence_json or {},
            "maxops_metadata": row.metadata_json or {},
        }
        aws_payload = getattr(resource_row, "aws_payload_json", None)
        if aws_payload is not None:
            resource_metadata["aws_payload"] = aws_payload

        resources.append(
            {
                "resource_id": row.resource_id,
                "resource_type": row.resource_type,
                "resource_name": row.resource_name,
                "region": row.region,
                "account_id": row.account_id,
                "tags": tags,
                "metadata": resource_metadata,
            }
        )
        if row.generated_at and (latest_time is None or row.generated_at > latest_time):
            latest_time = row.generated_at

    return {
        "check_id": check_id,
        "resources": resources,
        "resources_found": len(resources),
        "execution_time": latest_time.isoformat() if latest_time else None,
    }


def summarize_imported_check_history(
    db: Session, check_id: str
) -> List[Dict[str, Any]]:
    rows = db.query(MaxOpsInventory).filter(MaxOpsInventory.check_id == check_id).all()
    excluded_ids = get_excluded_resource_ids(db, check_id)
    rows = [row for row in rows if row.resource_id not in excluded_ids]
    if not rows:
        return []

    grouped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        timestamp = row.generated_at.isoformat() if row.generated_at else "unknown"
        summary = grouped.setdefault(
            timestamp,
            {
                "timestamp": timestamp,
                "savings": 0.0,
                "resources_found": 0,
            },
        )
        summary["savings"] += float(row.potential_savings_yearly or 0.0)
        summary["resources_found"] += 1

    ordered = sorted(grouped.values(), key=lambda item: item["timestamp"])
    for item in ordered:
        item["savings"] = round(item["savings"], 2)
    return ordered


def get_ec2_inventory_overview(db: Session) -> Dict[str, Any]:
    rows = (
        db.query(Ec2Inventory, MaxOpsInventory)
        .outerjoin(
            MaxOpsInventory,
            (MaxOpsInventory.resource_type == "ec2")
            & (MaxOpsInventory.inventory_id == Ec2Inventory.inventory_id),
        )
        .order_by(Ec2Inventory.inventory_id.asc())
        .all()
    )

    if not rows:
        return {
            "generated_at": None,
            "account_id": None,
            "summary": {
                "total_instances": 0,
                "running_instances": 0,
                "stopped_instances": 0,
                "actionable_instances": 0,
                "healthy_instances": 0,
                "average_cpu_utilization": 0.0,
                "average_memory_utilization": 0.0,
                "average_network_in": 0.0,
                "average_network_out": 0.0,
                "monthly_cost_estimate": 0.0,
                "potential_savings_yearly": 0.0,
            },
            "dimensions": {
                "regions": [],
                "instance_types": [],
                "environments": [],
            },
            "findings_breakdown": [],
            "instances": [],
        }

    generated_at = max(
        (item.generated_at for item, _ in rows if item.generated_at), default=None
    )
    account_id = next((item.account_id for item, _ in rows if item.account_id), None)

    regions = Counter()
    instance_types = Counter()
    environments = Counter()
    findings = Counter()

    total_cpu = 0.0
    total_memory = 0.0
    total_network_in = 0.0
    total_network_out = 0.0
    total_monthly_cost = 0.0
    total_yearly_savings = 0.0
    running_instances = 0
    stopped_instances = 0
    actionable_instances = 0

    instances: List[Dict[str, Any]] = []

    for item, finding in rows:
        tags = item.tags_json or {}
        metadata = item.metadata_json or {}
        env = str(tags.get("env") or tags.get("environment") or "unknown").lower()
        metric_history = (
            item.metric_history_json or metadata.get("metric_history") or {}
        )
        cpu = _coerce_float(
            item.avg_cpu_utilization, _coerce_float(metadata.get("avg_cpu_utilization"))
        )
        network_in = _coerce_float(
            item.avg_network_in, _coerce_float(metadata.get("avg_network_in"))
        )
        network_out = _coerce_float(
            item.avg_network_out, _coerce_float(metadata.get("avg_network_out"))
        )
        resource_for_pricing = {
            "resource_type": "ec2",
            "resource_id": item.resource_id,
            "region": item.region,
            "instance_type": item.instance_type,
            "metadata": metadata,
        }
        monthly_cost = _coerce_float(
            item.monthly_cost_estimate,
            estimate_baseline_monthly_cost("ec2", resource_for_pricing),
        )
        memory_utilization = _coerce_float(
            item.avg_memory_utilization,
            _estimate_memory_utilization(item, metadata),
        )
        disk_used_gb, disk_available_gb = _estimate_disk_footprint(item, metadata)
        received_bytes = round(network_in * 1024 * 1024, 2)
        sent_bytes = round(network_out * 1024 * 1024, 2)
        savings_yearly = float(
            (finding.potential_savings_yearly if finding else 0.0) or 0.0
        )
        savings_monthly = float(
            (finding.potential_savings_monthly if finding else 0.0) or 0.0
        )
        status = str(
            (finding.metadata_json or {}).get("status")
            if finding and finding.metadata_json
            else ""
        )
        finding_type = (
            finding.finding_type if finding and finding.finding_type else "healthy"
        )

        regions.update([item.region or "unknown"])
        instance_types.update([item.instance_type or "unknown"])
        environments.update([env])
        findings.update([finding_type])

        total_cpu += cpu
        total_memory += memory_utilization
        total_network_in += network_in
        total_network_out += network_out
        total_monthly_cost += monthly_cost
        total_yearly_savings += savings_yearly

        if item.state == "running":
            running_instances += 1
        elif item.state == "stopped":
            stopped_instances += 1

        if status == "actionable" or finding_type != "healthy":
            actionable_instances += 1

        instances.append(
            {
                "inventory_id": item.inventory_id,
                "resource_id": item.resource_id,
                "resource_name": item.resource_name,
                "account_id": item.account_id,
                "region": item.region,
                "availability_zone": item.availability_zone,
                "state": item.state,
                "instance_type": item.instance_type,
                "launch_time": item.launch_time.isoformat()
                if item.launch_time
                else None,
                "tags": tags,
                "metadata": metadata,
                "usage": {
                    "cpu_utilization": round(cpu, 2),
                    "memory_utilization": round(memory_utilization, 2),
                    "network_in_mb": round(network_in, 2),
                    "network_out_mb": round(network_out, 2),
                    "received_bytes_mb": round(received_bytes / (1024 * 1024), 2),
                    "sent_bytes_mb": round(sent_bytes / (1024 * 1024), 2),
                    "disk_used_gb": disk_used_gb,
                    "disk_available_gb": disk_available_gb,
                    "trends": {
                        "cpu": _build_metric_trend(metric_history, "cpuutilization"),
                        "memory": _build_metric_trend(
                            metric_history, "memoryutilization"
                        ),
                    },
                },
                "maxops": {
                    "check_id": finding.check_id if finding else None,
                    "finding_type": None if finding_type == "healthy" else finding_type,
                    "severity": finding.severity if finding else None,
                    "title": finding.title if finding else None,
                    "recommended_action": finding.recommended_action
                    if finding
                    else None,
                    "potential_savings_monthly": round(savings_monthly, 2),
                    "potential_savings_yearly": round(savings_yearly, 2),
                    "status": status
                    or ("healthy" if finding_type == "healthy" else "actionable"),
                },
            }
        )

    total_instances = len(instances)
    healthy_instances = max(total_instances - actionable_instances, 0)

    return {
        "generated_at": generated_at.isoformat() if generated_at else None,
        "account_id": account_id,
        "summary": {
            "total_instances": total_instances,
            "running_instances": running_instances,
            "stopped_instances": stopped_instances,
            "actionable_instances": actionable_instances,
            "healthy_instances": healthy_instances,
            "average_cpu_utilization": round(total_cpu / total_instances, 2),
            "average_memory_utilization": round(total_memory / total_instances, 2),
            "average_network_in": round(total_network_in / total_instances, 2),
            "average_network_out": round(total_network_out / total_instances, 2),
            "monthly_cost_estimate": round(total_monthly_cost, 2),
            "potential_savings_yearly": round(total_yearly_savings, 2),
        },
        "dimensions": {
            "regions": _counter_to_rows(regions),
            "instance_types": _counter_to_rows(instance_types),
            "environments": _counter_to_rows(environments),
        },
        "findings_breakdown": _counter_to_rows(findings),
        "instances": instances,
    }


def get_s3_inventory_overview(db: Session) -> Dict[str, Any]:
    rows = (
        db.query(S3Inventory, MaxOpsInventory)
        .outerjoin(
            MaxOpsInventory,
            (MaxOpsInventory.resource_type == "s3")
            & (MaxOpsInventory.inventory_id == S3Inventory.inventory_id),
        )
        .order_by(S3Inventory.inventory_id.asc())
        .all()
    )

    if not rows:
        return _build_s3_overview_from_latest_check_results(db)

    generated_at = max(
        (item.generated_at for item, _ in rows if item.generated_at), default=None
    )
    account_id = next((item.account_id for item, _ in rows if item.account_id), None)

    regions = Counter()
    environments = Counter()
    teams = Counter()
    bucket_kinds = Counter()
    criticality = Counter()
    findings = Counter()

    actionable_buckets = 0
    versioned_buckets = 0
    logging_enabled_buckets = 0
    inventory_enabled_buckets = 0
    replicated_buckets = 0
    lifecycle_policy_buckets = 0
    total_size_gb = 0.0
    total_objects = 0
    total_monthly_cost = 0.0
    total_yearly_savings = 0.0

    buckets: List[Dict[str, Any]] = []

    for item, finding in rows:
        tags = item.tags_json or {}
        metadata = item.metadata_json or {}
        aws_payload = item.aws_payload_json or {}
        env = str(
            metadata.get("environment")
            or tags.get("env")
            or tags.get("environment")
            or "unknown"
        ).lower()
        team = str(metadata.get("team") or tags.get("team") or "unknown").lower()
        bucket_kind = str(metadata.get("bucket_kind") or "unknown").lower()
        criticality_value = str(
            metadata.get("criticality") or tags.get("criticality") or "unknown"
        ).lower()
        storage_class_mix = metadata.get("storage_class_mix") or {}
        lifecycle_rules = metadata.get("lifecycle_rules") or []

        versioning_enabled = (
            str(
                item.versioning_status or metadata.get("versioning_status") or ""
            ).lower()
            == "enabled"
        )
        logging_enabled = str(item.logging_enabled or "").lower() == "true" or bool(
            metadata.get("logging_enabled")
        )
        inventory_enabled = (
            int(
                item.inventory_configuration_count
                or metadata.get("inventory_configuration_count")
                or 0
            )
            > 0
        )
        replication_enabled = (
            int(
                item.replication_rule_count
                or metadata.get("replication_rule_count")
                or 0
            )
            > 0
        )
        lifecycle_enabled = len(lifecycle_rules) > 0

        status = str(
            (finding.metadata_json or {}).get("status")
            if finding and finding.metadata_json
            else ""
        )
        finding_type = (
            finding.finding_type if finding and finding.finding_type else "healthy"
        )
        savings_monthly = float(
            (finding.potential_savings_monthly if finding else 0.0) or 0.0
        )
        savings_yearly = float(
            (finding.potential_savings_yearly if finding else 0.0) or 0.0
        )
        monthly_cost = _coerce_float(
            item.estimated_monthly_cost,
            _coerce_float(metadata.get("estimated_monthly_cost")),
        )
        bucket_size_gb = _coerce_float(
            item.bucket_size_gb, _coerce_float(metadata.get("bucket_size_gb"))
        )
        object_count = int(item.object_count or metadata.get("object_count") or 0)

        regions.update([item.region or "unknown"])
        environments.update([env])
        teams.update([team])
        bucket_kinds.update([bucket_kind])
        criticality.update([criticality_value])
        findings.update([finding_type])

        if status == "actionable" or finding_type != "healthy":
            actionable_buckets += 1
        if versioning_enabled:
            versioned_buckets += 1
        if logging_enabled:
            logging_enabled_buckets += 1
        if inventory_enabled:
            inventory_enabled_buckets += 1
        if replication_enabled:
            replicated_buckets += 1
        if lifecycle_enabled:
            lifecycle_policy_buckets += 1

        total_size_gb += bucket_size_gb
        total_objects += object_count
        total_monthly_cost += monthly_cost
        total_yearly_savings += savings_yearly

        buckets.append(
            {
                "inventory_id": item.inventory_id,
                "resource_id": item.resource_id,
                "resource_name": item.resource_name,
                "account_id": item.account_id,
                "region": item.region,
                "state": item.state,
                "creation_date": item.creation_date.isoformat()
                if item.creation_date
                else None,
                "tags": tags,
                "metadata": metadata,
                "storage": {
                    "bucket_size_gb": round(bucket_size_gb, 2),
                    "object_count": object_count,
                    "estimated_monthly_cost": round(monthly_cost, 2),
                    "storage_class_mix": storage_class_mix,
                },
                "posture": {
                    "versioning_enabled": versioning_enabled,
                    "logging_enabled": logging_enabled,
                    "inventory_enabled": inventory_enabled,
                    "replication_enabled": replication_enabled,
                    "lifecycle_enabled": lifecycle_enabled,
                },
                "maxops": {
                    "check_id": finding.check_id if finding else None,
                    "finding_type": None if finding_type == "healthy" else finding_type,
                    "severity": finding.severity if finding else None,
                    "title": finding.title if finding else None,
                    "description": finding.description if finding else None,
                    "recommended_action": finding.recommended_action
                    if finding
                    else None,
                    "potential_savings_monthly": round(savings_monthly, 2),
                    "potential_savings_yearly": round(savings_yearly, 2),
                    "status": status
                    or ("healthy" if finding_type == "healthy" else "actionable"),
                    "evidence": finding.evidence_json if finding else {},
                    "current_config": finding.current_config_json if finding else {},
                    "target_config": finding.target_config_json if finding else {},
                    "metadata": finding.metadata_json if finding else {},
                },
                "aws_payload": aws_payload,
            }
        )

    total_buckets = len(buckets)
    healthy_buckets = max(total_buckets - actionable_buckets, 0)

    return {
        "generated_at": generated_at.isoformat() if generated_at else None,
        "account_id": account_id,
        "summary": {
            "total_buckets": total_buckets,
            "actionable_buckets": actionable_buckets,
            "healthy_buckets": healthy_buckets,
            "versioned_buckets": versioned_buckets,
            "logging_enabled_buckets": logging_enabled_buckets,
            "inventory_enabled_buckets": inventory_enabled_buckets,
            "replicated_buckets": replicated_buckets,
            "lifecycle_policy_buckets": lifecycle_policy_buckets,
            "total_size_gb": round(total_size_gb, 2),
            "total_objects": total_objects,
            "monthly_cost_estimate": round(total_monthly_cost, 2),
            "potential_savings_yearly": round(total_yearly_savings, 2),
        },
        "dimensions": {
            "regions": _counter_to_rows(regions),
            "environments": _counter_to_rows(environments),
            "teams": _counter_to_rows(teams),
            "bucket_kinds": _counter_to_rows(bucket_kinds),
            "criticality": _counter_to_rows(criticality),
        },
        "findings_breakdown": _counter_to_rows(findings),
        "posture_breakdown": {
            "versioning": {
                "enabled": versioned_buckets,
                "disabled": total_buckets - versioned_buckets,
            },
            "logging": {
                "enabled": logging_enabled_buckets,
                "disabled": total_buckets - logging_enabled_buckets,
            },
            "inventory": {
                "enabled": inventory_enabled_buckets,
                "disabled": total_buckets - inventory_enabled_buckets,
            },
            "replication": {
                "enabled": replicated_buckets,
                "disabled": total_buckets - replicated_buckets,
            },
            "lifecycle": {
                "enabled": lifecycle_policy_buckets,
                "disabled": total_buckets - lifecycle_policy_buckets,
            },
        },
        "buckets": buckets,
    }


def get_rds_inventory_overview(db: Session) -> Dict[str, Any]:
    rows = (
        db.query(RdsInventory, MaxOpsInventory)
        .outerjoin(
            MaxOpsInventory,
            (MaxOpsInventory.resource_type == "rds")
            & (MaxOpsInventory.inventory_id == RdsInventory.inventory_id),
        )
        .order_by(RdsInventory.inventory_id.asc())
        .all()
    )

    if not rows:
        return {
            "generated_at": None,
            "account_id": None,
            "summary": {
                "total_instances": 0,
                "actionable_instances": 0,
                "healthy_instances": 0,
                "graviton_instances": 0,
                "non_graviton_instances": 0,
                "idle_candidates": 0,
                "total_connections": 0.0,
                "monthly_cost_estimate": 0.0,
                "potential_savings_yearly": 0.0,
            },
            "dimensions": {
                "regions": [],
                "environments": [],
                "teams": [],
                "engines": [],
                "instance_classes": [],
            },
            "findings_breakdown": [],
            "instances": [],
        }

    generated_at = max(
        (item.generated_at for item, _ in rows if item.generated_at), default=None
    )
    account_id = next((item.account_id for item, _ in rows if item.account_id), None)

    regions = Counter()
    environments = Counter()
    teams = Counter()
    engines = Counter()
    instance_classes = Counter()
    findings = Counter()

    actionable_instances = 0
    graviton_instances = 0
    non_graviton_instances = 0
    idle_candidates = 0
    total_connections = 0.0
    total_monthly_cost = 0.0
    total_yearly_savings = 0.0

    instances: List[Dict[str, Any]] = []

    for item, finding in rows:
        tags = item.tags_json or {}
        metadata = item.metadata_json or {}
        aws_payload = item.aws_payload_json or {}
        env = str(tags.get("env") or metadata.get("environment") or "unknown").lower()
        team = str(metadata.get("team") or tags.get("team") or "unknown").lower()
        engine = str(item.engine or metadata.get("engine") or "unknown").lower()
        instance_class = str(
            item.db_instance_class or metadata.get("instance_class") or "unknown"
        ).lower()
        metric_history = (
            item.metric_history_json or metadata.get("metric_history") or {}
        )
        cpu = _coerce_float(
            item.avg_cpu_utilization, _coerce_float(metadata.get("avg_cpu_utilization"))
        )
        connections = _coerce_float(
            item.avg_connections, _coerce_float(metadata.get("avg_connections"))
        )
        read_iops = _coerce_float(
            item.avg_read_iops, _coerce_float(metadata.get("avg_read_iops"))
        )
        write_iops = _coerce_float(
            item.avg_write_iops, _coerce_float(metadata.get("avg_write_iops"))
        )
        resource_for_pricing = {
            "resource_type": "rds",
            "resource_id": item.resource_id,
            "region": item.region,
            "db_instance_class": item.db_instance_class,
            "metadata": metadata,
        }
        monthly_cost = _coerce_float(
            item.monthly_cost_estimate,
            estimate_baseline_monthly_cost("rds", resource_for_pricing),
        )
        savings_monthly = float(
            (finding.potential_savings_monthly if finding else 0.0) or 0.0
        )
        savings_yearly = float(
            (finding.potential_savings_yearly if finding else 0.0) or 0.0
        )
        status = str(
            (finding.metadata_json or {}).get("status")
            if finding and finding.metadata_json
            else ""
        )
        finding_type = (
            finding.finding_type if finding and finding.finding_type else "healthy"
        )
        is_graviton = _is_graviton_family(instance_class)

        regions.update([item.region or "unknown"])
        environments.update([env])
        teams.update([team])
        engines.update([engine])
        instance_classes.update([instance_class])
        findings.update([finding_type])

        if status == "actionable" or finding_type != "healthy":
            actionable_instances += 1
        if is_graviton:
            graviton_instances += 1
        else:
            non_graviton_instances += 1
        if finding and finding.check_id == "rds_idle_databases":
            idle_candidates += 1

        total_connections += connections
        total_monthly_cost += monthly_cost
        total_yearly_savings += savings_yearly

        instances.append(
            {
                "inventory_id": item.inventory_id,
                "resource_id": item.resource_id,
                "resource_name": item.resource_name,
                "account_id": item.account_id,
                "region": item.region,
                "availability_zone": item.availability_zone,
                "state": item.state,
                "engine": item.engine,
                "db_instance_class": item.db_instance_class,
                "created_at": item.created_at_source.isoformat()
                if item.created_at_source
                else None,
                "tags": tags,
                "metadata": metadata,
                "workload": {
                    "cpu_utilization": round(cpu, 2),
                    "connections": round(connections, 2),
                    "read_iops": round(read_iops, 2),
                    "write_iops": round(write_iops, 2),
                    "monthly_cost_estimate": round(monthly_cost, 2),
                    "is_graviton": is_graviton,
                    "trends": {
                        "cpu": _build_simple_metric_trend(
                            metric_history, "cpuutilization"
                        ),
                        "connections": _build_simple_metric_trend(
                            metric_history, "databaseconnections"
                        ),
                        "read_iops": _build_simple_metric_trend(
                            metric_history, "readiops"
                        ),
                        "write_iops": _build_simple_metric_trend(
                            metric_history, "writeiops"
                        ),
                    },
                },
                "maxops": {
                    "check_id": finding.check_id if finding else None,
                    "finding_type": None if finding_type == "healthy" else finding_type,
                    "severity": finding.severity if finding else None,
                    "title": finding.title if finding else None,
                    "description": finding.description if finding else None,
                    "recommended_action": finding.recommended_action
                    if finding
                    else None,
                    "potential_savings_monthly": round(savings_monthly, 2),
                    "potential_savings_yearly": round(savings_yearly, 2),
                    "status": status
                    or ("healthy" if finding_type == "healthy" else "actionable"),
                    "evidence": finding.evidence_json if finding else {},
                    "current_config": finding.current_config_json if finding else {},
                    "target_config": finding.target_config_json if finding else {},
                    "metadata": finding.metadata_json if finding else {},
                },
                "aws_payload": aws_payload,
            }
        )

    total_instances = len(instances)
    healthy_instances = max(total_instances - actionable_instances, 0)

    return {
        "generated_at": generated_at.isoformat() if generated_at else None,
        "account_id": account_id,
        "summary": {
            "total_instances": total_instances,
            "actionable_instances": actionable_instances,
            "healthy_instances": healthy_instances,
            "graviton_instances": graviton_instances,
            "non_graviton_instances": non_graviton_instances,
            "idle_candidates": idle_candidates,
            "total_connections": round(total_connections, 2),
            "monthly_cost_estimate": round(total_monthly_cost, 2),
            "potential_savings_yearly": round(total_yearly_savings, 2),
        },
        "dimensions": {
            "regions": _counter_to_rows(regions),
            "environments": _counter_to_rows(environments),
            "teams": _counter_to_rows(teams),
            "engines": _counter_to_rows(engines),
            "instance_classes": _counter_to_rows(instance_classes),
        },
        "findings_breakdown": _counter_to_rows(findings),
        "instances": instances,
    }


def get_elasticache_inventory_overview(db: Session) -> Dict[str, Any]:
    rows = (
        db.query(ElasticacheInventory, MaxOpsInventory)
        .outerjoin(
            MaxOpsInventory,
            (MaxOpsInventory.resource_type == "elasticache")
            & (MaxOpsInventory.inventory_id == ElasticacheInventory.inventory_id),
        )
        .order_by(ElasticacheInventory.inventory_id.asc())
        .all()
    )
    # Hide legacy member rows immediately, even before the next scan/import
    # performs the durable cleanup and snooze migration.
    rows = [
        (item, finding)
        for item, finding in rows
        if not is_elasticache_member_cluster(
            {
                "resource_type": item.resource_type,
                "metadata": item.metadata_json or {},
            }
        )
    ]

    if not rows:
        return {
            "generated_at": None,
            "account_id": None,
            "summary": {
                "total_resources": 0,
                "actionable_resources": 0,
                "healthy_resources": 0,
                "clusters": 0,
                "replication_groups": 0,
                "redis_resources": 0,
                "valkey_resources": 0,
                "graviton_resources": 0,
                "monthly_cost_estimate": 0.0,
                "potential_savings_yearly": 0.0,
            },
            "dimensions": {
                "regions": [],
                "environments": [],
                "teams": [],
                "resource_types": [],
                "engines": [],
                "node_types": [],
            },
            "findings_breakdown": [],
            "resources": [],
        }

    generated_at = max(
        (item.generated_at for item, _ in rows if item.generated_at), default=None
    )
    account_id = next((item.account_id for item, _ in rows if item.account_id), None)

    regions = Counter()
    environments = Counter()
    teams = Counter()
    resource_types = Counter()
    engines = Counter()
    node_types = Counter()
    findings = Counter()

    actionable_resources = 0
    clusters = 0
    replication_groups = 0
    redis_resources = 0
    valkey_resources = 0
    graviton_resources = 0
    total_monthly_cost = 0.0
    total_yearly_savings = 0.0

    resources: List[Dict[str, Any]] = []

    for item, finding in rows:
        tags = item.tags_json or {}
        metadata = item.metadata_json or {}
        aws_payload = item.aws_payload_json or {}
        env = str(tags.get("env") or metadata.get("environment") or "unknown").lower()
        team = str(metadata.get("team") or tags.get("team") or "unknown").lower()
        resource_type = str(
            item.resource_type or metadata.get("resource_kind") or "unknown"
        ).lower()
        engine = str(item.engine or metadata.get("engine") or "unknown").lower()
        node_type = str(
            item.cache_node_type or metadata.get("cache_node_type") or "unknown"
        ).lower()
        metric_history = (
            item.metric_history_json or metadata.get("metric_history") or {}
        )
        curritems = _coerce_float(
            item.avg_curritems, _coerce_float(metadata.get("avg_curritems"))
        )
        keycount = _coerce_float(
            item.avg_keycount, _coerce_float(metadata.get("avg_keycount"))
        )
        resource_for_pricing = {
            "resource_type": "elasticache",
            "resource_id": item.resource_id,
            "region": item.region,
            "cache_node_type": item.cache_node_type,
            "metadata": metadata,
        }
        monthly_cost = _coerce_float(
            item.monthly_cost_estimate,
            estimate_baseline_monthly_cost("elasticache", resource_for_pricing),
        )
        savings_monthly = float(
            (finding.potential_savings_monthly if finding else 0.0) or 0.0
        )
        savings_yearly = float(
            (finding.potential_savings_yearly if finding else 0.0) or 0.0
        )
        status = str(
            (finding.metadata_json or {}).get("status")
            if finding and finding.metadata_json
            else ""
        )
        finding_type = (
            finding.finding_type if finding and finding.finding_type else "healthy"
        )
        is_graviton = _is_graviton_family(node_type)

        regions.update([item.region or "unknown"])
        environments.update([env])
        teams.update([team])
        resource_types.update([resource_type])
        engines.update([engine])
        node_types.update([node_type])
        findings.update([finding_type])

        if status == "actionable" or finding_type != "healthy":
            actionable_resources += 1
        if resource_type == "elasticache_cluster":
            clusters += 1
        if resource_type == "elasticache_replication_group":
            replication_groups += 1
        if engine == "redis":
            redis_resources += 1
        if engine == "valkey":
            valkey_resources += 1
        if is_graviton:
            graviton_resources += 1

        total_monthly_cost += monthly_cost
        total_yearly_savings += savings_yearly

        resources.append(
            {
                "inventory_id": item.inventory_id,
                "resource_id": item.resource_id,
                "resource_name": item.resource_name,
                "resource_type": item.resource_type,
                "account_id": item.account_id,
                "region": item.region,
                "availability_zone": item.availability_zone,
                "state": item.state,
                "tags": tags,
                "metadata": metadata,
                "workload": {
                    "curritems": round(curritems, 2),
                    "keycount": round(keycount, 2),
                    "monthly_cost_estimate": round(monthly_cost, 2),
                    "is_graviton": is_graviton,
                    "trends": {
                        "curritems": _build_simple_metric_trend(
                            metric_history, "curritems"
                        ),
                        "keycount": _build_simple_metric_trend(
                            metric_history, "keycount"
                        ),
                    },
                },
                "maxops": {
                    "check_id": finding.check_id if finding else None,
                    "finding_type": None if finding_type == "healthy" else finding_type,
                    "severity": finding.severity if finding else None,
                    "title": finding.title if finding else None,
                    "description": finding.description if finding else None,
                    "recommended_action": finding.recommended_action
                    if finding
                    else None,
                    "potential_savings_monthly": round(savings_monthly, 2),
                    "potential_savings_yearly": round(savings_yearly, 2),
                    "status": status
                    or ("healthy" if finding_type == "healthy" else "actionable"),
                    "evidence": finding.evidence_json if finding else {},
                    "current_config": finding.current_config_json if finding else {},
                    "target_config": finding.target_config_json if finding else {},
                    "metadata": finding.metadata_json if finding else {},
                },
                "aws_payload": aws_payload,
            }
        )

    total_resources = len(resources)
    healthy_resources = max(total_resources - actionable_resources, 0)

    return {
        "generated_at": generated_at.isoformat() if generated_at else None,
        "account_id": account_id,
        "summary": {
            "total_resources": total_resources,
            "actionable_resources": actionable_resources,
            "healthy_resources": healthy_resources,
            "clusters": clusters,
            "replication_groups": replication_groups,
            "redis_resources": redis_resources,
            "valkey_resources": valkey_resources,
            "graviton_resources": graviton_resources,
            "monthly_cost_estimate": round(total_monthly_cost, 2),
            "potential_savings_yearly": round(total_yearly_savings, 2),
        },
        "dimensions": {
            "regions": _counter_to_rows(regions),
            "environments": _counter_to_rows(environments),
            "teams": _counter_to_rows(teams),
            "resource_types": _counter_to_rows(resource_types),
            "engines": _counter_to_rows(engines),
            "node_types": _counter_to_rows(node_types),
        },
        "findings_breakdown": _counter_to_rows(findings),
        "resources": resources,
    }


def get_asg_inventory_overview(db: Session) -> Dict[str, Any]:
    rows = (
        db.query(AsgInventory, MaxOpsInventory)
        .outerjoin(
            MaxOpsInventory,
            (MaxOpsInventory.resource_type == "asg")
            & (MaxOpsInventory.inventory_id == AsgInventory.inventory_id),
        )
        .order_by(AsgInventory.inventory_id.asc())
        .all()
    )

    if not rows:
        return {
            "generated_at": None,
            "account_id": None,
            "summary": {
                "total_groups": 0,
                "active_groups": 0,
                "actionable_groups": 0,
                "healthy_groups": 0,
                "monthly_cost_estimate": 0.0,
                "potential_savings_yearly": 0.0,
                "total_min_size": 0,
                "total_desired_capacity": 0,
                "total_max_size": 0,
            },
            "dimensions": {
                "regions": [],
                "environments": [],
                "teams": [],
                "instance_types": [],
                "platforms": [],
            },
            "findings_breakdown": [],
            "resources": [],
        }

    generated_at = max(
        (item.generated_at for item, _ in rows if item.generated_at), default=None
    )
    account_id = next((item.account_id for item, _ in rows if item.account_id), None)

    regions = Counter()
    environments = Counter()
    teams = Counter()
    instance_types = Counter()
    platforms = Counter()
    findings = Counter()

    active_groups = 0
    actionable_groups = 0
    total_monthly_cost = 0.0
    total_yearly_savings = 0.0
    total_min_size = 0
    total_desired_capacity = 0
    total_max_size = 0

    resources: List[Dict[str, Any]] = []

    for item, finding in rows:
        tags = item.tags_json or {}
        metadata = item.metadata_json or {}
        aws_payload = item.aws_payload_json or {}
        env = str(tags.get("env") or metadata.get("environment") or "unknown").lower()
        team = str(metadata.get("team") or tags.get("team") or "unknown").lower()
        instance_type = str(item.instance_type or "unknown").lower()
        platform = str(item.platform_normalized or "unknown").lower()
        pricing_resource = {
            "resource_type": "ec2",
            "resource_id": item.resource_id,
            "region": item.region,
            "instance_type": item.instance_type,
            "platform": item.platform_normalized,
            "metadata": metadata,
        }
        monthly_unit_cost = estimate_baseline_monthly_cost("ec2", pricing_resource)
        monthly_cost = round(monthly_unit_cost * max(item.desired_capacity or 0, 0), 2)
        savings_monthly = float(
            (finding.potential_savings_monthly if finding else 0.0) or 0.0
        )
        savings_yearly = float(
            (finding.potential_savings_yearly if finding else 0.0) or 0.0
        )
        status = str(
            (finding.metadata_json or {}).get("status")
            if finding and finding.metadata_json
            else ""
        )
        finding_type = (
            finding.finding_type if finding and finding.finding_type else "healthy"
        )
        availability_zones = metadata.get("availability_zones") or []
        in_service_instance_ids = metadata.get("in_service_instance_ids") or []

        regions.update([item.region or "unknown"])
        environments.update([env])
        teams.update([team])
        instance_types.update([instance_type])
        platforms.update([platform])
        findings.update([finding_type])

        if str(item.state or "").lower() == "active":
            active_groups += 1
        if status == "actionable" or finding_type != "healthy":
            actionable_groups += 1

        total_monthly_cost += monthly_cost
        total_yearly_savings += savings_yearly
        total_min_size += int(item.min_size or 0)
        total_desired_capacity += int(item.desired_capacity or 0)
        total_max_size += int(item.max_size or 0)

        resources.append(
            {
                "inventory_id": item.inventory_id,
                "resource_id": item.resource_id,
                "resource_name": item.resource_name,
                "resource_type": item.resource_type,
                "account_id": item.account_id,
                "region": item.region,
                "state": item.state,
                "instance_type": item.instance_type,
                "platform_normalized": item.platform_normalized,
                "tags": tags,
                "metadata": metadata,
                "capacity": {
                    "min_size": int(item.min_size or 0),
                    "desired_capacity": int(item.desired_capacity or 0),
                    "max_size": int(item.max_size or 0),
                    "in_service_instances": len(in_service_instance_ids),
                    "availability_zone_count": len(availability_zones),
                    "monthly_cost_estimate": monthly_cost,
                },
                "maxops": {
                    "check_id": finding.check_id if finding else None,
                    "finding_type": None if finding_type == "healthy" else finding_type,
                    "severity": finding.severity if finding else None,
                    "title": finding.title if finding else None,
                    "description": finding.description if finding else None,
                    "recommended_action": finding.recommended_action
                    if finding
                    else None,
                    "potential_savings_monthly": round(savings_monthly, 2),
                    "potential_savings_yearly": round(savings_yearly, 2),
                    "status": status
                    or ("healthy" if finding_type == "healthy" else "actionable"),
                    "evidence": finding.evidence_json if finding else {},
                    "current_config": finding.current_config_json if finding else {},
                    "target_config": finding.target_config_json if finding else {},
                    "metadata": finding.metadata_json if finding else {},
                },
                "aws_payload": aws_payload,
            }
        )

    total_groups = len(resources)
    healthy_groups = max(total_groups - actionable_groups, 0)

    return {
        "generated_at": generated_at.isoformat() if generated_at else None,
        "account_id": account_id,
        "summary": {
            "total_groups": total_groups,
            "active_groups": active_groups,
            "actionable_groups": actionable_groups,
            "healthy_groups": healthy_groups,
            "monthly_cost_estimate": round(total_monthly_cost, 2),
            "potential_savings_yearly": round(total_yearly_savings, 2),
            "total_min_size": total_min_size,
            "total_desired_capacity": total_desired_capacity,
            "total_max_size": total_max_size,
        },
        "dimensions": {
            "regions": _counter_to_rows(regions),
            "environments": _counter_to_rows(environments),
            "teams": _counter_to_rows(teams),
            "instance_types": _counter_to_rows(instance_types),
            "platforms": _counter_to_rows(platforms),
        },
        "findings_breakdown": _counter_to_rows(findings),
        "resources": resources,
    }


def get_dynamodb_inventory_overview(db: Session) -> Dict[str, Any]:
    rows = (
        db.query(DynamoDbInventory, MaxOpsInventory)
        .outerjoin(
            MaxOpsInventory,
            (MaxOpsInventory.resource_type == "dynamodb")
            & (MaxOpsInventory.inventory_id == DynamoDbInventory.inventory_id),
        )
        .order_by(DynamoDbInventory.inventory_id.asc())
        .all()
    )

    if not rows:
        return {
            "generated_at": None,
            "account_id": None,
            "summary": {
                "total_resources": 0,
                "actionable_resources": 0,
                "healthy_resources": 0,
                "tables": 0,
                "gsis": 0,
                "provisioned_resources": 0,
                "on_demand_resources": 0,
                "monthly_cost_estimate": 0.0,
                "potential_savings_yearly": 0.0,
                "total_item_count": 0,
                "avg_consumed_rcu": 0.0,
                "avg_consumed_wcu": 0.0,
            },
            "dimensions": {
                "regions": [],
                "environments": [],
                "teams": [],
                "resource_types": [],
                "billing_modes": [],
                "table_classes": [],
            },
            "findings_breakdown": [],
            "resources": [],
        }

    generated_at = max(
        (item.generated_at for item, _ in rows if item.generated_at), default=None
    )
    account_id = next((item.account_id for item, _ in rows if item.account_id), None)

    regions = Counter()
    environments = Counter()
    teams = Counter()
    resource_types = Counter()
    billing_modes = Counter()
    table_classes = Counter()
    findings = Counter()

    actionable_resources = 0
    tables = 0
    gsis = 0
    provisioned_resources = 0
    on_demand_resources = 0
    total_monthly_cost = 0.0
    total_yearly_savings = 0.0
    total_item_count = 0
    total_avg_consumed_rcu = 0.0
    total_avg_consumed_wcu = 0.0

    resources: List[Dict[str, Any]] = []

    for item, finding in rows:
        tags = item.tags_json or {}
        metadata = item.metadata_json or {}
        aws_payload = item.aws_payload_json or {}
        env = str(tags.get("env") or metadata.get("environment") or "unknown").lower()
        team = str(metadata.get("team") or tags.get("team") or "unknown").lower()
        table_class = str(
            item.table_class or metadata.get("table_class") or "unknown"
        ).lower()
        metric_history = (
            item.metric_history_json or metadata.get("metric_history") or {}
        )
        item_count = int(metadata.get("item_count") or item.item_count or 0)
        read_capacity_units = _coerce_float(
            item.read_capacity_units, _coerce_float(metadata.get("read_capacity_units"))
        )
        write_capacity_units = _coerce_float(
            item.write_capacity_units,
            _coerce_float(metadata.get("write_capacity_units")),
        )
        avg_consumed_rcu = _coerce_float(metadata.get("avg_consumed_rcu"))
        avg_consumed_wcu = _coerce_float(metadata.get("avg_consumed_wcu"))
        resource_type = str(item.resource_type or "dynamodb").lower()
        billing_mode_value = (
            item.billing_mode
            or metadata.get("billing_mode")
            or metadata.get("BillingMode")
        )
        if not billing_mode_value and (
            read_capacity_units > 0 or write_capacity_units > 0
        ):
            billing_mode_value = "PROVISIONED"
        billing_mode = str(billing_mode_value or "unknown").lower()
        resource_for_pricing = {
            "resource_type": resource_type,
            "resource_id": item.resource_id,
            "resource_name": item.resource_name,
            "region": item.region,
            "billing_mode": billing_mode_value,
            "metadata": metadata,
        }
        monthly_cost = _coerce_float(
            item.monthly_cost_estimate,
            estimate_baseline_monthly_cost(resource_type, resource_for_pricing),
        )
        p95_consumed_rcu = _coerce_float(metadata.get("p95_consumed_rcu"))
        p95_consumed_wcu = _coerce_float(metadata.get("p95_consumed_wcu"))
        avg_read_throttle_events = _coerce_float(
            metadata.get("avg_read_throttle_events")
        )
        avg_write_throttle_events = _coerce_float(
            metadata.get("avg_write_throttle_events")
        )
        savings_monthly = float(
            (finding.potential_savings_monthly if finding else 0.0) or 0.0
        )
        savings_yearly = float(
            (finding.potential_savings_yearly if finding else 0.0) or 0.0
        )
        status = str(
            (finding.metadata_json or {}).get("status")
            if finding and finding.metadata_json
            else ""
        )
        finding_type = (
            finding.finding_type if finding and finding.finding_type else "healthy"
        )

        regions.update([item.region or "unknown"])
        environments.update([env])
        teams.update([team])
        resource_types.update([resource_type])
        billing_modes.update([billing_mode])
        table_classes.update([table_class])
        findings.update([finding_type])

        if status == "actionable" or finding_type != "healthy":
            actionable_resources += 1
        if resource_type == "dynamodb_table":
            tables += 1
        if resource_type == "dynamodb_gsi":
            gsis += 1
        if billing_mode == "provisioned":
            provisioned_resources += 1
        if billing_mode == "pay_per_request":
            on_demand_resources += 1

        total_monthly_cost += monthly_cost
        total_yearly_savings += savings_yearly
        total_item_count += item_count
        total_avg_consumed_rcu += avg_consumed_rcu
        total_avg_consumed_wcu += avg_consumed_wcu

        resources.append(
            {
                "inventory_id": item.inventory_id,
                "resource_id": item.resource_id,
                "resource_name": item.resource_name,
                "resource_type": item.resource_type,
                "account_id": item.account_id,
                "region": item.region,
                "state": item.state,
                "billing_mode": billing_mode_value,
                "table_name": item.table_name,
                "table_class": item.table_class,
                "tags": tags,
                "metadata": metadata,
                "workload": {
                    "item_count": item_count,
                    "read_capacity_units": round(read_capacity_units, 2),
                    "write_capacity_units": round(write_capacity_units, 2),
                    "avg_consumed_rcu": round(avg_consumed_rcu, 4),
                    "avg_consumed_wcu": round(avg_consumed_wcu, 4),
                    "p95_consumed_rcu": round(p95_consumed_rcu, 4),
                    "p95_consumed_wcu": round(p95_consumed_wcu, 4),
                    "avg_read_throttle_events": round(avg_read_throttle_events, 4),
                    "avg_write_throttle_events": round(avg_write_throttle_events, 4),
                    "monthly_cost_estimate": round(monthly_cost, 2),
                    "trends": {
                        "consumed_rcu": _build_simple_metric_trend(
                            metric_history, "consumedreadcapacityunits"
                        ),
                        "consumed_wcu": _build_simple_metric_trend(
                            metric_history, "consumedwritecapacityunits"
                        ),
                        "item_count": _build_simple_metric_trend(
                            metric_history, "itemcount"
                        ),
                        "read_throttle_events": _build_simple_metric_trend(
                            metric_history, "readthrottleevents"
                        ),
                        "write_throttle_events": _build_simple_metric_trend(
                            metric_history, "writethrottleevents"
                        ),
                    },
                },
                "maxops": {
                    "check_id": finding.check_id if finding else None,
                    "finding_type": None if finding_type == "healthy" else finding_type,
                    "severity": finding.severity if finding else None,
                    "title": finding.title if finding else None,
                    "description": finding.description if finding else None,
                    "recommended_action": finding.recommended_action
                    if finding
                    else None,
                    "potential_savings_monthly": round(savings_monthly, 2),
                    "potential_savings_yearly": round(savings_yearly, 2),
                    "status": status
                    or ("healthy" if finding_type == "healthy" else "actionable"),
                    "evidence": finding.evidence_json if finding else {},
                    "current_config": finding.current_config_json if finding else {},
                    "target_config": finding.target_config_json if finding else {},
                    "metadata": finding.metadata_json if finding else {},
                },
                "aws_payload": aws_payload,
            }
        )

    total_resources = len(resources)
    healthy_resources = max(total_resources - actionable_resources, 0)

    return {
        "generated_at": generated_at.isoformat() if generated_at else None,
        "account_id": account_id,
        "summary": {
            "total_resources": total_resources,
            "actionable_resources": actionable_resources,
            "healthy_resources": healthy_resources,
            "tables": tables,
            "gsis": gsis,
            "provisioned_resources": provisioned_resources,
            "on_demand_resources": on_demand_resources,
            "monthly_cost_estimate": round(total_monthly_cost, 2),
            "potential_savings_yearly": round(total_yearly_savings, 2),
            "total_item_count": total_item_count,
            "avg_consumed_rcu": round(total_avg_consumed_rcu / total_resources, 4),
            "avg_consumed_wcu": round(total_avg_consumed_wcu / total_resources, 4),
        },
        "dimensions": {
            "regions": _counter_to_rows(regions),
            "environments": _counter_to_rows(environments),
            "teams": _counter_to_rows(teams),
            "resource_types": _counter_to_rows(resource_types),
            "billing_modes": _counter_to_rows(billing_modes),
            "table_classes": _counter_to_rows(table_classes),
        },
        "findings_breakdown": _counter_to_rows(findings),
        "resources": resources,
    }


def get_ebs_inventory_overview(db: Session) -> Dict[str, Any]:
    rows = (
        db.query(EbsInventory, MaxOpsInventory)
        .outerjoin(
            MaxOpsInventory,
            (MaxOpsInventory.resource_type == "ebs")
            & (MaxOpsInventory.inventory_id == EbsInventory.inventory_id),
        )
        .order_by(EbsInventory.inventory_id.asc())
        .all()
    )

    if not rows:
        return {
            "generated_at": None,
            "account_id": None,
            "summary": {
                "total_volumes": 0,
                "actionable_volumes": 0,
                "healthy_volumes": 0,
                "attached_volumes": 0,
                "unattached_volumes": 0,
                "gp3_volumes": 0,
                "provisioned_iops_volumes": 0,
                "monthly_cost_estimate": 0.0,
                "potential_savings_yearly": 0.0,
                "total_size_gb": 0.0,
                "avg_iops": 0.0,
                "avg_throughput_mb": 0.0,
            },
            "dimensions": {
                "regions": [],
                "environments": [],
                "teams": [],
                "volume_types": [],
                "states": [],
                "criticality": [],
            },
            "findings_breakdown": [],
            "volumes": [],
        }

    generated_at = max(
        (item.generated_at for item, _ in rows if item.generated_at), default=None
    )
    account_id = next((item.account_id for item, _ in rows if item.account_id), None)

    regions = Counter()
    environments = Counter()
    teams = Counter()
    volume_types = Counter()
    states = Counter()
    criticality = Counter()
    findings = Counter()

    actionable_volumes = 0
    attached_volumes = 0
    unattached_volumes = 0
    gp3_volumes = 0
    provisioned_iops_volumes = 0
    total_monthly_cost = 0.0
    total_yearly_savings = 0.0
    total_size_gb = 0.0
    total_avg_iops = 0.0
    total_avg_throughput_mb = 0.0

    volumes: List[Dict[str, Any]] = []

    for item, finding in rows:
        tags = item.tags_json or {}
        metadata = item.metadata_json or {}
        aws_payload = item.aws_payload_json or {}
        env = str(tags.get("env") or metadata.get("environment") or "unknown").lower()
        team = str(metadata.get("team") or tags.get("team") or "unknown").lower()
        volume_type = str(
            item.volume_type or metadata.get("volume_type") or "unknown"
        ).lower()
        state = str(item.state or "unknown").lower()
        metric_history = (
            item.metric_history_json or metadata.get("metric_history") or {}
        )
        size_gb = _coerce_float(item.size_gb, _coerce_float(metadata.get("size")))
        avg_iops = _coerce_float(item.avg_iops, _coerce_float(metadata.get("avg_iops")))
        avg_throughput_mb = _coerce_float(
            item.avg_throughput_mb, _coerce_float(metadata.get("avg_throughput_mb"))
        )
        resource_for_pricing = {
            "resource_type": "ebs",
            "resource_id": item.resource_id,
            "region": item.region,
            "volume_type": volume_type,
            "size_gb": size_gb,
            "metadata": metadata,
        }
        stored_monthly_cost = _coerce_optional_float(item.monthly_cost_estimate)
        monthly_cost = (
            stored_monthly_cost
            if stored_monthly_cost is not None and stored_monthly_cost > 0
            else estimate_baseline_monthly_cost("ebs", resource_for_pricing)
        )
        savings_monthly = float(
            (finding.potential_savings_monthly if finding else 0.0) or 0.0
        )
        savings_yearly = float(
            (finding.potential_savings_yearly if finding else 0.0) or 0.0
        )
        status = str(
            (finding.metadata_json or {}).get("status")
            if finding and finding.metadata_json
            else ""
        )
        finding_type = (
            finding.finding_type if finding and finding.finding_type else "healthy"
        )
        is_attached = (
            str(item.attached or metadata.get("attached") or "").lower() == "true"
        )
        criticality_value = str(
            metadata.get("criticality") or tags.get("criticality") or "unknown"
        ).lower()

        regions.update([item.region or "unknown"])
        environments.update([env])
        teams.update([team])
        volume_types.update([volume_type])
        states.update([state])
        criticality.update([criticality_value])
        findings.update([finding_type])

        if status == "actionable" or finding_type != "healthy":
            actionable_volumes += 1
        if is_attached:
            attached_volumes += 1
        else:
            unattached_volumes += 1
        if volume_type == "gp3":
            gp3_volumes += 1
        if volume_type in {"io1", "io2"}:
            provisioned_iops_volumes += 1

        total_monthly_cost += monthly_cost
        total_yearly_savings += savings_yearly
        total_size_gb += size_gb
        total_avg_iops += avg_iops
        total_avg_throughput_mb += avg_throughput_mb

        volumes.append(
            {
                "inventory_id": item.inventory_id,
                "resource_id": item.resource_id,
                "resource_name": item.resource_name,
                "resource_type": item.resource_type,
                "account_id": item.account_id,
                "region": item.region,
                "availability_zone": item.availability_zone,
                "state": item.state,
                "volume_type": item.volume_type,
                "attached": is_attached,
                "tags": tags,
                "metadata": metadata,
                "workload": {
                    "size_gb": round(size_gb, 2),
                    "iops": int(metadata.get("iops") or item.iops or 0),
                    "throughput": int(
                        metadata.get("throughput") or item.throughput or 0
                    ),
                    "avg_iops": round(avg_iops, 2),
                    "avg_throughput_mb": round(avg_throughput_mb, 2),
                    "monthly_cost_estimate": round(monthly_cost, 2),
                    "trends": {
                        "read_ops": _build_simple_metric_trend(
                            metric_history, "volumereadops"
                        ),
                        "write_ops": _build_simple_metric_trend(
                            metric_history, "volumewriteops"
                        ),
                        "read_bytes": _build_simple_metric_trend(
                            metric_history, "volumereadbytes"
                        ),
                        "write_bytes": _build_simple_metric_trend(
                            metric_history, "volumewritebytes"
                        ),
                    },
                },
                "maxops": {
                    "check_id": finding.check_id if finding else None,
                    "finding_type": None if finding_type == "healthy" else finding_type,
                    "severity": finding.severity if finding else None,
                    "title": finding.title if finding else None,
                    "description": finding.description if finding else None,
                    "recommended_action": finding.recommended_action
                    if finding
                    else None,
                    "potential_savings_monthly": round(savings_monthly, 2),
                    "potential_savings_yearly": round(savings_yearly, 2),
                    "status": status
                    or ("healthy" if finding_type == "healthy" else "actionable"),
                    "evidence": finding.evidence_json if finding else {},
                    "current_config": finding.current_config_json if finding else {},
                    "target_config": finding.target_config_json if finding else {},
                    "metadata": finding.metadata_json if finding else {},
                },
                "aws_payload": aws_payload,
            }
        )

    total_volumes = len(volumes)
    healthy_volumes = max(total_volumes - actionable_volumes, 0)

    return {
        "generated_at": generated_at.isoformat() if generated_at else None,
        "account_id": account_id,
        "summary": {
            "total_volumes": total_volumes,
            "actionable_volumes": actionable_volumes,
            "healthy_volumes": healthy_volumes,
            "attached_volumes": attached_volumes,
            "unattached_volumes": unattached_volumes,
            "gp3_volumes": gp3_volumes,
            "provisioned_iops_volumes": provisioned_iops_volumes,
            "monthly_cost_estimate": round(total_monthly_cost, 2),
            "potential_savings_yearly": round(total_yearly_savings, 2),
            "total_size_gb": round(total_size_gb, 2),
            "avg_iops": round(total_avg_iops / total_volumes, 2),
            "avg_throughput_mb": round(total_avg_throughput_mb / total_volumes, 2),
        },
        "dimensions": {
            "regions": _counter_to_rows(regions),
            "environments": _counter_to_rows(environments),
            "teams": _counter_to_rows(teams),
            "volume_types": _counter_to_rows(volume_types),
            "states": _counter_to_rows(states),
            "criticality": _counter_to_rows(criticality),
        },
        "findings_breakdown": _counter_to_rows(findings),
        "volumes": volumes,
    }


def _counter_to_rows(counter: Counter) -> List[Dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common()]


def _empty_s3_overview() -> Dict[str, Any]:
    return {
        "generated_at": None,
        "account_id": None,
        "summary": {
            "total_buckets": 0,
            "actionable_buckets": 0,
            "healthy_buckets": 0,
            "versioned_buckets": 0,
            "logging_enabled_buckets": 0,
            "inventory_enabled_buckets": 0,
            "replicated_buckets": 0,
            "lifecycle_policy_buckets": 0,
            "total_size_gb": 0.0,
            "total_objects": 0,
            "monthly_cost_estimate": 0.0,
            "potential_savings_yearly": 0.0,
        },
        "dimensions": {
            "regions": [],
            "environments": [],
            "teams": [],
            "bucket_kinds": [],
            "criticality": [],
        },
        "findings_breakdown": [],
        "posture_breakdown": {
            "versioning": {"enabled": 0, "disabled": 0},
            "logging": {"enabled": 0, "disabled": 0},
            "inventory": {"enabled": 0, "disabled": 0},
            "replication": {"enabled": 0, "disabled": 0},
            "lifecycle": {"enabled": 0, "disabled": 0},
        },
        "buckets": [],
    }


def _truthy_metadata_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "enabled", "on"}


def _first_present(
    metadata: Dict[str, Any], keys: List[str], default: Any = None
) -> Any:
    for key in keys:
        if key in metadata and metadata.get(key) is not None:
            return metadata.get(key)
    return default


def _build_s3_overview_from_latest_check_results(db: Session) -> Dict[str, Any]:
    rows = (
        db.query(
            OnboardingCheckResult,
            OnboardingExecution.completed_at,
            OnboardingExecution.started_at,
        )
        .join(
            OnboardingExecution,
            OnboardingCheckResult.execution_id == OnboardingExecution.id,
        )
        .filter(OnboardingCheckResult.resource_type == "s3")
        .filter(OnboardingCheckResult.status == "completed")
        .filter(
            (OnboardingExecution.completed_at.isnot(None))
            | (OnboardingExecution.started_at.isnot(None))
        )
        .order_by(
            OnboardingCheckResult.check_id.asc(),
            OnboardingExecution.completed_at.desc(),
            OnboardingExecution.started_at.desc(),
        )
        .all()
    )

    latest_by_check: Dict[str, tuple[OnboardingCheckResult, Optional[datetime]]] = {}
    for result, completed_at, started_at in rows:
        if result.check_id in latest_by_check:
            continue
        latest_by_check[result.check_id] = (result, completed_at or started_at)

    if not latest_by_check:
        return _empty_s3_overview()

    regions = Counter()
    environments = Counter()
    teams = Counter()
    bucket_kinds = Counter()
    criticality = Counter()
    findings = Counter()
    total_size_gb = 0.0
    total_objects = 0
    total_monthly_cost = 0.0
    total_yearly_savings = 0.0
    versioned_buckets = 0
    logging_enabled_buckets = 0
    inventory_enabled_buckets = 0
    replicated_buckets = 0
    lifecycle_policy_buckets = 0
    account_id = None
    latest_time = None
    buckets: List[Dict[str, Any]] = []
    excluded_by_check: Dict[str, set[str]] = {}

    for check_result, execution_time in latest_by_check.values():
        resources = (check_result.metadata_json or {}).get("resources") or []
        excluded_ids = excluded_by_check.setdefault(
            check_result.check_id,
            get_excluded_resource_ids(db, check_result.check_id),
        )
        if execution_time and (latest_time is None or execution_time > latest_time):
            latest_time = execution_time

        for resource in resources:
            resource_id = str(resource.get("resource_id") or "").strip()
            if not resource_id or resource_id in excluded_ids:
                continue

            metadata = resource.get("metadata") or {}
            tags = resource.get("tags") or {}
            pricing = resource.get("pricing") or metadata.get("pricing") or {}
            env = str(
                metadata.get("environment")
                or tags.get("env")
                or tags.get("environment")
                or "unknown"
            ).lower()
            team = str(metadata.get("team") or tags.get("team") or "unknown").lower()
            kind = str(
                metadata.get("bucket_kind") or metadata.get("kind") or "unknown"
            ).lower()
            criticality_value = str(
                metadata.get("criticality") or tags.get("criticality") or "unknown"
            ).lower()
            region = resource.get("region") or metadata.get("region") or "global"
            monthly_cost = _coerce_float(
                _first_present(
                    metadata,
                    ["estimated_monthly_cost", "monthly_cost_estimate", "monthly_cost"],
                ),
                _coerce_float(pricing.get("monthly_cost")),
            )
            bucket_size_gb = _coerce_float(
                _first_present(metadata, ["bucket_size_gb", "size_gb", "storage_gb"])
            )
            object_count = int(
                _coerce_float(
                    _first_present(
                        metadata, ["object_count", "objects", "total_objects"]
                    )
                )
            )
            savings_monthly = _coerce_float(metadata.get("potential_savings_monthly"))
            savings_yearly = _coerce_float(
                metadata.get("potential_savings_yearly"), savings_monthly * 12
            )

            versioning_enabled = _truthy_metadata_value(
                _first_present(metadata, ["versioning_enabled", "versioning_status"])
            )
            logging_enabled = _truthy_metadata_value(
                _first_present(metadata, ["logging_enabled"])
            )
            inventory_enabled = _truthy_metadata_value(
                _first_present(
                    metadata, ["inventory_enabled", "inventory_configuration_count"]
                )
            )
            replication_enabled = _truthy_metadata_value(
                _first_present(
                    metadata, ["replication_enabled", "replication_rule_count"]
                )
            )
            lifecycle_enabled = _truthy_metadata_value(
                _first_present(
                    metadata,
                    [
                        "lifecycle_enabled",
                        "lifecycle_rule_count",
                        "lifecycle_rules_count",
                    ],
                )
            )
            finding_type = str(
                metadata.get("finding_type") or check_result.check_id or "s3_finding"
            )

            regions.update([region])
            environments.update([env])
            teams.update([team])
            bucket_kinds.update([kind])
            criticality.update([criticality_value])
            findings.update([finding_type])
            total_size_gb += bucket_size_gb
            total_objects += object_count
            total_monthly_cost += monthly_cost
            total_yearly_savings += savings_yearly
            if versioning_enabled:
                versioned_buckets += 1
            if logging_enabled:
                logging_enabled_buckets += 1
            if inventory_enabled:
                inventory_enabled_buckets += 1
            if replication_enabled:
                replicated_buckets += 1
            if lifecycle_enabled:
                lifecycle_policy_buckets += 1
            if not account_id and resource.get("account_id"):
                account_id = resource.get("account_id")

            buckets.append(
                {
                    "inventory_id": len(buckets) + 1,
                    "resource_id": resource_id,
                    "resource_name": resource.get("resource_name") or resource_id,
                    "account_id": resource.get("account_id"),
                    "region": region,
                    "state": resource.get("state") or "available",
                    "creation_date": metadata.get("creation_date"),
                    "tags": tags,
                    "metadata": metadata,
                    "storage": {
                        "bucket_size_gb": round(bucket_size_gb, 2),
                        "object_count": object_count,
                        "estimated_monthly_cost": round(monthly_cost, 2),
                        "storage_class_mix": metadata.get("storage_class_mix") or {},
                    },
                    "posture": {
                        "versioning_enabled": versioning_enabled,
                        "logging_enabled": logging_enabled,
                        "inventory_enabled": inventory_enabled,
                        "replication_enabled": replication_enabled,
                        "lifecycle_enabled": lifecycle_enabled,
                    },
                    "maxops": {
                        "check_id": check_result.check_id,
                        "finding_type": finding_type,
                        "severity": metadata.get("severity"),
                        "title": metadata.get("title") or check_result.name,
                        "description": metadata.get("description")
                        or check_result.description,
                        "recommended_action": metadata.get("recommended_action"),
                        "potential_savings_monthly": round(savings_monthly, 2),
                        "potential_savings_yearly": round(savings_yearly, 2),
                        "status": "actionable",
                        "evidence": metadata.get("evidence") or {},
                        "current_config": metadata.get("current_config") or {},
                        "target_config": metadata.get("target_config") or {},
                        "metadata": metadata,
                    },
                    "aws_payload": metadata.get("aws_payload") or {},
                }
            )

    if not buckets:
        return _empty_s3_overview()

    total_buckets = len(buckets)
    return {
        "generated_at": latest_time.isoformat() if latest_time else None,
        "account_id": account_id,
        "summary": {
            "total_buckets": total_buckets,
            "actionable_buckets": total_buckets,
            "healthy_buckets": 0,
            "versioned_buckets": versioned_buckets,
            "logging_enabled_buckets": logging_enabled_buckets,
            "inventory_enabled_buckets": inventory_enabled_buckets,
            "replicated_buckets": replicated_buckets,
            "lifecycle_policy_buckets": lifecycle_policy_buckets,
            "total_size_gb": round(total_size_gb, 2),
            "total_objects": total_objects,
            "monthly_cost_estimate": round(total_monthly_cost, 2),
            "potential_savings_yearly": round(total_yearly_savings, 2),
        },
        "dimensions": {
            "regions": _counter_to_rows(regions),
            "environments": _counter_to_rows(environments),
            "teams": _counter_to_rows(teams),
            "bucket_kinds": _counter_to_rows(bucket_kinds),
            "criticality": _counter_to_rows(criticality),
        },
        "findings_breakdown": _counter_to_rows(findings),
        "posture_breakdown": {
            "versioning": {
                "enabled": versioned_buckets,
                "disabled": total_buckets - versioned_buckets,
            },
            "logging": {
                "enabled": logging_enabled_buckets,
                "disabled": total_buckets - logging_enabled_buckets,
            },
            "inventory": {
                "enabled": inventory_enabled_buckets,
                "disabled": total_buckets - inventory_enabled_buckets,
            },
            "replication": {
                "enabled": replicated_buckets,
                "disabled": total_buckets - replicated_buckets,
            },
            "lifecycle": {
                "enabled": lifecycle_policy_buckets,
                "disabled": total_buckets - lifecycle_policy_buckets,
            },
        },
        "buckets": buckets,
        "source": "latest_check_results",
    }


def _estimate_memory_utilization(item: Ec2Inventory, metadata: Dict[str, Any]) -> float:
    if item.state != "running":
        return 0.0

    cpu = float(metadata.get("avg_cpu_utilization") or 0.0)
    usage_profile = str(metadata.get("usage_profile") or "").lower()
    if usage_profile == "steady_prod":
        multiplier = 1.18
        offset = 8.0
    elif usage_profile == "bursty_api":
        multiplier = 1.12
        offset = 10.0
    elif usage_profile == "idle_nonprod":
        multiplier = 0.82
        offset = 4.5
    elif usage_profile == "stopped_unused":
        multiplier = 0.0
        offset = 0.0
    else:
        multiplier = 0.96
        offset = 7.0

    estimated = cpu * multiplier + offset
    return round(min(max(estimated, 0.0), 96.0), 2)


def _estimate_disk_footprint(
    item: Ec2Inventory, metadata: Dict[str, Any]
) -> tuple[float, float]:
    base_disk = 120.0
    if item.instance_type:
        family = item.instance_type.split(".", 1)[0]
        if family.startswith("r"):
            base_disk = 220.0
        elif family.startswith("m"):
            base_disk = 160.0
        elif family.startswith("c"):
            base_disk = 140.0
        elif family.startswith("t"):
            base_disk = 80.0

    state_factor = 0.38 if item.state == "stopped" else 0.62
    cpu_factor = float(metadata.get("avg_cpu_utilization") or 0.0) / 100.0
    used = round(base_disk * min(state_factor + cpu_factor * 0.35, 0.92), 2)
    available = round(max(base_disk - used, 0.0), 2)
    return used, available
