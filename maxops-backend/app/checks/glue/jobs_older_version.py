"""
Glue Check - Jobs Running Older Glue Version

Flags Glue jobs whose GlueVersion is lower than a minimum target.
Example GlueVersion values: "1.0", "2.0", "3.0", "4.0"

Notes:
- This check is version-parameterized (min_glue_version). It does NOT try to guess "latest".
- Works best if your aws_adapter exposes Glue jobs via resource_type "glue_job"
  and includes GlueVersion in metadata.

Assumptions:
- aws_adapter.get_resources("glue_job", filters, region) returns list of jobs with:
  - resource_id (job name)
  - metadata.GlueVersion (or similar)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason


def _parse_glue_version(version: Any) -> Optional[Tuple[int, ...]]:
    """
    Parse:
      - "1.0" -> (1, 0)
      - "2.0" -> (2, 0)
      - "3.0" -> (3, 0)
      - "4.0" -> (4, 0)
    Returns None if unparsable.
    """
    if version is None:
        return None

    s = str(version).strip()
    if not s:
        return None

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


def _version_gte(a: Any, b: Any) -> Optional[bool]:
    pa = _parse_glue_version(a)
    pb = _parse_glue_version(b)
    if pa is None or pb is None:
        return None
    n = max(len(pa), len(pb))
    pa = pa + (0,) * (n - len(pa))
    pb = pb + (0,) * (n - len(pb))
    return pa >= pb


def _md(r: Dict[str, Any]) -> Dict[str, Any]:
    return r.get("metadata") or {}


def _get_job_name(r: Dict[str, Any]) -> Optional[str]:
    return r.get("resource_id") or r.get("id") or r.get("name") or _md(r).get("Name") or _md(r).get("name")


def _glue_version(r: Dict[str, Any]) -> Optional[str]:
    md = _md(r)
    v = (
        md.get("GlueVersion")
        or md.get("glue_version")
        or md.get("glueVersion")
        or r.get("GlueVersion")
        or r.get("glue_version")
    )
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def check_glue_jobs_older_version(
    aws_adapter,
    min_glue_version: str = "3.0",
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Identify Glue jobs running older Glue versions.
    """
    jobs = aws_adapter.get_resources("glue_job", {}, region)

    flagged: List[Dict[str, Any]] = []

    for job in jobs:
        job_name = _get_job_name(job)
        if not job_name:
            continue

        try:
            version = _glue_version(job)
            if not version:
                # Unknown / missing version -> skip to avoid false positives
                continue

            ok = _version_gte(version, min_glue_version)

            # If version is unparsable, skip (avoid false positives)
            if ok is not True:
                md = job.setdefault("metadata", {})
                md["recommended_action"] = "upgrade"
                md["current_glue_version"] = version
                md["min_glue_version"] = min_glue_version
                md["check_reason"] = create_check_reason("version_outdated", {
                    "resource": "glue_job",
                    "job_name": job_name,
                    "current_glue_version": version,
                    "min_glue_version": min_glue_version,
                })
                flagged.append(job)

        except Exception as e:
            print(f"Error checking Glue job version for {job_name}: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="glue_jobs_older_version",
    name="Glue Jobs Older Version",
    description="Identifies Glue jobs running older Glue versions than the minimum target",
    resource_type="glue_job",
    check_function=check_glue_jobs_older_version,
    default_action="upgrade",
    parameters={
        "min_glue_version": "3.0",
        "region": None,
    },
))
