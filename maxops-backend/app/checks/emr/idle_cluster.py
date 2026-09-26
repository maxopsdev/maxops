"""
EMR Check - Idle Clusters (WAITING too long)

Flags EMR clusters that have been in WAITING state longer than a threshold.

Assumptions:
- aws_adapter.get_resources("emr_cluster", filters, region) returns clusters with:
  - resource_id (cluster id)
  - metadata.State (or state)
  - metadata.Status.Timeline.ReadyDateTime / CreationDateTime / etc (best-effort)
- If timeline isn't in metadata, we try best-effort config fetch via adapter methods.

Default action: "terminate" (you may prefer "review" depending on your org).
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason
from app.utils.math_utils import roundf


def _get_cluster_id(c: Dict[str, Any]) -> Optional[str]:
    return c.get("resource_id") or c.get("id") or (c.get("metadata") or {}).get("Id")


def _md(c: Dict[str, Any]) -> Dict[str, Any]:
    return c.get("metadata") or {}


def _get_state(c: Dict[str, Any]) -> str:
    md = _md(c)
    return (md.get("State") or md.get("state") or c.get("State") or c.get("state") or "").strip().upper()


def _parse_dt(val: Any) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.astimezone(timezone.utc) if val.tzinfo else val.replace(tzinfo=timezone.utc)
    s = str(val).strip()
    if not s:
        return None
    # Handle common AWS formats: "2025-01-01T12:34:56Z"
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _get_ready_time_from_metadata(c: Dict[str, Any]) -> Optional[datetime]:
    md = _md(c)
    status = md.get("Status") or md.get("status") or {}
    timeline = {}
    if isinstance(status, dict):
        timeline = status.get("Timeline") or status.get("timeline") or {}
    if isinstance(timeline, dict):
        # ReadyDateTime is when cluster reached WAITING/RUNNING ready state.
        for k in ("ReadyDateTime", "readyDateTime", "CreationDateTime", "creationDateTime"):
            dt = _parse_dt(timeline.get(k))
            if dt:
                return dt
    # Some adapters flatten
    for k in ("ready_time", "ReadyDateTime", "creation_time", "CreationDateTime"):
        dt = _parse_dt(md.get(k) or c.get(k))
        if dt:
            return dt
    return None


def _fetch_cluster_detail(aws_adapter, cluster_id: str, region: Optional[str]) -> Optional[Dict[str, Any]]:
    for fn_name in ("get_emr_cluster", "describe_emr_cluster", "describe_cluster", "get_resource_configuration"):
        fn = getattr(aws_adapter, fn_name, None)
        if callable(fn):
            try:
                try:
                    resp = fn(cluster_id, region)
                except TypeError:
                    resp = fn("emr_cluster", cluster_id, region)
                return resp if isinstance(resp, dict) else None
            except Exception:
                return None
    return None


def check_emr_idle_clusters(
    aws_adapter,
    idle_waiting_hours: int = 6,
    include_states: Optional[List[str]] = None,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Flags clusters in WAITING longer than idle_waiting_hours.

    Returns:
        List of cluster resources flagged with metadata.
    """
    states = include_states or ["WAITING"]
    clusters = aws_adapter.get_resources("emr_cluster", {"state": states}, region)

    now = datetime.now(timezone.utc)
    threshold = timedelta(hours=idle_waiting_hours)

    flagged: List[Dict[str, Any]] = []

    for c in clusters:
        cluster_id = _get_cluster_id(c)
        if not cluster_id:
            continue

        try:
            state = _get_state(c)
            if state not in {s.upper() for s in states}:
                continue

            ready_time = _get_ready_time_from_metadata(c)
            if ready_time is None:
                detail = _fetch_cluster_detail(aws_adapter, cluster_id, region)
                if detail:
                    # Try same extraction on detail payload
                    ready_time = _get_ready_time_from_metadata(detail)

            if ready_time is None:
                # If we cannot determine age, skip to avoid false positives
                continue

            idle_for = now - ready_time
            if idle_for >= threshold:
                md = c.setdefault("metadata", {})
                md["recommended_action"] = "terminate"
                md["cluster_state"] = state
                md["idle_waiting_hours_threshold"] = idle_waiting_hours
                md["ready_time_utc"] = ready_time.isoformat()
                md["idle_waiting_hours"] = roundf(idle_for.total_seconds() / 3600.0, 2)
                md["check_reason"] = create_check_reason("idle", {
                    "resource": "emr_cluster",
                    "cluster_id": cluster_id,
                    "state": state,
                    "idle_waiting_hours": roundf(idle_for.total_seconds() / 3600.0, 2),
                    "threshold_hours": idle_waiting_hours,
                })
                flagged.append(c)

        except Exception as e:
            print(f"Error checking EMR idle cluster {cluster_id}: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="emr_idle_clusters",
    name="EMR Idle Clusters",
    description="Identifies EMR clusters in WAITING state longer than a threshold",
    resource_type="emr_cluster",
    check_function=check_emr_idle_clusters,
    default_action="terminate",
    parameters={
        "idle_waiting_hours": 6,
        "include_states": ["WAITING"],
        "region": None,
    },
))
