
"""
S3 Check - No MPU (Abort Incomplete Multipart Upload) Policy
Flags buckets whose lifecycle rules do not include AbortIncompleteMultipartUpload.
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


def _has_abort_mpu_rule(rules: List[Dict[str, Any]]) -> bool:
    """
    True if ANY rule includes AbortIncompleteMultipartUpload.
    """
    for r in rules:
        if not isinstance(r, dict):
            continue
        a = r.get("AbortIncompleteMultipartUpload") or r.get("abortIncompleteMultipartUpload")
        if isinstance(a, dict):
            # AWS uses DaysAfterInitiation under AbortIncompleteMultipartUpload
            if a.get("DaysAfterInitiation") is not None or a.get("daysAfterInitiation") is not None:
                return True
            # If present at all, still count it as configured.
            return True
    return False


def check_s3_no_mpu_policy(
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

            if not _has_abort_mpu_rule(rules):
                md = b.setdefault("metadata", {})
                md["recommended_action"] = "review"
                md["rule_count"] = len(rules)
                md["check_reason"] = create_check_reason("missing_policy", {
                    "policy": "abort_incomplete_multipart_upload",
                    "bucket": bucket_name,
                })
                flagged.append(b)

        except Exception as e:
            print(f"Error checking S3 MPU policy for {bucket_name}: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="s3_no_mpu_policy",
    name="S3 No MPU Policy",
    description="Identifies S3 buckets missing lifecycle AbortIncompleteMultipartUpload rules",
    resource_type="s3",
    check_function=check_s3_no_mpu_policy,
    default_action="review",
    parameters={
        "region": None,
    },
))
