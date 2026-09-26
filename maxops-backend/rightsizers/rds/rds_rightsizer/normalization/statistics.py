"""Exact validation, alignment, and window statistics for RDS metrics."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from rightsizers.common.statistics import (
    nearest_rank_percentile,
    nearest_rank_percentile_sorted,
)


LOWER_TAIL = {
    "freeable_memory_bytes",
    "free_storage_bytes",
    "cpu_credit_balance",
    "burst_balance_percent",
    "ebs_io_balance_percent",
    "ebs_byte_balance_percent",
}
PERCENT_METRICS = {
    "cpu_percent",
    "burst_balance_percent",
    "ebs_io_balance_percent",
    "ebs_byte_balance_percent",
}
NON_NEGATIVE_METRICS = {
    "freeable_memory_bytes",
    "swap_bytes",
    "connections",
    "read_iops",
    "write_iops",
    "read_throughput_bps",
    "write_throughput_bps",
    "read_latency_seconds",
    "write_latency_seconds",
    "disk_queue_depth",
    "free_storage_bytes",
    "network_rx_bps",
    "network_tx_bps",
    "cpu_credit_balance",
    "cpu_credit_usage",
    "replica_lag_seconds",
}


def percentile(values: Iterable[float], value: float) -> float | None:
    return nearest_rank_percentile(values, value)


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _valid(metric: str, value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if metric in PERCENT_METRICS and not 0 <= number <= 100:
        return None
    if metric in NON_NEGATIVE_METRICS and number < 0:
        return None
    return number


def _points(metric: str, payload: dict[str, Any]) -> tuple[dict[datetime, float], int]:
    result: dict[datetime, float] = {}
    invalid = 0
    timestamps = payload.get("timestamps") or []
    values = payload.get("values") or []
    for raw_timestamp, raw_value in zip(timestamps, values):
        timestamp = _timestamp(raw_timestamp)
        value = _valid(metric, raw_value)
        if timestamp is None or value is None:
            invalid += 1
            continue
        if metric in {"network_rx_bps", "network_tx_bps"}:
            value = value * 8 / 1_000_000
        result[timestamp] = value
    return result, invalid


def _summary(
    metric: str,
    points: dict[datetime, float],
    invalid: int,
    *,
    cutoff: datetime,
    end: datetime,
    period: int,
    resource_created_at: datetime | None,
    raw_count: int,
) -> dict[str, Any]:
    ordered = sorted(value for timestamp, value in points.items() if timestamp >= cutoff)
    if ordered:
        status = "PRESENT"
    elif raw_count:
        status = "INVALID"
    else:
        status = "EMPTY"
    expected_start = max(cutoff, resource_created_at) if resource_created_at else cutoff
    expected = max((end - expected_start).total_seconds(), 0) / period
    base: dict[str, Any] = {
        "status": status,
        "sample_count": len(ordered),
        "observed_days": len(ordered) * period / 86400,
        "coverage_ratio": min(len(ordered) / expected, 1.0) if expected else None,
        "invalid_sample_count": invalid,
        "first_timestamp": min(points).isoformat() if points else None,
        "last_timestamp": max(points).isoformat() if points else None,
    }
    if not ordered:
        fields = ("minimum", "p01", "p05", "average") if metric in LOWER_TAIL else (
            "average", "p50", "p95", "p99", "maximum"
        )
        base.update({field: None for field in fields})
        return base
    if metric in LOWER_TAIL:
        base.update(
            minimum=ordered[0],
            p01=nearest_rank_percentile_sorted(ordered, 1),
            p05=nearest_rank_percentile_sorted(ordered, 5),
            average=sum(ordered) / len(ordered),
        )
    else:
        base.update(
            average=sum(ordered) / len(ordered),
            p50=nearest_rank_percentile_sorted(ordered, 50),
            p95=nearest_rank_percentile_sorted(ordered, 95),
            p99=nearest_rank_percentile_sorted(ordered, 99),
            maximum=ordered[-1],
        )
    return base


def normalize_cloudwatch_metrics(
    raw: dict[str, Any],
    end: datetime,
    *,
    resource_created_at: datetime | None = None,
    not_applicable: set[str] | None = None,
) -> dict[str, Any]:
    """Normalize one complete CloudWatch collection into the persisted V1 shape."""
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    period = int(raw.get("period_seconds") or 300)
    metric_payloads = raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {}
    not_applicable = not_applicable or set()
    point_sets: dict[str, dict[datetime, float]] = {}
    invalid_counts: dict[str, int] = {}
    raw_counts: dict[str, int] = {}
    for metric, payload in metric_payloads.items():
        if not isinstance(payload, dict):
            continue
        points, invalid = _points(metric, payload)
        point_sets[metric] = points
        invalid_counts[metric] = invalid
        raw_counts[metric] = len(payload.get("values") or [])

    def combine(left: str, right: str, target: str) -> None:
        paired = set(point_sets.get(left, {})) & set(point_sets.get(right, {}))
        point_sets[target] = {
            timestamp: point_sets[left][timestamp] + point_sets[right][timestamp]
            for timestamp in paired
        }
        invalid_counts[target] = invalid_counts.get(left, 0) + invalid_counts.get(right, 0)
        raw_counts[target] = max(raw_counts.get(left, 0), raw_counts.get(right, 0))

    combine("read_iops", "write_iops", "total_iops")
    combine("read_throughput_bps", "write_throughput_bps", "total_throughput_bps")
    windows: dict[str, Any] = {}
    for days in (14, 30, 60):
        cutoff = end - timedelta(days=days)
        window: dict[str, Any] = {}
        for metric in sorted(point_sets):
            if metric in not_applicable:
                window[metric] = {"status": "NOT_APPLICABLE", "sample_count": 0}
                continue
            window[metric] = _summary(
                metric,
                point_sets[metric],
                invalid_counts.get(metric, 0),
                cutoff=cutoff,
                end=end,
                period=period,
                resource_created_at=resource_created_at,
                raw_count=raw_counts.get(metric, 0),
            )
        for target, left, right in (
            ("total_iops", "read_iops", "write_iops"),
            ("total_throughput_bps", "read_throughput_bps", "write_throughput_bps"),
        ):
            left_count = sum(1 for timestamp in point_sets.get(left, {}) if timestamp >= cutoff)
            right_count = sum(1 for timestamp in point_sets.get(right, {}) if timestamp >= cutoff)
            paired = window.get(target, {}).get("sample_count", 0)
            denominator = max(left_count, right_count)
            window.setdefault(target, {})["pairing_ratio"] = paired / denominator if denominator else None
        windows[f"{days}d"] = window

    def demand(metric: str, field: str) -> float | None:
        values = [
            windows[name].get(metric, {}).get(field)
            for name in ("14d", "30d", "60d")
        ]
        known = [float(value) for value in values if value is not None]
        return max(known) if known and field != "p01" else min(known) if known else None

    selected = {
        metric: demand(metric, "p01" if metric in LOWER_TAIL else "p99")
        for metric in point_sets
    }
    return {
        "generated_at": end.isoformat(),
        "period_seconds": period,
        "collection": {"status": "SUCCESS", "reason_code": None},
        "windows": windows,
        "selected_decision_values": selected,
        "performance_insights": {
            "status": "NOT_NEEDED",
            "requested_days": 7,
            "observed_days": None,
            "summaries": None,
            "wait_type_summary": None,
            "truncation": None,
        },
        "normalization_version": "rds-v1-exact",
    }
