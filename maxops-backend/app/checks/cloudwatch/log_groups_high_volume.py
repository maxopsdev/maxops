"""
CloudWatch Logs Check - Log Groups With Very High Ingest Volume (Bytes)

Flags log groups whose ingested bytes are very high over a lookback window.

Signals:
- CloudWatch Logs metric: IncomingBytes (per log group)
- We evaluate SUM over the lookback window (or sum of returned datapoints)

Assumptions:
- aws_adapter.get_resources("cloudwatch_log_group", {}, region) returns log groups with `resource_id` or `name`
- aws_adapter.get_resource_utilization(log_group_name, "cloudwatch_log_group", start, end) returns a dict of metrics
  where "IncomingBytes" may be a scalar, list of numbers, list of datapoints dicts, or a dict with "Datapoints".
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason
from app.utils.math_utils import roundf


def _metric(u: Dict[str, Any], *keys: str, default: Any = 0) -> Any:
    for k in keys:
        if k in u and u[k] is not None:
            return u[k]
    return default


def _extract_numbers(x: Any) -> List[float]:
    """
    Normalize common CloudWatch/adapter shapes to list[float]:
    - scalar number -> [number]
    - list[number] -> list
    - list[dict] -> pulls Sum/sum/Value/value/Count/count
    - dict with Datapoints -> recurse into Datapoints
    """
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
                for key in ("Sum", "sum", "Value", "value", "Count", "count"):
                    if key in item and item[key] is not None:
                        out.append(float(item[key]))
                        break
        return out
    return []


def check_cloudwatch_log_groups_high_ingest_bytes(
    aws_adapter,
    lookback_days: int = 7,
    high_ingest_bytes_threshold: int = 50_000_000_000,  # 50 GB over lookback window
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    log_groups = aws_adapter.get_resources("cloudwatch_log_group", {}, region)

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=lookback_days)

    flagged: List[Dict[str, Any]] = []

    for lg in log_groups:
        log_group_name = lg.get("resource_id") or lg.get("name")
        if not log_group_name:
            continue

        try:
            u = aws_adapter.get_resource_utilization(log_group_name, "cloudwatch_log_group", start_date, end_date)

            incoming_bytes = _metric(
                u,
                "IncomingBytes",
                "incomingbytes",
                "incoming_bytes",
                default=0,
            )

            vals = _extract_numbers(incoming_bytes)
            total_bytes = sum(vals) if vals else float(incoming_bytes or 0)

            if total_bytes >= float(high_ingest_bytes_threshold):
                md = lg.setdefault("metadata", {})
                md["recommended_action"] = "review"
                md["lookback_days"] = lookback_days
                md["total_incoming_bytes"] = roundf(total_bytes, 2)
                md["high_ingest_bytes_threshold"] = int(high_ingest_bytes_threshold)
                md["check_reason"] = create_check_reason("high_volume", {
                    "resource": "cloudwatch_log_group",
                    "metric": "IncomingBytes",
                    "log_group": log_group_name,
                    "lookback_days": lookback_days,
                    "total_incoming_bytes": roundf(total_bytes, 2),
                    "threshold": int(high_ingest_bytes_threshold),
                })
                flagged.append(lg)

        except Exception as e:
            print(f"Error checking log group ingest bytes for {log_group_name}: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="cloudwatch_log_groups_high_ingest_bytes",
    name="CloudWatch Log Groups High Ingest (Bytes)",
    description="Identifies CloudWatch Log Groups with very high ingested bytes over a lookback window",
    resource_type="cloudwatch_log_group",
    check_function=check_cloudwatch_log_groups_high_ingest_bytes,
    default_action="review",
    parameters={
        "lookback_days": 7,
        "high_ingest_bytes_threshold": 50_000_000_000,
        "region": None,
    },
))
