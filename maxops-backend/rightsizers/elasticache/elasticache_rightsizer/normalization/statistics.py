from __future__ import annotations

import math
from typing import Iterable


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def metric_summary(
    values: Iterable[float | int | None], period_seconds: int = 300
) -> dict[str, float | int | None]:
    clean = [
        float(value)
        for value in values
        if value is not None and math.isfinite(float(value))
    ]
    return {
        "p99": _percentile(clean, 0.99),
        "p95": _percentile(clean, 0.95),
        "max": max(clean) if clean else None,
        "avg": sum(clean) / len(clean) if clean else None,
        "sample_count": len(clean),
        "period_seconds": period_seconds,
    }
