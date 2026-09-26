
"""
S3 Check - No Noncurrent Version Expiration
Flags versioned buckets whose lifecycle rules do not include NoncurrentVersionExpiration.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason


def _get_bucket_lifecycle(aws_adapter, bucket_name: str, region: Optional[str]) -> Optional[Dict[str, Any]]:
    for fn_name in (
        "get_s3_bucket_lifecycle",
        "get_bucket_lifecycle",
        "get_bucket_lifecycle_configuration",
        "get_resource_lifecycle",
        "get_resource_configuration",
    ):
        fn = getattr(aws_adapter, fn_name, None)
        if callable(fn):
            try:
                try:
                    return fn(bucket_name, region)
                except TypeError:
                    return fn("s3", bucket_name, region)
            except Exception:
                return None
    raise NotImplementedError("aws_adapter is missing an S3 lifecycle fetch method")


def _get_bucket_versioning_status(aws_adapter, bucket_name: str, region: Optional[str]) -> str:
    """
    Return versioning status string: 'Enabled', 'Suspended', or '' (unknown/not versioned).
    """
    for fn_name in (
        "get_s3_bucket_versioning",
        "get_bucket_versioning",
        "get_resource_versioning",
        "get_bucket_configuration",
    ):
        fn = getattr(aws_adapter, fn_name, None)
        if callable(fn):
            try:
                try:
                    resp = fn(bucket_name, region)
                except TypeError:
                    resp = fn("s3", bucket_name, region)
                if isinstance(resp, dict):
                    return (resp.get("Status") or resp.get("status") or "").strip()
                if isinstance(resp, str):
                    return resp.strip()
            except Exception:
                return ""
    return ""


def _rules_from_lifecycle(lifecycle: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not lifecycle or not isinstance(lifecycle, dict):
        return []
    rules = lifecycle.get("Rules") or lifecycle.get("rules") or []
    return rules if isinstance(rules, list) else []


def _has_noncurrent_expiration(rules: List[Dict[str, Any]]) -> bool:
    for r in rules:
        if not isinstance(r, dict):
            continue
        nve = r.get("NoncurrentVersionExpiration") or r.get("noncurrentVersionExpiration")
        if isinstance(nve, dict):
            # AWS uses NoncurrentDays (and optionally NewerNoncurrentVersions)
            if nve.get("NoncurrentDays") is not None or nve.get("noncurrentDays") is not None:
                return True
            return True
    return False


def check_s3_no_noncurrent_expiration(
    aws_adapter,
    region: Optional[str] = None,
    require_versioning_enabled: bool = True,
) -> List[Dict[str, Any]]:
    """
    If require_versioning_enabled is True, only flags buckets whose versioning is Enabled.
    """
    buckets = aws_adapter.get_resources("s3", {}, region)

    flagged: List[Dict[str, Any]] = []
    for b in buckets:
        bucket_name = b.get("resource_id") or b.get("name")
        if not bucket_name:
            continue

        try:
            status = _get_bucket_versioning_status(aws_adapter, bucket_name, region)
            is_versioned = (status.lower() == "enabled")

            if require_versioning_enabled and not is_versioned:
                continue

            lifecycle = _get_bucket_lifecycle(aws_adapter, bucket_name, region)
            rules = _rules_from_lifecycle(lifecycle)

            if not _has_noncurrent_expiration(rules):
                md = b.setdefault("metadata", {})
                md["recommended_action"] = "review"
                md["rule_count"] = len(rules)
                md["versioning_status"] = status or "unknown"
                md["check_reason"] = create_check_reason("missing_policy", {
                    "policy": "noncurrent_version_expiration",
                    "bucket": bucket_name,
                    "versioning_status": status or "unknown",
                })
                flagged.append(b)

        except Exception as e:
            print(f"Error checking S3 noncurrent expiration for {bucket_name}: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="s3_no_noncurrent_expiration",
    name="S3 No Noncurrent Expiration",
    description="Identifies versioned S3 buckets missing NoncurrentVersionExpiration lifecycle rules",
    resource_type="s3",
    check_function=check_s3_no_noncurrent_expiration,
    default_action="review",
    parameters={
        "region": None,
        "require_versioning_enabled": True,
    },
))
