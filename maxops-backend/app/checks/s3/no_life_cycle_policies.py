"""
S3 Check - No Lifecycle Policy
Flags buckets that have no lifecycle configuration (or zero rules).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason


def _get_bucket_lifecycle(aws_adapter, bucket_name: str, region: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Best-effort lifecycle fetch to work across adapters.
    Expected to return a dict like {"Rules": [...]} or {"rules": [...]}.
    Return None if lifecycle is not configured.
    """
    # Prefer a dedicated method if your adapter has one.
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
                # Try common signatures
                try:
                    return fn(bucket_name, region)
                except TypeError:
                    return fn("s3", bucket_name, region)
            except Exception:
                # Treat "no lifecycle" or access errors as no config only if adapter does so;
                # otherwise it will be caught by outer try/except.
                return None
    # If none exists, caller can decide how to handle.
    raise NotImplementedError("aws_adapter is missing an S3 lifecycle fetch method")


def _rules_from_lifecycle(lifecycle: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not lifecycle:
        return []
    if isinstance(lifecycle, dict):
        rules = lifecycle.get("Rules") or lifecycle.get("rules") or []
        return rules if isinstance(rules, list) else []
    return []


def check_s3_no_lifecycle_policy(
    aws_adapter,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Returns buckets with no lifecycle policy configured.
    """
    buckets = aws_adapter.get_resources("s3", {}, region)

    flagged: List[Dict[str, Any]] = []
    for b in buckets:
        bucket_name = b.get("resource_id") or b.get("name")
        if not bucket_name:
            continue

        try:
            lifecycle = _get_bucket_lifecycle(aws_adapter, bucket_name, region)
            rules = _rules_from_lifecycle(lifecycle)

            if len(rules) == 0:
                md = b.setdefault("metadata", {})
                md["recommended_action"] = "review"
                md["rule_count"] = 0
                md["check_reason"] = create_check_reason("missing_policy", {
                    "policy": "lifecycle",
                    "bucket": bucket_name,
                })
                flagged.append(b)

        except Exception as e:
            print(f"Error checking S3 bucket lifecycle for {bucket_name}: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="s3_no_lifecycle_policy",
    name="S3 No Lifecycle Policy",
    description="Identifies S3 buckets with no lifecycle configuration (no rules)",
    resource_type="s3",
    check_function=check_s3_no_lifecycle_policy,
    default_action="review",
    parameters={
        "region": None,
    },
))
