
"""
S3 Check - No Archival Policy
Flags buckets whose lifecycle rules do not include transitions to archival/cold storage classes.

Default definition of "archival":
- GLACIER
- DEEP_ARCHIVE
- GLACIER_IR

(You can broaden this to include IA/Intelligent-Tiering via include_infrequent_access=True.)
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


def _has_archival_transition(rules: List[Dict[str, Any]], archival_classes: Set[str]) -> bool:
    """
    True if ANY rule contains Transition or NoncurrentVersionTransition to an archival class.
    """
    archival_classes_norm = {c.upper() for c in archival_classes}

    def _check_transition_list(tlist: Any) -> bool:
        if not isinstance(tlist, list):
            return False
        for t in tlist:
            if not isinstance(t, dict):
                continue
            sc = (t.get("StorageClass") or t.get("storageClass") or "").upper()
            if sc and sc in archival_classes_norm:
                return True
        return False

    for r in rules:
        if not isinstance(r, dict):
            continue

        transitions = r.get("Transitions") or r.get("transitions") or r.get("Transition") or r.get("transition")
        noncurrent_transitions = (
            r.get("NoncurrentVersionTransitions")
            or r.get("noncurrentVersionTransitions")
            or r.get("NoncurrentVersionTransition")
            or r.get("noncurrentVersionTransition")
        )

        # Normalize single dict to list
        if isinstance(transitions, dict):
            transitions = [transitions]
        if isinstance(noncurrent_transitions, dict):
            noncurrent_transitions = [noncurrent_transitions]

        if _check_transition_list(transitions) or _check_transition_list(noncurrent_transitions):
            return True

    return False


def check_s3_no_archival_policy(
    aws_adapter,
    region: Optional[str] = None,
    include_infrequent_access: bool = False,
) -> List[Dict[str, Any]]:
    """
    If include_infrequent_access=True, also treat IA/Intelligent-Tiering as "archival-like".
    """
    buckets = aws_adapter.get_resources("s3", {}, region)

    archival_classes = {"GLACIER", "DEEP_ARCHIVE", "GLACIER_IR"}
    if include_infrequent_access:
        archival_classes |= {"STANDARD_IA", "ONEZONE_IA", "INTELLIGENT_TIERING"}

    flagged: List[Dict[str, Any]] = []
    for b in buckets:
        bucket_name = b.get("resource_id") or b.get("name")
        if not bucket_name:
            continue

        try:
            lifecycle = _get_bucket_lifecycle(aws_adapter, bucket_name, region)
            rules = _rules_from_lifecycle(lifecycle)

            if not _has_archival_transition(rules, archival_classes):
                md = b.setdefault("metadata", {})
                md["recommended_action"] = "review"
                md["rule_count"] = len(rules)
                md["archival_classes_checked"] = sorted(list(archival_classes))
                md["check_reason"] = create_check_reason("missing_policy", {
                    "policy": "archival_transition",
                    "bucket": bucket_name,
                    "archival_classes_checked": sorted(list(archival_classes)),
                })
                flagged.append(b)

        except Exception as e:
            print(f"Error checking S3 archival policy for {bucket_name}: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="s3_no_archival_policy",
    name="S3 No Archival Policy",
    description="Identifies S3 buckets missing lifecycle transitions to archival/cold storage classes",
    resource_type="s3",
    check_function=check_s3_no_archival_policy,
    default_action="review",
    parameters={
        "region": None,
        "include_infrequent_access": False,
    },
))
