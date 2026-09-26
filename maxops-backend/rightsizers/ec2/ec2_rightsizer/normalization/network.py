"""Network counter conversions with no capacity assumptions."""

from __future__ import annotations

from typing import Optional


def _validate_period(period_seconds: float) -> None:
    if period_seconds <= 0:
        raise ValueError("period_seconds must be positive")


def bytes_to_mbps(byte_sum: Optional[float], period_seconds: float) -> Optional[float]:
    """Convert a CloudWatch byte Sum to decimal megabits per second."""
    _validate_period(period_seconds)
    if byte_sum is None:
        return None
    return float(byte_sum) / period_seconds * 8.0 / 1_000_000.0


def packets_to_pps(
    packet_sum: Optional[float], period_seconds: float
) -> Optional[float]:
    """Convert a CloudWatch packet Sum to packets per second."""
    _validate_period(period_seconds)
    if packet_sum is None:
        return None
    return float(packet_sum) / period_seconds
