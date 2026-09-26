"""RDS risk-component aggregation."""
from __future__ import annotations

from typing import Any


RISK_ORDER = {"NOT_AVAILABLE": 0, "NOT_NEEDED": 0, "LOW": 0, "MEDIUM": 1, "HIGH": 2}


def ratio_risk(value: float | None, medium: float, high: float) -> str:
    if value is None or value >= high:
        return "HIGH"
    if value >= medium:
        return "MEDIUM"
    return "LOW"


def assess_risk(
    warnings: list[str], *, compute: str = "LOW", memory: str = "LOW",
    storage: str = "LOW", network: str = "LOW", db_load: str = "NOT_NEEDED",
) -> dict[str, Any]:
    telemetry_codes = {
        "OBSERVATION_WINDOW_TOO_SHORT", "OBSERVATION_COVERAGE_TOO_LOW",
        "RDS_CPU_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED",
        "RDS_MEMORY_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED",
    }
    telemetry = "HIGH" if any(code in telemetry_codes for code in warnings) else "LOW"
    operations = "HIGH" if any(code.endswith("REQUIRES_REVIEW") for code in warnings) else "LOW"
    compatibility = "HIGH" if any("UNKNOWN" in code for code in warnings) else "LOW"
    values = [telemetry, compute, memory, db_load, storage, network, compatibility, operations]
    return {
        "telemetry": telemetry, "compute": compute, "memory": memory,
        "db_load": db_load, "storage": storage, "network": network,
        "connections": "HIGH" if "CONNECTION_HEADROOM_REQUIRES_REVIEW" in warnings else "LOW",
        "compatibility": compatibility, "operations": operations,
        "overall": max(values, key=lambda value: RISK_ORDER[value]),
        "reason_codes": list(dict.fromkeys(warnings)),
    }
