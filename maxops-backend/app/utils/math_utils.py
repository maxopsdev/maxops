"""
Common math helpers for checks across services.

Goal: keep check implementations clean and consistent when dealing with:
- scalar vs time-series values
- percentile calculations
- safe division
- rounding / formatting
"""

from __future__ import annotations

from typing import Optional, Sequence, Union, List

Number = Union[int, float]
SeriesOrScalar = Union[Number, Sequence[Optional[Number]], None]


def to_float_list(values: SeriesOrScalar) -> List[float]:
    """Normalize scalar/list/tuple/None into a list[float] with Nones removed."""
    if values is None:
        return []
    if isinstance(values, (list, tuple)):
        return [float(v) for v in values if v is not None]
    return [float(values)]


def avg(values: SeriesOrScalar) -> float:
    """Average of a series or scalar; returns 0.0 for empty."""
    vals = to_float_list(values)
    return sum(vals) / len(vals) if vals else 0.0


def percentile(values: SeriesOrScalar, p: float) -> float:
    """
    Percentile for a series (or scalar).
    - p should be in [0, 100]
    - Uses nearest-rank on sorted values (simple, stable, dependency-free).
    Returns 0.0 for empty.
    """
    vals = to_float_list(values)
    if not vals:
        return 0.0
    vals.sort()
    p = max(0.0, min(100.0, float(p)))
    idx = int(round((p / 100.0) * (len(vals) - 1)))
    return float(vals[idx])


def p95(values: SeriesOrScalar) -> float:
    """Convenience wrapper for 95th percentile."""
    return percentile(values, 95.0)


def safe_div(numerator: Optional[Number], denominator: Optional[Number], default: float = 0.0) -> float:
    """Safe division with default if denom is 0/None."""
    if denominator in (None, 0):
        return float(default)
    if numerator is None:
        return float(default)
    return float(numerator) / float(denominator)


def roundf(value: Optional[Number], ndigits: int = 2) -> Optional[float]:
    """Round a numeric value; preserves None."""
    if value is None:
        return None
    return round(float(value), ndigits)
