"""Privacy-bounded Performance Insights load attribution."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from ..normalization.statistics import percentile


def _summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "maximum": max(values) if values else None,
        "sample_count": len(values),
    }


def normalize_performance_insights(raw: dict[str, Any]) -> dict[str, Any]:
    total: dict[str, float] = {}
    groups: dict[str, dict[str, float]] = {}
    for metric in raw.get("metric_list") or []:
        key = metric.get("Key") if isinstance(metric.get("Key"), dict) else {}
        dimensions = key.get("Dimensions") if isinstance(key.get("Dimensions"), dict) else {}
        group = str(
            dimensions.get("db.wait_event_type.name")
            or dimensions.get("db.wait_event_type")
            or ""
        )
        target = groups.setdefault(group, {}) if group else total
        for point in metric.get("DataPoints") or []:
            timestamp = point.get("Timestamp")
            value = point.get("Value")
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(number) or number < 0 or timestamp is None:
                continue
            key_timestamp = timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp)
            target[key_timestamp] = target.get(key_timestamp, 0.0) + number
    cpu_group = next((values for name, values in groups.items() if name.casefold() == "cpu"), {})
    cpu: list[float] = []
    non_cpu: list[float] = []
    unattributed: list[float] = []
    total_values: list[float] = []
    group_totals: dict[str, float] = {name: 0.0 for name in groups}
    for timestamp, total_value in sorted(total.items()):
        grouped = sum(values.get(timestamp, 0.0) for values in groups.values())
        cpu_value = cpu_group.get(timestamp, 0.0)
        total_values.append(total_value)
        cpu.append(cpu_value)
        non_cpu.append(max(grouped - cpu_value, 0.0))
        unattributed.append(max(total_value - grouped, 0.0))
        for name, values in groups.items():
            group_totals[name] += values.get(timestamp, 0.0)
    denominator = sum(total_values)
    wait_types = [
        {
            "name": name,
            "share": value / denominator if denominator else 0.0,
            "aas_p99": percentile(list(groups[name].values()), 99),
        }
        for name, value in sorted(group_totals.items(), key=lambda item: (-item[1], item[0]))
    ]
    period = int(raw.get("period_seconds") or 300)
    return {
        "status": "AVAILABLE",
        "required": True,
        "requested_days": 7,
        "observed_days": len(total_values) * period / 86400,
        "summaries": {
            "total_load": _summary(total_values),
            "cpu_load": _summary(cpu),
            "non_cpu_load": _summary(non_cpu),
            "unattributed_load": _summary(unattributed),
        },
        "wait_type_summary": wait_types,
        "truncation": {
            "group_limit": 25,
            "unattributed_share": sum(unattributed) / denominator if denominator else 0.0,
        },
    }
