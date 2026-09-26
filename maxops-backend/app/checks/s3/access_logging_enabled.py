"""
S3 Check - Server Access Logging Enabled
Flags buckets that have S3 server access logging enabled.

Cost angle:
- Access logs generate objects continuously in the target bucket.
- If you don’t lifecycle/expire them, the log bucket can become a major cost center.

This check only detects logging enabled; it does not validate destination lifecycle.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason


def _get_bucket_logging(aws_adapter, bucket_name: str, region: Optional[str]) -> Optional[Dict[str, Any]]:
    for fn_name in (
        "get_s3_bucket_logging",
        "get_bucket_logging",
        "get_bucket_logging_configuration",
        "get_resource_logging",
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


def _logging_enabled(logging_cfg: Optional[Dict[str, Any]]) -> bool:
    if not logging_cfg:
        return False
    # AWS SDK: {"LoggingEnabled": {"TargetBucket": "...", "TargetPrefix": "..."}}
    le = logging_cfg.get("LoggingEnabled") or logging_cfg.get("loggingEnabled")
    return isinstance(le, dict) and len(le) > 0


def check_s3_logging_enabled(
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
            cfg = _get_bucket_logging(aws_adapter, bucket_name, region)

            if _logging_enabled(cfg):
                le = cfg.get("LoggingEnabled") or cfg.get("loggingEnabled") or {}
                target_bucket = le.get("TargetBucket") or le.get("targetBucket")
                target_prefix = le.get("TargetPrefix") or le.get("targetPrefix")

                md = b.setdefault("metadata", {})
                md["recommended_action"] = "review"
                md["logging_target_bucket"] = target_bucket
                md["logging_target_prefix"] = target_prefix
                md["check_reason"] = create_check_reason("enabled_feature", {
                    "feature": "s3_server_access_logging",
                    "bucket": bucket_name,
                    "target_bucket": target_bucket,
                    "target_prefix": target_prefix,
                })
                flagged.append(b)

        except Exception as e:
            print(f"Error checking S3 logging for {bucket_name}: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="s3_logging_enabled",
    name="S3 Logging Enabled",
    description="Identifies S3 buckets with server access logging enabled",
    resource_type="s3",
    check_function=check_s3_logging_enabled,
    default_action="review",
    parameters={
        "region": None,
    },
))
