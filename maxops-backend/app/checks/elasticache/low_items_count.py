"""
ElastiCache Check - Low Item Count in Cache

Flags ElastiCache (Redis/Valkey/Memcached) resources that have very low item/key count
over a lookback window.

Metric (best-effort):
- CurrItems (commonly available for Memcached; some adapters also expose for Redis/Valkey)
If CurrItems is unavailable via adapter metrics, this check will skip to avoid false positives.

Assumptions:
- aws_adapter.get_resources("elasticache_replication_group", {}, region) OR
  aws_adapter.get_resources("elasticache_cluster", {}, region)
- aws_adapter.get_resource_utilization(resource_id, "<resource_type>", start, end) returns metrics dict
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason
from app.utils.math_utils import avg, roundf


def _extract_numbers(x: Any) -> List[float]:
    if x is None:
        return []
    if isinstance(x, dict) and "Datapoints" in x:
        return _extract_numbers(x.get("Datapoints"))
    if isinstance(x, (int, float)):
        return [float(x)]
    if isinstance(x, (list, tuple)):
        out: List[float] = []
        for item in x:
            if item is None:
                continue
            if isinstance(item, (int, float)):
                out.append(float(item))
                continue
            if isinstance(item, dict):
                for key in ("Average", "average", "Sum", "sum", "Value", "value", "Count", "count", "Maximum", "maximum"):
                    if key in item and item[key] is not None:
                        out.append(float(item[key]))
                        break
        return out
    return []


def _get_cache_resources(aws_adapter, region: Optional[str]) -> List[Tuple[str, Dict[str, Any]]]:
    """
    Return a list of (resource_type, resource_obj) across supported ElastiCache resource types.
    """
    out: List[Tuple[str, Dict[str, Any]]] = []
    for rt in ("elasticache_replication_group", "elasticache_cluster"):
        try:
            items = aws_adapter.get_resources(rt, {}, region)
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, dict):
                        out.append((rt, it))
        except Exception:
            continue
    return out


def _get_resource_name(r: Dict[str, Any]) -> Optional[str]:
    return r.get("resource_id") or r.get("id") or r.get("name")


def check_elasticache_low_item_count(
    aws_adapter,
    lookback_days: int = 7,
    low_item_threshold: int = 1_000,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    end = datetime.utcnow()
    start = end - timedelta(days=lookback_days)

    flagged: List[Dict[str, Any]] = []

    for rt, r in _get_cache_resources(aws_adapter, region):
        rid = _get_resource_name(r)
        if not rid:
            continue

        try:
            metrics = aws_adapter.get_resource_utilization(rid, rt, start, end)

            # Best-effort metric key for item/key count
            curr_items_raw = (
                metrics.get("CurrItems")
                or metrics.get("curritems")
                or metrics.get("curr_items")
                or metrics.get("KeyCount")          # some adapters expose redis key count this way
                or metrics.get("keycount")
                or metrics.get("key_count")
            )

            values = _extract_numbers(curr_items_raw)
            if not values:
                # Avoid false positives if the adapter doesn't expose item count metrics
                continue

            avg_items = avg(values)
            max_items = max(values) if values else 0.0

            if avg_items <= float(low_item_threshold) and max_items <= float(low_item_threshold):
                md = r.setdefault("metadata", {})
                md["recommended_action"] = "elasticache_downsize"
                md["recommended_actions"] = [
                    "elasticache_downsize",
                    "elasticache_delete",
                ]
                md["lookback_days"] = lookback_days
                md["low_item_threshold"] = int(low_item_threshold)
                md["avg_item_count"] = roundf(avg_items, 2)
                md["max_item_count"] = roundf(max_items, 2)
                md["check_reason"] = create_check_reason("low_usage", {
                    "resource": rt,
                    "id": rid,
                    "metric": "CurrItems/KeyCount",
                    "lookback_days": lookback_days,
                    "avg_item_count": roundf(avg_items, 2),
                    "threshold": int(low_item_threshold),
                })
                flagged.append(r)

        except Exception as e:
            print(f"Error checking low item count for {rt}:{rid}: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="elasticache_low_item_count",
    name="ElastiCache Low Item Count",
    description="Identifies ElastiCache caches with very low item/key count over a lookback window",
    resource_type="elasticache",
    check_function=check_elasticache_low_item_count,
    default_action="elasticache_downsize",
    parameters={
        "lookback_days": 7,
        "low_item_threshold": 1000,
        "region": None,
    },
))
