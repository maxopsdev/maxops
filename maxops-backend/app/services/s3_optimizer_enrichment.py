"""Scan-time bridge from AWS/CUR signals to persisted S3 optimizer results."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import logging
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from sqlalchemy.orm import Session

from app.pricing.s3_price_map import resolve
from app.services.s3_bucket_source import (
    coverage_by_bucket,
    derive_signals,
    load_bucket_rows,
    normalize_bucket_name,
)
from app.services.s3_optimizer import evaluate_bucket
from app.services.s3_optimizer_history import build_monthly_history


logger = logging.getLogger("uvicorn.error")
DEFAULT_WINDOW_DAYS = 90
OPTIMIZER_VERSION = "v1"
OPTIMIZER_METADATA_KEYS = (
    "s3_optimizer",
    "storage_class_breakdown",
    "lifecycle_transitions",
    "intelligent_tiering_config",
    "s3_optimizer_version",
)


def _window(params: Optional[Mapping[str, Any]]) -> tuple[str, str, int]:
    """Return the inclusive scan window inputs used by all S3 buckets."""

    values = params or {}
    days = int(values.get("window_days", DEFAULT_WINDOW_DAYS))
    if days <= 0:
        raise ValueError("window_days must be positive")
    end = str(values.get("end_date") or date.today().isoformat())
    start = str(values.get("start_date") or (date.fromisoformat(end) - timedelta(days=days)).isoformat())
    return start, end, days


def _resource_metadata(resource: Mapping[str, Any]) -> dict[str, Any]:
    """Return a mutable resource metadata dictionary."""

    metadata = resource.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _bucket_rows(rows: Iterable[Any], bucket: str) -> list[Any]:
    """Select one normalized bucket's rows without changing unknown values."""

    wanted = normalize_bucket_name(bucket)
    selected = []
    for row in rows:
        row_bucket = row.get("bucket", row.get("line_item_resource_id")) if isinstance(row, Mapping) else getattr(row, "bucket", None)
        if normalize_bucket_name(row_bucket) == wanted:
            selected.append(row)
    return selected


def _parse_lifecycle_rule(rule: Mapping[str, Any]) -> dict[str, Any]:
    """Parse one AWS lifecycle rule into the stable optimizer metadata shape."""

    filter_value = rule.get("Filter")
    if not isinstance(filter_value, Mapping):
        filter_value = {}
    prefix_filter = filter_value.get("Prefix") or rule.get("Prefix")
    tag_filter: dict[str, str] = {}
    object_size_filters: dict[str, Any] = {}
    tag = filter_value.get("Tag")
    if isinstance(tag, Mapping) and tag.get("Key") is not None:
        tag_filter[str(tag["Key"])] = str(tag.get("Value", ""))
    and_filter = filter_value.get("And")
    if isinstance(and_filter, Mapping):
        prefix_filter = and_filter.get("Prefix", prefix_filter)
        for item in and_filter.get("Tags", []) or []:
            if isinstance(item, Mapping) and item.get("Key") is not None:
                tag_filter[str(item["Key"])] = str(item.get("Value", ""))
        for name in ("ObjectSizeGreaterThan", "ObjectSizeLessThan"):
            if name in and_filter:
                object_size_filters[name] = and_filter[name]
    for name in ("ObjectSizeGreaterThan", "ObjectSizeLessThan"):
        if name in filter_value:
            object_size_filters[name] = filter_value[name]
    transitions = []
    for transition in rule.get("Transitions", []) or []:
        if not isinstance(transition, Mapping):
            continue
        transitions.append(
            {
                "to_class": transition.get("StorageClass"),
                "days": transition.get("Days"),
            }
        )
    return {
        "rule_id": rule.get("ID"),
        "status": rule.get("Status"),
        "prefix_filter": prefix_filter,
        "tag_filter": tag_filter,
        "object_size_filters": object_size_filters,
        "transitions": transitions,
    }


def _lifecycle_metadata(typed: Mapping[str, Any]) -> Optional[list[dict[str, Any]]]:
    """Convert typed lifecycle output, preserving unknown failures as ``None``."""

    status = typed.get("status")
    if status == "absent":
        return []
    if status != "usable":
        return None
    return [_parse_lifecycle_rule(rule) for rule in typed.get("rules", []) or []]


