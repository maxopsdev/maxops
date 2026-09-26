"""
EMR Check - Oversized Core/Task Nodes (Low Utilization)

Flags EMR clusters whose core/task node utilization appears low over a lookback window.

Because EMR exposes metrics via CloudWatch (and sometimes via YARN metrics),
this check relies on aws_adapter.get_resource_utilization(cluster_id, "emr_cluster", start, end)
returning average CPU/memory metrics for the cluster (or for core/task groups if your adapter supports that).

Signals (best-effort keys):
- cpuutilization / CPUUtilization
- memoryutilization / MemoryUtilization (optional; may not exist)
- yarn_memory_available_pct / yarn_memory_used_pct (optional)

Logic:
- Flag if avg CPU < cpu_threshold AND (if memory metric available, avg memory < memory_threshold)

Default action: "review" (rightsizing needs context).

Assumptions:
- aws_adapter.get_resources("emr_cluster", {"state": [...]}, region) lists clusters
- aws_adapter.get_resource_utilization(cluster_id, "emr_cluster", start, end) returns metric dict
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason
from app.utils.math_utils import avg, roundf


def _get_cluster_id(c: Dict[str, Any]) -> Optional[str]:
    return c.get("resource_id") or c.get("id") or (c.get("metadata") or {}).get("Id")


def _metric(u: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in u and u[k] is not None:
            return u[k]
    return default


def check_emr_oversized_core_task_nodes(
    aws_adapter,
    lookback_hours: int = 6,
    cpu_threshold: float = 20.0,
    memory_threshold: float = 30.0,
    require_memory_metric: bool = False,
    include_states: Optional[List[str]] = None,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Flags clusters with low CPU (and optionally low memory) utilization.

    Returns:
        List of clusters flagged.
    """
    states = include_states or ["RUNNING", "WAITING"]
    clusters = aws_adapter.get_resources("emr_cluster", {"state": states}, region)

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(hours=lookback_hours)

    flagged: List[Dict[str, Any]] = []

    for c in clusters:
        cluster_id = _get_cluster_id(c)
        if not cluster_id:
            continue

        try:
            u = aws_adapter.get_resource_utilization(cluster_id, "emr_cluster", start_date, end_date)

            cpu = _metric(u, "CPUUtilization", "cpuutilization", "cpu", default=None)
            mem = _metric(u, "MemoryUtilization", "memoryutilization", "memory", default=None)

            avg_cpu = avg(cpu) if cpu is not None else None
            avg_mem = avg(mem) if mem is not None else None

            if avg_cpu is None:
                # can't evaluate
                continue

            cpu_low = avg_cpu < cpu_threshold
            mem_low = (avg_mem is not None and avg_mem < memory_threshold)

            if require_memory_metric and avg_mem is None:
                continue

            # If memory metric not available, CPU alone can be used (common).
            is_oversized = cpu_low and (mem_low if avg_mem is not None else True)

            if is_oversized:
                md = c.setdefault("metadata", {})
                md["recommended_action"] = "review"
                md["lookback_hours"] = lookback_hours
                md["avg_cpu_utilization"] = roundf(avg_cpu, 2)
                md["cpu_threshold"] = cpu_threshold
                if avg_mem is not None:
                    md["avg_memory_utilization"] = roundf(avg_mem, 2)
                    md["memory_threshold"] = memory_threshold
                md["check_reason"] = create_check_reason("oversized", {
                    "resource": "emr_cluster",
                    "cluster_id": cluster_id,
                    "lookback_hours": lookback_hours,
                    "avg_cpu": roundf(avg_cpu, 2),
                    "cpu_threshold": cpu_threshold,
                    "avg_mem": roundf(avg_mem, 2) if avg_mem is not None else None,
                    "memory_threshold": memory_threshold if avg_mem is not None else None,
                })
                flagged.append(c)

        except Exception as e:
            print(f"Error checking EMR utilization for {cluster_id}: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="emr_oversized_core_task_nodes",
    name="EMR Oversized Core/Task Nodes",
    description="Identifies EMR clusters with low utilization over a lookback window (potentially oversized)",
    resource_type="emr_cluster",
    check_function=check_emr_oversized_core_task_nodes,
    default_action="review",
    parameters={
        "lookback_hours": 6,
        "cpu_threshold": 20.0,
        "memory_threshold": 30.0,
        "require_memory_metric": False,
        "include_states": ["RUNNING", "WAITING"],
        "region": None,
    },
))
