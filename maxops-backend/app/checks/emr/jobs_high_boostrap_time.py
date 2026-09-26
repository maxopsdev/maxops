"""
EMR Check - High Bootstrap / Provisioning Time

Flags EMR clusters whose time-to-ready is high.

Definition:
- bootstrap/provisioning time = ReadyDateTime - CreationDateTime
  (includes provisioning + bootstrap actions + app install until cluster is ready)

Data source:
- EMR DescribeCluster -> Status.Timeline.CreationDateTime / ReadyDateTime

Assumptions:
- aws_adapter.get_resources("emr_cluster", filters, region) returns clusters with:
  - resource_id (cluster id)
  - metadata may already include Status.Timeline.* timestamps
- If timestamps are missing, we best-effort fetch cluster details via adapter methods.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason
from app.utils.math_utils import roundf


def _md(x: Dict[str, Any]) -> Dict[str, Any]:
    return x.get("metadata") or {}


def _get_cluster_id(c: Dict[str, Any]) -> Optional[str]:
    return c.get("resource_id") or c.get("id") or _md(c).get("Id")


def _parse_dt(val: Any) -> Optional[datetime]:
    """
    Parse common AWS datetime shapes into timezone-aware UTC datetime.
    Supports datetime objects and ISO8601 strings (with Z).
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.astimezone(timezone.utc) if val.tzinfo else val.replace(tzinfo=timezone.utc)
    s = str(val).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _timeline_from_obj(obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract Status.Timeline dict if present in either obj or obj["metadata"].
    """
    md = _md(obj)
    status = md.get("Status") or md.get("status") or obj.get("Status") or obj.get("status") or {}
    if isinstance(status, dict):
        timeline = status.get("Timeline") or status.get("timeline") or {}
        return timeline if isinstance(timeline, dict) else {}
    return {}


def _get_creation_and_ready_times(obj: Dict[str, Any]) -> tuple[Optional[datetime], Optional[datetime]]:
    timeline = _timeline_from_obj(obj)

    created = _parse_dt(
        timeline.get("CreationDateTime")
        or timeline.get("creationDateTime")
        or _md(obj).get("CreationDateTime")
        or _md(obj).get("creationDateTime")
        or obj.get("CreationDateTime")
        or obj.get("creationDateTime")
    )

    ready = _parse_dt(
        timeline.get("ReadyDateTime")
        or timeline.get("readyDateTime")
        or _md(obj).get("ReadyDateTime")
        or _md(obj).get("readyDateTime")
        or obj.get("ReadyDateTime")
        or obj.get("readyDateTime")
    )

    return created, ready


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


def _get_state(c: Dict[str, Any]) -> str:
    return ( _md(c).get("State") or _md(c).get("state") or c.get("State") or c.get("state") or "" ).strip().upper()


def check_emr_high_bootstrap_time(
    aws_adapter,
    bootstrap_minutes_threshold: int = 20,
    include_states: Optional[List[str]] = None,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Flags EMR clusters where (ReadyDateTime - CreationDateTime) exceeds bootstrap_minutes_threshold.

    Args:
        aws_adapter: AWS adapter with credentials
        bootstrap_minutes_threshold: minutes threshold for time-to-ready (default: 20)
        include_states: cluster states to evaluate (default: RUNNING, WAITING)
        region: optional region filter

    Returns:
        List of clusters flagged with metadata.
    """
    states = include_states or ["RUNNING", "WAITING"]
    clusters = aws_adapter.get_resources("emr_cluster", {"state": states}, region)

    threshold = timedelta(minutes=bootstrap_minutes_threshold)
    flagged: List[Dict[str, Any]] = []

    for c in clusters:
        cluster_id = _get_cluster_id(c)
        if not cluster_id:
            continue

        try:
            state = _get_state(c)
            if state and state not in {s.upper() for s in states}:
                continue

            created, ready = _get_creation_and_ready_times(c)

            if created is None or ready is None:
                detail = _fetch_cluster_detail(aws_adapter, cluster_id, region)
                if detail:
                    created, ready = _get_creation_and_ready_times(detail)

            # Can't compute reliably -> skip to avoid false positives
            if created is None or ready is None:
                continue

            delta = ready - created
            if delta >= threshold:
                md = c.setdefault("metadata", {})
                md["recommended_action"] = "review"
                md["recommended_actions"] = [
                    "review_bootstrap_actions",
                    "reduce_bootstrap_work",
                    "prebake_ami_or_use_custom_image",
                    "use_emr_runtime_features_or_layered_configs",
                ]
                md["cluster_state"] = state or None
                md["creation_time_utc"] = created.isoformat()
                md["ready_time_utc"] = ready.isoformat()
                md["bootstrap_minutes"] = roundf(delta.total_seconds() / 60.0, 2)
                md["bootstrap_minutes_threshold"] = bootstrap_minutes_threshold

                md["check_reason"] = create_check_reason("slow_startup", {
                    "resource": "emr_cluster",
                    "cluster_id": cluster_id,
                    "state": state or None,
                    "bootstrap_minutes": roundf(delta.total_seconds() / 60.0, 2),
                    "threshold_minutes": bootstrap_minutes_threshold,
                })

                flagged.append(c)

        except Exception as e:
            print(f"Error checking EMR bootstrap time for {cluster_id}: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="emr_high_bootstrap_time",
    name="EMR High Bootstrap Time",
    description="Identifies EMR clusters with high time-to-ready (ReadyDateTime - CreationDateTime)",
    resource_type="emr_cluster",
    check_function=check_emr_high_bootstrap_time,
    default_action="review",
    parameters={
        "bootstrap_minutes_threshold": 20,
        "include_states": ["RUNNING", "WAITING"],
        "region": None,
    },
))