def _versioning_status(adapter: Any, bucket: str, metadata: Mapping[str, Any], region: str) -> Optional[str]:
    """Read versioning when available; keep missing/failed reads unknown."""

    existing = metadata.get("versioning_status")
    if existing is not None:
        return existing
    getter = getattr(adapter, "get_s3_bucket_versioning", None)
    if not callable(getter):
        return None
    try:
        try:
            response = getter(bucket, region=region)
        except TypeError:
            response = getter(bucket)
        return response.get("Status") if isinstance(response, Mapping) else None
    except Exception as exc:
        logger.warning("S3 versioning enrichment failed for %s: %s", bucket, exc)
        return None


def _unknown_result(
    resource: Mapping[str, Any],
    inventory_meta: Mapping[str, Any],
    price_map: Mapping[str, Any],
    params: Mapping[str, Any],
    error: str,
) -> dict[str, Any]:
    """Build a persisted unknown result after any bucket-level failure."""

    result = evaluate_bucket(
        {
            "resource_id": resource.get("resource_id"),
            "telemetry_status": "unknown",
            "confidence": "low",
            "cur_days_covered": 0,
            "unmapped_usage_types": [],
        },
        price_map,
        {"metrics": {}},
        inventory_meta,
        params,
    )
    result["telemetry_status"] = "unknown"
    result["error"] = str(error)
    return result


def _set_optimizer_metadata(
    resource: dict[str, Any],
    result: dict[str, Any],
    lifecycle: Optional[list[dict[str, Any]]],
    intelligent_tiering: Optional[list[dict[str, Any]]],
    price_map: Mapping[str, Any],
) -> None:
    """Attach additive optimizer metadata to an in-memory bucket resource."""

    metadata = _resource_metadata(resource)
    breakdown = result.get("current", {}).get("storage_class_breakdown")
    metadata["s3_optimizer"] = result
    metadata["storage_class_breakdown"] = breakdown
    metadata["lifecycle_transitions"] = lifecycle
    metadata["intelligent_tiering_config"] = intelligent_tiering
    metadata["s3_optimizer_version"] = {
        "price_map_window": price_map.get("price_map_window"),
        "seed_as_of": price_map.get("seed_as_of"),
        "engine": OPTIMIZER_VERSION,
    }
    resource["metadata"] = metadata


def _overlay_metadata(
    adapter: Any,
    resource_id: str,
    resource: Mapping[str, Any],
) -> None:
    """Bridge the four persisted optimizer fields into the scan adapter cache."""

    overlay = getattr(adapter, "overlay_resource_metadata", None)
    if not callable(overlay):
        return
    metadata = resource.get("metadata") or {}
    for key in (
        "s3_optimizer",
        "storage_class_breakdown",
        "lifecycle_transitions",
        "intelligent_tiering_config",
    ):
        try:
            overlay("s3", resource_id, key, metadata.get(key))
        except Exception:
            # Metadata propagation is best effort; a cache adapter failure
            # must not prevent the bucket from receiving an unknown result.
            logger.exception("S3 optimizer cache overlay failed for %s", resource_id)


