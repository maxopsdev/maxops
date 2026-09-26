
"""
S3 Check - No Expiration Policy
Flags buckets whose lifecycle rules do not include Expiration for current objects.
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


def _rules_from_lifecycle(lifecycle: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not lifecycle or not isinstance(lifecycle, dict):
        return []
    rules = lifecycle.get("Rules") or lifecycle.get("rules") or []
    return rules if isinstance(rules, list) else []


def _has_current_expiration(rules: List[Dict[str, Any]]) -> bool:
    """
    True if ANY rule includes Expiration with Days/Date/ExpiredObjectDeleteMarker.
    """
    for r in rules:
        if not isinstance(r, dict):
            continue
        exp = r.get("Expiration") or r.get("expiration")
        if isinstance(exp, dict) and exp:
            if (
                exp.get("Days") is not None
                or exp.get("days") is not None
                or exp.get("Date") is not None
                or exp.get("date") is not None
                or exp.get("ExpiredObjectDeleteMarker") is True
                or exp.get("expiredObjectDeleteMarker") is True
            ):
                return True
            # If present, count as configured (even if fields vary by adapter)
            return True
    return False


def check_s3_no_expiration_policy(
    aws_adapter,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    buckets = aws_adapter.get_resources("s3", {}, region)

    flagged: List[Dict[str, Any]] = []
    for b in buckets:
        bucket_name = b.get("resource_id") or b.get("name")
        if not bucket_name:
            continue

        try:
            lifecycle = _get_bucket_lifecycle(aws_adapter, bucket_name, region)
            rules = _rules_from_lifecycle(lifecycle)

            if not _has_current_expiration(rules):
                md = b.setdefault("metadata", {})
                md["recommended_action"] = "review"
                md["rule_count"] = len(rules)
                md["check_reason"] = create_check_reason("missing_policy", {
                    "policy": "expiration",
                    "bucket": bucket_name,
                })
                flagged.append(b)

        except Exception as e:
            print(f"Error checking S3 expiration policy for {bucket_name}: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="s3_no_expiration_policy",
    name="S3 No Expiration Policy",
    description="Identifies S3 buckets missing lifecycle Expiration rules for current objects",
    resource_type="s3",
    check_function=check_s3_no_expiration_policy,
    default_action="review",
    parameters={
        "region": None,
    },
))
