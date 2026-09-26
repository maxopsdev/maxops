"""
EMR Check - Clusters Running Older EMR Release Label

Flags RUNNING/WAITING EMR clusters whose ReleaseLabel is lower than a minimum target.
Example ReleaseLabel values: "emr-6.10.0", "emr-7.1.0"

Notes:
- This check is version-parameterized (min_release_label). It does NOT try to guess "latest".
- Works best if your aws_adapter exposes EMR clusters via resource_type "emr_cluster"
  and includes ReleaseLabel in metadata.

Assumptions:
- aws_adapter.get_resources("emr_cluster", filters, region) returns list of clusters with:
  - resource_id (cluster id)
  - metadata.ReleaseLabel (or similar)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason


def _parse_emr_release_label(label: Any) -> Optional[Tuple[int, ...]]:
    """
    Parse:
      - "emr-6.10.0" -> (6, 10, 0)
      - "6.10.0" -> (6, 10, 0)
      - "emr-6.10" -> (6, 10)
    Returns None if unparsable.
    """
    if label is None:
        return None

    s = str(label).strip().lower()
    if not s:
        return None

    if s.startswith("emr-"):
        s = s[4:]

    parts = s.split(".")
    out: List[int] = []
    for p in parts:
        p = p.strip()
        if p == "":
            out.append(0)
            continue
        try:
            out.append(int(p))
        except ValueError:
            return None
    return tuple(out)


def _version_lt(a: Any, b: Any) -> Optional[bool]:
    pa = _parse_emr_release_label(a)
    pb = _parse_emr_release_label(b)
    if pa is None or pb is None:
        return None

    max_len = max(len(pa), len(pb))
    pa = pa + (0,) * (max_len - len(pa))
    pb = pb + (0,) * (max_len - len(pb))
    return pa < pb


def _get_cluster_release_label(cluster: Dict[str, Any]) -> Optional[str]:
    md = cluster.get("metadata") or {}
    return (
        md.get("ReleaseLabel")
        or md.get("release_label")
        or md.get("releaseLabel")
        or cluster.get("ReleaseLabel")
        or cluster.get("release_label")
    )


def _get_cluster_state(cluster: Dict[str, Any]) -> str:
    md = cluster.get("metadata") or {}
    return (
        md.get("State")
        or md.get("state")
        or cluster.get("State")
        or cluster.get("state")
        or ""
    )


def check_emr_clusters_older_version(
    aws_adapter,
    min_release_label: str = "emr-6.10.0",
    include_states: Optional[List[str]] = None,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Flags clusters where ReleaseLabel < min_release_label.

    Args:
        aws_adapter: AWS adapter with credentials
        min_release_label: minimum allowed EMR release label (default: emr-6.10.0)
        include_states: which states to evaluate (default: RUNNING, WAITING)
        region: optional region filter

    Returns:
        List of clusters flagged as running an older EMR version.
    """
    states = include_states or ["RUNNING", "WAITING"]

    # Best-effort state filter (adapter may ignore unknown keys)
    clusters = aws_adapter.get_resources("emr_cluster", {"state": states}, region)

    flagged: List[Dict[str, Any]] = []

    for c in clusters:
        cluster_id = c.get("resource_id") or c.get("id") or (c.get("metadata") or {}).get("Id")
        if not cluster_id:
            continue

        try:
            state = _get_cluster_state(c)
            if state and state.upper() not in {s.upper() for s in states}:
                continue

            release = _get_cluster_release_label(c)
            cmp_res = _version_lt(release, min_release_label)

            # If versions can't be parsed, skip to avoid false positives.
            if cmp_res is not True:
                continue

            md = c.setdefault("metadata", {})
            md["recommended_action"] = "upgrade"
            md["current_release_label"] = release
            md["min_release_label"] = min_release_label
            md["cluster_state"] = state or None

            md["check_reason"] = create_check_reason("outdated_version", {
                "resource": "emr_cluster",
                "cluster_id": cluster_id,
                "current_release_label": release,
                "min_release_label": min_release_label,
                "state": state or None,
            })

            flagged.append(c)

        except Exception as e:
            print(f"Error checking EMR cluster {cluster_id} for older version: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="emr_clusters_older_version",
    name="EMR Clusters Older Version",
    description="Identifies EMR clusters (RUNNING/WAITING) with ReleaseLabel below a minimum target",
    resource_type="emr_cluster",
    check_function=check_emr_clusters_older_version,
    default_action="upgrade",
    parameters={
        "min_release_label": "emr-6.10.0",
        "include_states": ["RUNNING", "WAITING"],
        "region": None,
    },
))
