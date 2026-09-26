"""
S3 Check - Versioned Buckets Missing Noncurrent Version Transitions

Flags S3 buckets with Versioning == Enabled that do NOT have lifecycle rules to transition
noncurrent (older) object versions to cheaper storage classes (e.g., GLACIER/DEEP_ARCHIVE/GLACIER_IR).

Why it matters:
- In versioned buckets, older versions can accumulate rapidly.
- Transitioning noncurrent versions is often a large cost saver (storage + long-term retention).

This check:
- Requires Versioning Enabled (configurable).
- Looks for NoncurrentVersionTransitions / NoncurrentVersionTransition in lifecycle rules.
- Optionally, also treats NoncurrentVersionTransition to IA/INTELLIGENT_TIERING as “good enough”.

Assumptions:
- aws_adapter.get_resources("s3", {}, region) returns bucket resources with `resource_id` or `name`.
- aws_adapter provides methods to fetch lifecycle and versioning info (best-effort discovery).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

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
                # Treat missing lifecycle as None
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


def _has_noncurrent_version_transition(
    rules: List[Dict[str, Any]],
    allowed_storage_classes: Set[str],
) -> bool:
    """
    True if ANY rule contains a NoncurrentVersionTransition(s) entry with a StorageClass
    in allowed_storage_classes.
    """
    allowed = {c.upper() for c in allowed_storage_classes}

    def _normalize_to_list(x: Any) -> List[Dict[str, Any]]:
        if x is None:
            return []
        if isinstance(x, dict):
            return [x]
        if isinstance(x, list):
            return [i for i in x if isinstance(i, dict)]
        return []

    for r in rules:
        if not isinstance(r, dict):
            continue

        nvt = (
            r.get("NoncurrentVersionTransitions")
            or r.get("noncurrentVersionTransitions")
            or r.get("NoncurrentVersionTransition")
            or r.get("noncurrentVersionTransition")
        )

        transitions = _normalize_to_list(nvt)
        for t in transitions:
            sc = (t.get("StorageClass") or t.get("storageClass") or "").upper()
            if sc and sc in allowed:
                return True

            # Some adapters may omit StorageClass but still indicate transition structure exists.
            # If you want strict class matching only, remove this fallback.
            if "StorageClass" not in t and "storageClass" not in t and len(t) > 0:
                # Presence of a noncurrent transition is still useful; treat as configured.
                return True

    return False


def check_s3_no_noncurrent_version_transition(
    aws_adapter,
    region: Optional[str] = None,
    require_versioning_enabled: bool = True,
    include_infrequent_access: bool = False,
) -> List[Dict[str, Any]]:
    """
    Flags versioned buckets that do not transition noncurrent versions to cheaper storage.

    Args:
        aws_adapter: AWS adapter with credentials
        region: Optional region filter
        require_versioning_enabled: If True, only evaluate buckets with Versioning == Enabled
        include_infrequent_access: If True, accept IA/Intelligent-Tiering as valid noncurrent transitions

    Returns:
        List of bucket resources flagged with metadata.
    """
    buckets = aws_adapter.get_resources("s3", {}, region)

    # Default: archival transitions only (highest ROI for old versions)
    allowed_classes: Set[str] = {"GLACIER", "DEEP_ARCHIVE", "GLACIER_IR"}
    if include_infrequent_access:
        allowed_classes |= {"STANDARD_IA", "ONEZONE_IA", "INTELLIGENT_TIERING"}

    flagged: List[Dict[str, Any]] = []

    for b in buckets:
        bucket_name = b.get("resource_id") or b.get("name")
        if not bucket_name:
            continue

        try:
            status = _get_bucket_versioning_status(aws_adapter, bucket_name, region)
            is_versioning_enabled = status.lower() == "enabled"

            if require_versioning_enabled and not is_versioning_enabled:
                continue

            lifecycle = _get_bucket_lifecycle(aws_adapter, bucket_name, region)
            rules = _rules_from_lifecycle(lifecycle)

            has_transition = _has_noncurrent_version_transition(rules, allowed_classes)

            if not has_transition:
                md = b.setdefault("metadata", {})
                md["recommended_action"] = "review"
                md["versioning_status"] = status or "unknown"
                md["lifecycle_rule_count"] = len(rules)
                md["allowed_storage_classes"] = sorted(list(allowed_classes))
                md["check_reason"] = create_check_reason("missing_policy", {
                    "policy": "noncurrent_version_transition",
                    "bucket": bucket_name,
                    "versioning_status": status or "unknown",
                    "allowed_storage_classes": sorted(list(allowed_classes)),
                })
                flagged.append(b)

        except Exception as e:
            print(f"Error checking S3 noncurrent version transition for {bucket_name}: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="s3_no_noncurrent_version_transition",
    name="S3 No Noncurrent Version Transition",
    description="Identifies versioned S3 buckets missing lifecycle transitions for noncurrent (older) object versions",
    resource_type="s3",
    check_function=check_s3_no_noncurrent_version_transition,
    default_action="review",
    parameters={
        "region": None,
        "require_versioning_enabled": True,
        "include_infrequent_access": False,
    },
))
