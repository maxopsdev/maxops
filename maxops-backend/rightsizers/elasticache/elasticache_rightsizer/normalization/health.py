def counter_sum_to_rate(value: float | None, period_seconds: int) -> float | None:
    if value is None:
        return None
    if period_seconds <= 0:
        raise ValueError("period_seconds must be positive")
    return float(value) / period_seconds
