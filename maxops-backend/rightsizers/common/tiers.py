"""Generic tier selection without product-specific output shaping."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any


def pick_tier_candidates(
    candidates: list[dict[str, Any]],
    tier_ratios: Iterable[tuple[str, float]],
    *,
    utilization_key: str = "projected_util",
    selection_key: Callable[[dict[str, Any]], Any],
) -> dict[str, dict[str, Any] | None]:
    return {
        name: min(
            (
                candidate
                for candidate in candidates
                if candidate.get(utilization_key) is not None
                and float(candidate[utilization_key]) <= ratio
            ),
            key=selection_key,
            default=None,
        )
        for name, ratio in tier_ratios
    }
