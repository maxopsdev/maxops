"""
S3 Check - Inventory Enabled
Flags buckets that have S3 Inventory configured/enabled.

Why this can matter for cost optimization:
- Inventory reports can create recurring objects (CSV/ORC/Parquet) in a destination bucket.
- If not actively used, it’s waste + can grow without lifecycle/expiration on the destination.

This check only detects inventory being enabled; it does not judge usefulness.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason


def _get_bucket_inventory(aws_adapter, bucket_name: str, region: Optional[str]) -> Optional[Any]:
    """
    Best-effort inventory fetch across adapters.
    Expected shapes:
      - list of inventory configurations
      - dict with "InventoryConfiguration" / "InventoryConfigurations" / "Configurations"
    Return None/empty if not configured.
    """
    for fn_name in (
        "get_s3_bucket_inventory",
        "get_bucket_inventory",
        "list_s3_bucket_inventory",
        "list_bucket_inventory",
        "get_bucket_inventory_configuration",
        "list_bucket_inventory_configurations",
        "get_resource_inventory",
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
    return None


def _inventory_count(inv: Any) -> int:
    if inv is None:
        return 0
    if isinstance(inv, list):
        return len(inv)
    if isinstance(inv, dict):
        for k in (
            "InventoryConfigurationList",
            "inventoryConfigurationList",
            "InventoryConfigurations",
            "inventoryConfigurations",
            "Configurations",
            "configurations",
        ):
            v = inv.get(k)
            if isinstance(v, list):
                return len(v)
        for k in ("InventoryConfiguration", "inventoryConfiguration"):
            v = inv.get(k)
            if isinstance(v, dict):
                return 1
    return 0


def _inventory_configs(inv: Any) -> List[Dict[str, Any]]:
    if inv is None:
        return []
    if isinstance(inv, dict):
        for k in (
            "InventoryConfigurationList",
            "inventoryConfigurationList",
            "InventoryConfigurations",
            "inventoryConfigurations",
            "Configurations",
            "configurations",
        ):
            v = inv.get(k)
            if isinstance(v, list):
                return [i for i in v if isinstance(i, dict)]
        for k in ("InventoryConfiguration", "inventoryConfiguration"):
            v = inv.get(k)
            if isinstance(v, dict):
                return [v]
    if isinstance(inv, list):
        return [i for i in inv if isinstance(i, dict)]
    return []


def _inventory_target_buckets(configs: List[Dict[str, Any]]) -> List[str]:
    buckets: List[str] = []
    for cfg in configs:
        dest = cfg.get("Destination") or {}
        s3_dest = dest.get("S3BucketDestination") or dest.get("s3BucketDestination") or {}
        bucket_arn = s3_dest.get("Bucket") or s3_dest.get("bucket")
        if isinstance(bucket_arn, str):
            if bucket_arn.startswith("arn:aws:s3:::"):
                buckets.append(bucket_arn.split(":::", 1)[1])
            else:
                buckets.append(bucket_arn)
    return sorted(list({b for b in buckets if b}))


def check_s3_inventory_enabled(
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
            inv = _get_bucket_inventory(aws_adapter, bucket_name, region)
            configs = _inventory_configs(inv)
            count = _inventory_count(inv)

            if count > 0:
                md = b.setdefault("metadata", {})
                md["recommended_action"] = "review"
                md["inventory_configuration_count"] = count
                md["inventory_target_buckets"] = _inventory_target_buckets(configs)
                md["check_reason"] = create_check_reason("enabled_feature", {
                    "feature": "s3_inventory",
                    "bucket": bucket_name,
                    "configuration_count": count,
                })
                flagged.append(b)

        except Exception as e:
            print(f"Error checking S3 inventory for {bucket_name}: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="s3_inventory_enabled",
    name="S3 Inventory Enabled",
    description="Identifies S3 buckets with Inventory configured/enabled",
    resource_type="s3",
    check_function=check_s3_inventory_enabled,
    default_action="review",
    parameters={
        "region": None,
    },
))
