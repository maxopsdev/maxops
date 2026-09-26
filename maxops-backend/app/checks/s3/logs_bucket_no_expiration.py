"""
S3 Check - Log Buckets Without Expiration Policy

Flags S3 buckets that look like "log buckets" but have NO lifecycle Expiration rule
for current objects.

Why this matters:
- Log buckets (CloudTrail, ALB/NLB access logs, VPC flow logs exports, etc.) can grow forever.
- Without an explicit expiration policy, storage costs typically trend upward with no bound.

How "log bucket" is detected (best-effort, configurable):
- Bucket name matches common log patterns (default list below), OR
- Bucket has tags indicating logs (e.g., purpose=logs, data=logs), if your adapter provides tags in metadata.

Assumptions:
- aws_adapter.get_resources("s3", {}, region) returns bucket resources with at least:
    { "resource_id": "<bucket-name>", "metadata": {...optional...} }
- aws_adapter has a method to fetch bucket lifecycle configuration:
    one of: get_s3_bucket_lifecycle / get_bucket_lifecycle / get_bucket_lifecycle_configuration / ...
  (same best-effort discovery pattern used in other S3 checks)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

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
                # If lifecycle isn't configured or access denied, adapter might raise.
                # We treat it as "no lifecycle" and let the check decide.
                return None
    # If your adapter doesn't implement lifecycle fetch yet, add one and remove this raise.
    raise NotImplementedError("aws_adapter is missing an S3 lifecycle fetch method")


def _rules_from_lifecycle(lifecycle: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not lifecycle or not isinstance(lifecycle, dict):
        return []
    rules = lifecycle.get("Rules") or lifecycle.get("rules") or []
    return rules if isinstance(rules, list) else []


def _has_current_object_expiration(rules: List[Dict[str, Any]]) -> bool:
    """
    True if ANY lifecycle rule includes Expiration configured for current objects.
    We check common AWS fields:
      - Expiration: { Days | Date | ExpiredObjectDeleteMarker }
    Note: ExpiredObjectDeleteMarker alone does not expire current objects,
    but we count it as an expiration configuration in general. If you want to require
    Days/Date specifically, tighten the logic below.
    """
    for r in rules:
        if not isinstance(r, dict):
            continue
        exp = r.get("Expiration") or r.get("expiration")
        if not isinstance(exp, dict) or not exp:
            continue

        # Current object expiration is typically Days or Date.
        if exp.get("Days") is not None or exp.get("days") is not None:
            return True
        if exp.get("Date") is not None or exp.get("date") is not None:
            return True

        # If you consider delete-marker cleanup as "expiration policy exists", keep this.
        # If you want strict current object expiration, remove the two lines below.
        if exp.get("ExpiredObjectDeleteMarker") is True or exp.get("expiredObjectDeleteMarker") is True:
            return True

    return False


def _looks_like_log_bucket(
    bucket_name: str,
    metadata: Dict[str, Any],
    name_patterns: Sequence[str],
    tag_keys: Sequence[str],
    tag_values: Sequence[str],
) -> Tuple[bool, str]:
    """
    Return (is_log_bucket, reason).
    - Name-based detection via substring patterns
    - Tag-based detection via known tag keys/values (if present in metadata)
    """
    n = (bucket_name or "").lower()

    for p in name_patterns:
        if p.lower() in n:
            return True, f"name_match:{p}"

    # Tags: accept a few possible shapes in metadata
    # - metadata["tags"] as dict or list of {Key,Value}
    tags_obj = metadata.get("tags") or metadata.get("Tags") or metadata.get("tag_set") or metadata.get("TagSet")
    tags: Dict[str, str] = {}

    if isinstance(tags_obj, dict):
        tags = {str(k).lower(): str(v).lower() for k, v in tags_obj.items() if k is not None and v is not None}
    elif isinstance(tags_obj, list):
        for item in tags_obj:
            if isinstance(item, dict):
                k = item.get("Key") or item.get("key")
                v = item.get("Value") or item.get("value")
                if k is not None and v is not None:
                    tags[str(k).lower()] = str(v).lower()

    if tags:
        keys = {k.lower() for k in tag_keys}
        values = {v.lower() for v in tag_values}

        for k, v in tags.items():
            if k in keys:
                return True, f"tag_key_match:{k}"
            if v in values:
                return True, f"tag_value_match:{k}={v}"

    return False, ""


def check_s3_log_buckets_without_expiration_policy(
    aws_adapter,
    region: Optional[str] = None,
    log_name_patterns: Optional[List[str]] = None,
    log_tag_keys: Optional[List[str]] = None,
    log_tag_values: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Identify log buckets that do not have current-object expiration lifecycle rules.

    Args:
        aws_adapter: AWS adapter with credentials
        region: Optional region filter
        log_name_patterns: Substrings to classify a bucket as a log bucket
        log_tag_keys: Tag keys that classify a bucket as logs (if tags are available)
        log_tag_values: Tag values that classify a bucket as logs

    Returns:
        List of buckets (resources) flagged with metadata.
    """
    # Sensible defaults (tune to your org conventions)
    name_patterns = log_name_patterns or [
        "log", "logs", "logging",
        "cloudtrail",
        "alb", "elbv2", "access-logs", "accesslogs",
        "vpcflow", "flowlogs", "vpc-flow",
        "awslogs", "s3-access",
        "athena-results",  # often acts like a log/artifact bucket
    ]
    tag_keys = log_tag_keys or ["purpose", "data_class", "bucket_type", "category", "type"]
    tag_values = log_tag_values or ["logs", "log", "logging", "audit", "cloudtrail", "access-logs", "flow-logs"]

    buckets = aws_adapter.get_resources("s3", {}, region)

    flagged: List[Dict[str, Any]] = []
    for b in buckets:
        bucket_name = b.get("resource_id") or b.get("name")
        if not bucket_name:
            continue

        try:
            md = b.get("metadata") or {}
            is_log, log_reason = _looks_like_log_bucket(bucket_name, md, name_patterns, tag_keys, tag_values)
            if not is_log:
                continue

            lifecycle = _get_bucket_lifecycle(aws_adapter, bucket_name, region)
            rules = _rules_from_lifecycle(lifecycle)

            has_expiration = _has_current_object_expiration(rules)

            if not has_expiration:
                b.setdefault("metadata", {})
                b["metadata"]["recommended_action"] = "review"
                b["metadata"]["log_bucket_reason"] = log_reason
                b["metadata"]["lifecycle_rule_count"] = len(rules)
                b["metadata"]["check_reason"] = create_check_reason("missing_policy", {
                    "policy": "expiration_for_log_bucket",
                    "bucket": bucket_name,
                    "log_bucket_reason": log_reason,
                })
                flagged.append(b)

        except Exception as e:
            print(f"Error checking log bucket expiration policy for {bucket_name}: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="s3_log_buckets_without_expiration_policy",
    name="S3 Log Buckets Without Expiration Policy",
    description="Identifies S3 log buckets that do not have lifecycle expiration configured for current objects",
    resource_type="s3",
    check_function=check_s3_log_buckets_without_expiration_policy,
    default_action="review",
    parameters={
        "region": None,
        "log_name_patterns": None,
        "log_tag_keys": None,
        "log_tag_values": None,
    },
))