def _bucket_metadata(resource: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached copy of all five persisted optimizer metadata keys."""

    metadata = resource.get("metadata")
    if not isinstance(metadata, Mapping):
        return {}
    return {key: deepcopy(metadata.get(key)) for key in OPTIMIZER_METADATA_KEYS}


def _finish_enrichment(
    db: Session,
    price_map: dict[str, Any],
    metadata_by_bucket: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Flush once and return the price map plus first-scan metadata."""

    try:
        db.flush()
    except Exception:
        # Keep the enrichment fail-soft while giving the scan's owning
        # transaction a chance to flush inventory rows later.
        logger.exception("S3 optimizer enrichment flush failed")
    price_map["metadata_by_bucket"] = metadata_by_bucket
    return price_map


def enrich_s3_buckets(
    db: Session,
    adapter: Any,
    buckets: list[dict[str, Any]],
    region: str,
    cache_root: Path | str,
    params: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Evaluate and bridge every S3 bucket in one region.

    CUR is loaded and resolved once for the region.  Each bucket remains
    fail-soft: an AWS or engine exception becomes an ``unknown`` result with
    an ``error`` field, and later buckets continue.  A CUR load failure is
    shared by every bucket because none can be evaluated safely without the
    common coverage and price inputs.

    The return value is the resolved price map so the caller can pass it to
    the existing sessionless S3 storage-cost estimator; callers may ignore it.
    """

    values = dict(params or {})
    start, end, days = _window(values)
    price_map: dict[str, Any] = {
        "resolved": {},
        "unresolved": [],
        "price_map_window": {"start": start, "end": end},
        "seed_as_of": None,
    }
    metadata_by_bucket: dict[str, dict[str, Any]] = {}
    try:
        rows = list(load_bucket_rows(Path(cache_root), start, end, "daily"))
        coverage = coverage_by_bucket(rows, start, end)
        price_map = resolve(rows, region, {"start": start, "end": end})
        try:
            history_start = (date.fromisoformat(end) - timedelta(days=365)).isoformat()
            history_rows = list(load_bucket_rows(Path(cache_root), history_start, end, "monthly"))
            histories = build_monthly_history(history_rows)
        except Exception as history_exc:
            logger.warning("S3 optimizer monthly history unavailable in %s: %s", region, history_exc)
            histories = {}
    except Exception as exc:
        logger.exception("S3 optimizer CUR enrichment failed in %s", region)
        for resource in buckets:
            bucket = str(resource.get("resource_id") or "")
            metadata = _resource_metadata(resource)
            inventory_meta = {
                "resource_id": bucket,
                "account_id": resource.get("account_id"),
                "region": region,
                "versioning_status": metadata.get("versioning_status"),
            }
            result = _unknown_result(resource, inventory_meta, price_map, values, str(exc))
            _set_optimizer_metadata(resource, result, None, None, price_map)
            _overlay_metadata(adapter, bucket, resource)
            metadata_by_bucket[bucket] = _bucket_metadata(resource)
        return _finish_enrichment(db, price_map, metadata_by_bucket)

    try:
        discovered = adapter.discover_storage_types(region)
    except Exception as exc:
        logger.exception("S3 optimizer CloudWatch discovery failed in %s", region)
        discovered = {}
        discovery_error = str(exc)
    else:
        discovery_error = None
    for resource in buckets:
        bucket = str(resource.get("resource_id") or "")
        metadata = _resource_metadata(resource)
        inventory_meta = {
            "resource_id": bucket,
            "account_id": resource.get("account_id"),
            "region": region,
            "versioning_status": _versioning_status(adapter, bucket, metadata, region),
        }
        lifecycle: Optional[list[dict[str, Any]]] = None
        intelligent_tiering: Optional[list[dict[str, Any]]] = None
        try:
            if discovery_error:
                raise RuntimeError(discovery_error)
            try:
                cloudwatch = adapter.fetch_daily_storage_metrics(
                    bucket,
                    discovered.get(bucket, set()),
                    days=days,
                    region=region,
                )
            except TypeError:
                cloudwatch = adapter.fetch_daily_storage_metrics(
                    bucket,
                    discovered.get(bucket, set()),
                    days=days,
                )
            try:
                lifecycle_typed = adapter.get_s3_bucket_lifecycle_typed(bucket, region=region)
            except TypeError:
                lifecycle_typed = adapter.get_s3_bucket_lifecycle_typed(bucket)
            lifecycle = _lifecycle_metadata(lifecycle_typed)
            try:
                intelligent_tiering_typed = adapter.list_s3_bucket_intelligent_tiering_configurations(
                    bucket, region=region
                )
            except TypeError:
                intelligent_tiering_typed = adapter.list_s3_bucket_intelligent_tiering_configurations(bucket)
            intelligent_tiering = (
                intelligent_tiering_typed.get("configurations")
                if intelligent_tiering_typed.get("status") == "usable"
                else None
            )
            bucket_rows = _bucket_rows(rows, bucket)
            signals = derive_signals(bucket_rows, coverage.get(bucket, {"covered_days": 0}))
            signals["resource_id"] = bucket
            if bucket in histories:
                signals["history"] = histories[bucket]
            result = evaluate_bucket(
                signals,
                price_map,
                cloudwatch,
                inventory_meta,
                values,
            )
            _set_optimizer_metadata(resource, result, lifecycle, intelligent_tiering, price_map)
            _overlay_metadata(adapter, bucket, resource)
            metadata_by_bucket[bucket] = _bucket_metadata(resource)
        except Exception as exc:
            logger.exception("S3 optimizer enrichment failed for %s", bucket)
            result = _unknown_result(resource, inventory_meta, price_map, values, str(exc))
            _set_optimizer_metadata(resource, result, lifecycle, intelligent_tiering, price_map)
            _overlay_metadata(adapter, bucket, resource)
            metadata_by_bucket[bucket] = _bucket_metadata(resource)
    return _finish_enrichment(db, price_map, metadata_by_bucket)
