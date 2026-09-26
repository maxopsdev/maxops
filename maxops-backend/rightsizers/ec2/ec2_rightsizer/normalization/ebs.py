"""EBS counter conversions and timestamp-aligned volume aggregation."""

from __future__ import annotations

from typing import Optional


def _validate_period(period_seconds: float) -> None:
    if period_seconds <= 0:
        raise ValueError("period_seconds must be positive")


def operations_to_iops(
    operation_sum: Optional[float], period_seconds: float
) -> Optional[float]:
    _validate_period(period_seconds)
    if operation_sum is None:
        return None
    return float(operation_sum) / period_seconds


def bytes_to_mibps(byte_sum: Optional[float], period_seconds: float) -> Optional[float]:
    _validate_period(period_seconds)
    if byte_sum is None:
        return None
    return float(byte_sum) / period_seconds / 1_048_576.0
