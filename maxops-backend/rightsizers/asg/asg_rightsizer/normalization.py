"""Deterministic ASG telemetry normalization."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from rightsizers.common.constants import TARGET_INDEPENDENT_DEMAND_SCHEMA
from rightsizers.common.statistics import exact_ceil_division

from .models import ASGCapacityPolicy


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def series_points(
    payload: dict[str, Any], *, percentage: bool = False
) -> dict[datetime, float]:
    points: dict[datetime, float] = {}
    for raw_timestamp, raw_value in zip(
        payload.get("timestamps") or [], payload.get("values") or []
    ):
        timestamp = _timestamp(raw_timestamp)
        number = _finite(raw_value)
        if timestamp is None or number is None:
            continue
        if percentage and not 0 <= number <= 100:
            continue
        points[timestamp] = number
    return points


def _percentile(values: Iterable[float], percentile: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    index = max(0, math.ceil(percentile / 100.0 * len(ordered)) - 1)
    return ordered[index]


def _summary(values: Iterable[float], *, include_p50: bool = False) -> dict[str, Any]:
    materialized = list(values)
    result: dict[str, Any] = {
        "p95": _percentile(materialized, 95),
        "p99": _percentile(materialized, 99),
        "maximum": max(materialized) if materialized else None,
        "sample_count": len(materialized),
    }
    if include_p50:
        result["p50"] = _percentile(materialized, 50)
    return result


def _record_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(records, key=lambda item: (item["required"], item["timestamp"]))

    def selected(percentile: float) -> dict[str, Any] | None:
        if not ordered:
            return None
        index = max(0, math.ceil(percentile / 100.0 * len(ordered)) - 1)
        return dict(ordered[index])

    return {
        "p50": selected(50)["required"] if selected(50) else None,
        "p99": selected(99)["required"] if selected(99) else None,
        "maximum": max((item["required"] for item in records), default=None),
        "sample_count": len(records),
        "p50_record": selected(50),
        "p99_record": selected(99),
    }


def aggregate_stable_member_memory(
    payload: dict[str, Any], expected_instance_ids: Iterable[str]
) -> dict[str, Any] | None:
    expected = sorted(set(str(value) for value in expected_instance_ids))
    members = payload.get("members") or {}
    if not expected or sorted(members) != expected:
        return None
    sources = [members[instance_id].get("source") or {} for instance_id in expected]
    signatures = {
        (str(source.get("namespace") or ""), str(source.get("metric_name") or ""))
        for source in sources
    }
    if len(signatures) != 1 or ("", "") in signatures:
        return None
    points = {
        instance_id: series_points(members[instance_id], percentage=True)
        for instance_id in expected
    }
    common = set.intersection(*(set(values) for values in points.values()))
    if not common:
        return None
    namespace, metric_name = next(iter(signatures))
    return {
        "timestamps": [timestamp.isoformat() for timestamp in sorted(common)],
        "values": [
            sum(points[instance_id][timestamp] for instance_id in expected)
            / len(expected)
            for timestamp in sorted(common)
        ],
        "source": {
            "kind": "stable_member_aggregate",
            "namespace": namespace,
            "metric_name": metric_name,
            "instance_ids": expected,
            "member_sources": sources,
            "aggregation": "mean_complete_member_intersection",
        },
    }


def normalize_asg_metrics(
    raw: dict[str, Any],
    end: datetime,
    policy: ASGCapacityPolicy,
    *,
    signals: dict[str, Any] | None = None,
    include_demand: bool = False,
) -> (
    tuple[dict[str, Any], dict[str, Any]]
    | tuple[dict[str, Any], dict[str, Any], dict[str, Any]]
):
    end = _timestamp(end) or datetime.now(timezone.utc)
    metrics = raw.get("metrics") or {}
    cpu = series_points(metrics.get("cpu_percent") or {}, percentage=True)
    memory = series_points(metrics.get("memory_percent") or {}, percentage=True)
    desired = series_points(metrics.get("desired_capacity") or {})
    in_service = {
        timestamp: value
        for timestamp, value in series_points(
            metrics.get("in_service_instances") or {}
        ).items()
        if value >= 1
    }
    memory_source = (metrics.get("memory_percent") or {}).get("source")
    cpu_pairing = len(set(cpu) & set(in_service)) / len(cpu) if cpu else None
    memory_pairing = (
        len(set(memory) & set(in_service)) / len(memory) if memory else None
    )
    memory_observed_days = len(memory) * policy.period_seconds / 86400.0
    memory_decision_usable = bool(memory) and (
        (memory_pairing or 0) >= policy.minimum_pairing_ratio
        and memory_observed_days >= policy.minimum_observed_days
    )
    cpu_paired_all = sorted(set(cpu) & set(in_service))
    memory_paired_all = sorted(set(memory) & set(in_service))
    cpu_used_all = {
        timestamp: in_service[timestamp] * cpu[timestamp] / 100.0
        for timestamp in cpu_paired_all
    }
    memory_used_all = {
        timestamp: in_service[timestamp] * memory[timestamp] / 100.0
        for timestamp in memory_paired_all
    }
    windows: dict[str, Any] = {}
    for days in policy.lookback_days:
        cutoff = end - timedelta(days=days)
        cpu_window = {key: value for key, value in cpu.items() if key >= cutoff}
        memory_window = {key: value for key, value in memory.items() if key >= cutoff}
        desired_window = {key: value for key, value in desired.items() if key >= cutoff}
        service_window = {
            key: value for key, value in in_service.items() if key >= cutoff
        }
        cpu_paired = [timestamp for timestamp in cpu_paired_all if timestamp >= cutoff]
        memory_paired = [
            timestamp for timestamp in memory_paired_all if timestamp >= cutoff
        ]
        cpu_used = {timestamp: cpu_used_all[timestamp] for timestamp in cpu_paired}
        memory_used = {
            timestamp: memory_used_all[timestamp] for timestamp in memory_paired
        }
        required: dict[str, Any] = {}
        # CPU remains required at the series level. Once memory is usable, use
        # the union of independently capacity-paired timestamps so a gap in one
        # utilization series cannot erase a spike observed in the other.
        decision_timestamps = (
            sorted(set(cpu_paired) | set(memory_paired))
            if memory_decision_usable
            else cpu_paired
        )
        for tier, ratio in policy.tier_ratios:
            records: list[dict[str, Any]] = []
            for timestamp in decision_timestamps:
                cpu_value = cpu_used.get(timestamp)
                memory_value = (
                    memory_used.get(timestamp) if memory_decision_usable else None
                )
                measured_values = [
                    value for value in (cpu_value, memory_value) if value is not None
                ]
                if not measured_values:
                    continue
                binding_value = max(measured_values)
                if memory_value is None or (
                    cpu_value is not None and cpu_value > memory_value
                ):
                    binding = "cpu"
                elif cpu_value is None or memory_value > cpu_value:
                    binding = "memory"
                else:
                    binding = "cpu_and_memory"
                records.append(
                    {
                        "timestamp": timestamp.isoformat(),
                        "required": exact_ceil_division(binding_value, ratio),
                        "binding_dimension": binding,
                        "cpu_used_instances": cpu_value,
                        "memory_used_instances": memory_value,
                    }
                )
            required[tier] = _record_summary(records)
        windows[f"{days}d"] = {
            "lookback_days": days,
            "period_seconds": policy.period_seconds,
            "normalized": {
                "cpu_percent": _summary(cpu_window.values()),
                "memory_percent": _summary(memory_window.values()),
                "desired_capacity": _summary(desired_window.values(), include_p50=True),
                "in_service_instances": _summary(
                    service_window.values(), include_p50=True
                ),
                "used_capacity": {
                    "cpu": _summary(cpu_used.values(), include_p50=True),
                    "memory": _summary(memory_used.values(), include_p50=True),
                },
                "required_capacity": required,
            },
            "coverage": {
                "cpu_in_service_pairing_ratio": (
                    len(cpu_paired) / len(cpu_window) if cpu_window else 0.0
                ),
                "memory_in_service_pairing_ratio": (
                    len(memory_paired) / len(memory_window) if memory_window else None
                ),
            },
            "signals": dict(signals or {}),
            "normalization_version": "asg-v1-exact",
        }

    def disclosure(name: str, points: dict[datetime, float]) -> dict[str, Any]:
        observed_days = len(points) * policy.period_seconds / 86400.0
        return {
            "present": bool(points),
            "observed_days": observed_days,
            "thin_data": observed_days < policy.minimum_observed_days,
        }

    telemetry = {
        "cpu_percent": {**disclosure("cpu_percent", cpu), "pairing_ratio": cpu_pairing},
        "memory_percent": {
            **disclosure("memory_percent", memory),
            "pairing_ratio": memory_pairing,
            "source": memory_source,
            "status": (
                "insufficient_pairing"
                if memory and (memory_pairing or 0) < policy.minimum_pairing_ratio
                else "usable"
                if memory
                else "unavailable"
            ),
        },
        "desired_capacity": disclosure("desired_capacity", desired),
        "in_service_instances": disclosure("in_service_instances", in_service),
    }
    if not include_demand:
        return windows, telemetry

    start = end - timedelta(days=max(policy.lookback_days))
    demand_timestamps = (
        sorted(set(cpu_paired_all) | set(memory_paired_all))
        if memory_decision_usable
        else cpu_paired_all
    )
    demand = {
        "normalization_version": TARGET_INDEPENDENT_DEMAND_SCHEMA,
        "period_seconds": policy.period_seconds,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "points": [
            {
                "timestamp": timestamp.isoformat(),
                "cpu_used_instance_equivalents": cpu_used_all.get(timestamp),
                "memory_used_instance_equivalents": (
                    memory_used_all.get(timestamp) if memory_decision_usable else None
                ),
            }
            for timestamp in demand_timestamps
            if timestamp >= start
        ],
    }
    return windows, telemetry, demand
