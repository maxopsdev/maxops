"""
CloudWatch Logs Check - Log Groups With No Recent Ingestion

Flags CloudWatch Log Groups that have had no ingestion over a lookback window.

Signals:
- CloudWatch Logs metric: IncomingBytes (per log group) OR IncomingLogEvents
- If total incoming bytes == 0 (and/or total incoming events == 0) over the lookback,
  we consider it "no recent ingestion".

Why it matters:
- Stale log groups often still retain historical data and can incur storage cost,
  especially if retention is not set.

Assumptions:
- aws_adapter.get_resources("cloudwatch_log_group", {}, region) returns log groups with `resource_id` or `name`
- aws_adapter.get_resource_utilization(log_group_name, "cloudwatch_log_group", start, end) returns metrics dict.
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


def check_cloudwatch_log_groups_no_recent_ingestion(
    aws_adapter,
    lookback_days: int = 14,
    # Some adapters can return very small floats; treat <= epsilon as zero.
    epsilon: float = 0.0,
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
                default=None,
            )
            incoming_events = _metric(
                u,
                "IncomingLogEvents",
                "incominglogevents",
                "incoming_log_events",
                default=None,
            )

            bytes_vals = _extract_numbers(incoming_bytes)
            events_vals = _extract_numbers(incoming_events)

            total_bytes = sum(bytes_vals) if bytes_vals else (float(incoming_bytes) if isinstance(incoming_bytes, (int, float)) else 0.0)
            total_events = sum(events_vals) if events_vals else (float(incoming_events) if isinstance(incoming_events, (int, float)) else 0.0)

            # If both metrics are available, require both to be "zero".
            # If only one is available, evaluate that one.
            has_bytes_metric = incoming_bytes is not None
            has_events_metric = incoming_events is not None

            no_bytes = (total_bytes <= epsilon) if has_bytes_metric else True
            no_events = (total_events <= epsilon) if has_events_metric else True

            no_recent_ingestion = no_bytes and no_events

            if no_recent_ingestion:
                md = lg.setdefault("metadata", {})
                md["recommended_action"] = "review"
                md["lookback_days"] = lookback_days
                md["total_incoming_bytes"] = roundf(total_bytes, 2)
                md["total_incoming_log_events"] = roundf(total_events, 2)
                md["check_reason"] = create_check_reason("stale", {
                    "resource": "cloudwatch_log_group",
                    "issue": "no_recent_ingestion",
                    "log_group": log_group_name,
                    "lookback_days": lookback_days,
                    "total_incoming_bytes": roundf(total_bytes, 2),
                    "total_incoming_log_events": roundf(total_events, 2),
                })
                flagged.append(lg)

        except Exception as e:
            print(f"Error checking log group recent ingestion for {log_group_name}: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="cloudwatch_log_groups_no_recent_ingestion",
    name="CloudWatch Log Groups With No Recent Ingestion",
    description="Identifies CloudWatch Log Groups with no recent ingestion over a lookback window",
    resource_type="cloudwatch_log_group",
    check_function=check_cloudwatch_log_groups_no_recent_ingestion,
    default_action="review",
    parameters={
        "lookback_days": 14,
        "epsilon": 0.0,
        "region": None,
    },
))
