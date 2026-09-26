"""Deterministic statistics shared by rightsizers."""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, localcontext
import math
from typing import Iterable, Optional, Sequence, TypeVar


T = TypeVar("T")


def exact_ceil_division(
    numerator: float | int | Decimal,
    *denominator_factors: float | int | Decimal,
) -> int:
    """Ceil a quotient without binary-float noise at inclusive boundaries."""
    if not denominator_factors:
        raise ValueError("at least one denominator factor is required")
    denominator = Decimal("1")
    for factor in denominator_factors:
        decimal_factor = Decimal(str(factor))
        if decimal_factor <= 0:
            raise ValueError("denominator factors must be positive")
        denominator *= decimal_factor
    with localcontext() as context:
        context.prec = 50
        quotient = Decimal(str(numerator)) / denominator
    return int(quotient.to_integral_value(rounding=ROUND_CEILING))


def nearest_rank_percentile(
    values: Iterable[Optional[float]], percentile: float
) -> Optional[float]:
    if not 0 < percentile <= 100:
        raise ValueError("percentile must be in (0, 100]")
    ordered = sorted(float(value) for value in values if value is not None)
    if not ordered:
        return None
    return nearest_rank_percentile_sorted(ordered, percentile)


def nearest_rank_percentile_sorted(
    ordered: Sequence[T], percentile: float
) -> Optional[T]:
    """Nearest-rank item from an already sorted, non-empty-capable sequence."""
    if not 0 < percentile <= 100:
        raise ValueError("percentile must be in (0, 100]")
    if not ordered:
        return None
    return ordered[max(0, math.ceil(percentile / 100.0 * len(ordered)) - 1)]
