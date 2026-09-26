"""
S3 Check - Replication Enabled
Flags buckets that have replication rules configured/enabled.

Cost angle:
- Replication duplicates storage and can increase request costs.
- Often enabled historically and forgotten.

This check detects replication presence; it does not determine business need.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason


def _get_bucket_replication(aws_adapter, bucket_name: str, region: Optional[str]) -> Optional[Dict[str, Any]]:
    for fn_name in (
        "get_s3_bucket_replication",
        "get_bucket_replication",
        "get_bucket_replication_configuration",
        "get_resource_replication",
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


def _replication_rules(replication_cfg: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not replication_cfg:
        return []
    # AWS SDK: {"ReplicationConfiguration": {"Rules": [...]}} or {"Rules":[...]}
    rc = replication_cfg.get("ReplicationConfiguration") or replication_cfg.get("replicationConfiguration") or replication_cfg
    if isinstance(rc, dict):
        rules = rc.get("Rules") or rc.get("rules") or []
        return rules if isinstance(rules, list) else []
    return []


def _enabled_rule_count(rules: List[Dict[str, Any]]) -> int:
    count = 0
    for r in rules:
        if not isinstance(r, dict):
            continue
        status = (r.get("Status") or r.get("status") or "").strip().lower()
        # AWS uses "Enabled"/"Disabled"
        if status == "enabled" or status == "":
            # Treat missing Status as enabled-ish (some adapters omit it)
            count += 1
    return count


def _replication_target_buckets(rules: List[Dict[str, Any]]) -> List[str]:
    buckets: List[str] = []
    for r in rules:
        if not isinstance(r, dict):
            continue
        dest = r.get("Destination") or r.get("destination") or {}
        bucket_arn = dest.get("Bucket") or dest.get("bucket")
        if isinstance(bucket_arn, str):
            if bucket_arn.startswith("arn:aws:s3:::"):
                buckets.append(bucket_arn.split(":::", 1)[1])
            else:
                buckets.append(bucket_arn)
    return sorted(list({b for b in buckets if b}))


def check_s3_replication_enabled(
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
            cfg = _get_bucket_replication(aws_adapter, bucket_name, region)
            rules = _replication_rules(cfg)
            enabled_count = _enabled_rule_count(rules)

            if enabled_count > 0:
                md = b.setdefault("metadata", {})
                md["recommended_action"] = "review"
                md["replication_rule_count"] = len(rules)
                md["replication_enabled_rule_count"] = enabled_count
                md["replication_target_buckets"] = _replication_target_buckets(rules)
                md["check_reason"] = create_check_reason("enabled_feature", {
                    "feature": "s3_replication",
                    "bucket": bucket_name,
                    "rules_total": len(rules),
                    "rules_enabled": enabled_count,
                })
                flagged.append(b)

        except Exception as e:
            print(f"Error checking S3 replication for {bucket_name}: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="s3_replication_enabled",
    name="S3 Replication Enabled",
    description="Identifies S3 buckets with replication rules configured/enabled",
    resource_type="s3",
    check_function=check_s3_replication_enabled,
    default_action="review",
    parameters={
        "region": None,
    },
))
