"""
S3 Check - No Delete Marker Expiration Set
Flags versioned buckets that do NOT have ExpiredObjectDeleteMarker cleanup configured.

Why it matters:
- In versioned buckets, delete markers can accumulate.
- Without cleanup, listings and lifecycle management get noisy and can add operational overhead.

This check:
- Only evaluates buckets with Versioning == Enabled (by default).
- Checks lifecycle rules for Expiration with ExpiredObjectDeleteMarker == True.
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
                    resp = fn(bucket_name, region)
                except TypeError:
                    resp = fn("s3", bucket_name, region)
                return resp if isinstance(resp, dict) else None
            except Exception:
                return None
    return None


def _get_bucket_versioning_status(aws_adapter, bucket_name: str, region: Optional[str]) -> str:
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


def _has_delete_marker_cleanup(rules: List[Dict[str, Any]]) -> bool:
    """
    True if any lifecycle rule has Expiration.ExpiredObjectDeleteMarker = True.
    """
    for r in rules:
        if not isinstance(r, dict):
            continue
        exp = r.get("Expiration") or r.get("expiration")
        if not isinstance(exp, dict):
            continue
        if exp.get("ExpiredObjectDeleteMarker") is True or exp.get("expiredObjectDeleteMarker") is True:
            return True
    return False


def check_s3_no_delete_marker_expiration(
    aws_adapter,
    region: Optional[str] = None,
    require_versioning_enabled: bool = True,
) -> List[Dict[str, Any]]:
    buckets = aws_adapter.get_resources("s3", {}, region)

    flagged: List[Dict[str, Any]] = []
    for b in buckets:
        bucket_name = b.get("resource_id") or b.get("name")
        if not bucket_name:
            continue

        try:
            status = _get_bucket_versioning_status(aws_adapter, bucket_name, region)
            is_versioned = status.lower() == "enabled"

            if require_versioning_enabled and not is_versioned:
                continue

            lifecycle = _get_bucket_lifecycle(aws_adapter, bucket_name, region)
            rules = _rules_from_lifecycle(lifecycle)

            if not _has_delete_marker_cleanup(rules):
                md = b.setdefault("metadata", {})
                md["recommended_action"] = "review"
                md["versioning_status"] = status or "unknown"
                md["lifecycle_rule_count"] = len(rules)
                md["check_reason"] = create_check_reason("missing_policy", {
                    "policy": "expired_object_delete_marker",
                    "bucket": bucket_name,
                    "versioning_status": status or "unknown",
                })
                flagged.append(b)

        except Exception as e:
            print(f"Error checking S3 delete marker cleanup for {bucket_name}: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="s3_no_delete_marker_expiration",
    name="S3 No Delete Marker Expiration",
    description="Identifies versioned S3 buckets missing ExpiredObjectDeleteMarker lifecycle cleanup",
    resource_type="s3",
    check_function=check_s3_no_delete_marker_expiration,
    default_action="review",
    parameters={
        "region": None,
        "require_versioning_enabled": True,
    },
))
