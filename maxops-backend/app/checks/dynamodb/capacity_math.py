"""Capacity metric helpers for DynamoDB checks."""

from __future__ import annotations

from typing import Any, Dict

from app.utils.math_utils import SeriesOrScalar, avg, p95, safe_div, to_float_list


DEFAULT_PERIOD_SECONDS = 3600


def _metadata_key(metrics: Dict[str, Any], metric_name: str, suffix: str, default: Any = None) -> Any:
    return (
        metrics.get(f"{metric_name}_{suffix}")
        or metrics.get(f"{metric_name.lower()}_{suffix}")
        or metrics.get(suffix)
        or default
    )


def consumed_capacity_series(
    metrics: Dict[str, Any],
    metric_name: str,
    values: SeriesOrScalar,
    default_period_seconds: int = DEFAULT_PERIOD_SECONDS,
) -> list[float]:
    """
    Convert consumed capacity datapoints to capacity units per second.

    CloudWatch DynamoDB Consumed*CapacityUnits should be compared to provisioned
    capacity as: consumed Sum / PERIOD(consumed), matching CloudWatch metric math.
    Older fixtures and some callers may already provide Average values, so only
    Sum datapoints are divided by the metric period.
    """
    stat = str(_metadata_key(metrics, metric_name, "statistic", "Average")).lower()
    period = float(_metadata_key(metrics, metric_name, "period_seconds", default_period_seconds) or default_period_seconds)
    datapoints = to_float_list(values)
    if stat == "sum" and period > 0:
        return [value / period for value in datapoints]
    return datapoints


def avg_consumed_capacity(
    metrics: Dict[str, Any],
    metric_name: str,
    values: SeriesOrScalar,
    default_period_seconds: int = DEFAULT_PERIOD_SECONDS,
) -> float:
    normalized = consumed_capacity_series(metrics, metric_name, values, default_period_seconds)
    return sum(normalized) / len(normalized) if normalized else 0.0


def p95_consumed_capacity(
    metrics: Dict[str, Any],
    metric_name: str,
    values: SeriesOrScalar,
    default_period_seconds: int = DEFAULT_PERIOD_SECONDS,
) -> float:
    return p95(consumed_capacity_series(metrics, metric_name, values, default_period_seconds))


def utilization_pct(consumed_per_second: float, provisioned_average: float) -> float:
    return safe_div(consumed_per_second, provisioned_average, default=0.0) * 100.0


def avg_provisioned_capacity(values: SeriesOrScalar) -> float:
    return avg(values)
