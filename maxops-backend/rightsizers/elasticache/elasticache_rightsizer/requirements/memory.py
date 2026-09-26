from typing import Any


def usable_memory_bytes(
    entry: Any,
    reserved_memory_percent: float = 0.25,
    reserved_memory_bytes: float | None = None,
) -> float | None:
    maximum = getattr(entry, "maxmemory_bytes", None)
    if maximum is None:
        return None
    if reserved_memory_bytes is not None:
        return max(float(maximum) - float(reserved_memory_bytes), 0.0)
    return float(maximum) * (1.0 - float(reserved_memory_percent))


def projected_memory_util(
    bytes_used_p99: float | None, target_usable: float | None
) -> float | None:
    if bytes_used_p99 is None or not target_usable:
        return None
    return float(bytes_used_p99) / float(target_usable) * 100.0
