"""S3 pricing handler."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict

from app.pricing.base import PricingContext
from app.services.aws_credentials import create_runtime_boto3_session

logger = logging.getLogger("uvicorn.error")
S3_STANDARD_PRICE_PER_GB = 0.023
S3_GLACIER_PRICE_PER_GB = 0.004
S3_DEEP_ARCHIVE_PRICE_PER_GB = 0.00099
# Deprecated Phase-1 fallback constants. New optimizer callers should pass a
# CUR-derived ``price_map``; these remain for the existing sessionless path.


def _get_s3_bucket_metrics(bucket_name: str, region: str) -> Dict[str, Any]:
    """Fetch S3 bucket storage metrics from CloudWatch."""
    try:
        cloudwatch = create_runtime_boto3_session(region_name=region).client("cloudwatch", region_name=region)
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=1)
        size_response = cloudwatch.get_metric_statistics(
            Namespace="AWS/S3",
            MetricName="BucketSizeBytes",
            Dimensions=[
                {"Name": "BucketName", "Value": bucket_name},
                {"Name": "StorageType", "Value": "StandardStorage"},
            ],
            StartTime=start_time,
            EndTime=end_time,
            Period=86400,
            Statistics=["Average"],
        )
        objects_response = cloudwatch.get_metric_statistics(
            Namespace="AWS/S3",
            MetricName="NumberOfObjects",
            Dimensions=[
                {"Name": "BucketName", "Value": bucket_name},
                {"Name": "StorageType", "Value": "AllStorageTypes"},
            ],
            StartTime=start_time,
            EndTime=end_time,
            Period=86400,
            Statistics=["Average"],
        )

        bucket_size_bytes = 0
        if size_response.get("Datapoints"):
            bucket_size_bytes = size_response["Datapoints"][0].get("Average", 0)
        object_count = 0
        if objects_response.get("Datapoints"):
            object_count = objects_response["Datapoints"][0].get("Average", 0)
        return {
            "bucket_size_bytes": bucket_size_bytes,
            "bucket_size_gb": bucket_size_bytes / (1024 ** 3),
            "object_count": int(object_count),
        }
    except Exception as exc:
        logger.warning("Failed to fetch S3 metrics for %s: %s", bucket_name, exc)
        return {"bucket_size_bytes": 0, "bucket_size_gb": 0, "object_count": 0}


def estimate_s3_storage_cost(
    bucket_name: str,
    region: str,
    price_map: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Estimate monthly S3 storage cost, or zero metrics when CloudWatch is unknown.

    ``price_map`` may be the resolved canonical-key mapping from the S3 price
    map.  When it is omitted, the deprecated Standard constant preserves the
    existing behavior for callers that have not migrated yet.
    """
    metrics = _get_s3_bucket_metrics(bucket_name, region)
    bucket_size_gb = metrics["bucket_size_gb"]
    standard_price = S3_STANDARD_PRICE_PER_GB
    if price_map is not None:
        standard_entry = price_map.get("storage.STANDARD.gb_month", price_map.get("STANDARD"))
        if isinstance(standard_entry, dict):
            standard_price = standard_entry.get("price")
        elif standard_entry is not None:
            standard_price = standard_entry
        if standard_price is None:
            current_monthly_cost = None
        else:
            current_monthly_cost = bucket_size_gb * float(standard_price)
    else:
        current_monthly_cost = bucket_size_gb * standard_price
    return {
        **metrics,
        "estimated_monthly_cost": current_monthly_cost,
    }


def handle_s3_pricing(context: PricingContext) -> None:
    """Add S3 storage costs and potential savings based on bucket metrics."""
    check_id = context.check_id
    resources = context.resources
    
    check_configs = {
        "s3_no_archival_policy": {
            "savings_ratio": 0.8,
            "note": "Moving to Glacier storage class saves ~80% on storage costs",
            "target_storage_price": S3_GLACIER_PRICE_PER_GB,
        },
        "s3_no_lifecycle_policy": {
            "savings_ratio": 0.5,
            "note": "Lifecycle policies can save 50%+ by automating transitions to cheaper storage classes",
            "target_storage_price": S3_STANDARD_PRICE_PER_GB * 0.5,
        },
        "s3_no_expiration_policy": {
            "savings_ratio": 0.3,
            "note": "Automatically expiring old objects can reduce storage by ~30%",
            "target_storage_price": 0,
        },
        "s3_log_buckets_without_expiration_policy": {
            "savings_ratio": 0.7,
            "note": "Log buckets grow continuously. Expiring logs older than 90-180 days typically saves 70%+",
            "target_storage_price": 0,
        },
        "s3_no_noncurrent_expiration": {
            "savings_ratio": 0.2,
            "note": "Old noncurrent versions accumulate. Expiring them saves ~20% on versioned buckets",
            "target_storage_price": 0,
        },
        "s3_no_noncurrent_version_transition": {
            "savings_ratio": 0.85,
            "note": "Archiving noncurrent versions to Deep Archive saves ~85% while maintaining compliance",
            "target_storage_price": S3_DEEP_ARCHIVE_PRICE_PER_GB,
        },
        "s3_no_mpu_policy": {
            "savings_ratio": 0.05,
            "note": "Incomplete multipart uploads waste storage. Cleanup typically recovers ~5% of storage",
            "target_storage_price": 0,
        },
        "s3_no_delete_marker_expiration": {
            "savings_ratio": 0.1,
            "note": "Expiring delete markers can reduce versioned bucket storage waste",
            "target_storage_price": 0,
        },
        "s3_logging_enabled": {
            "savings_ratio": 0.1,
            "note": "Server access logging creates recurring objects; lifecycle controls can reduce retained log storage",
            "target_storage_price": 0,
        },
        "s3_inventory_enabled": {
            "savings_ratio": 0.05,
            "note": "S3 Inventory creates recurring reports; remove unused reports or expire old report output",
            "target_storage_price": 0,
        },
        "s3_replication_enabled": {
            "savings_ratio": 0.4,
            "note": "Replication can duplicate storage. Review whether replicated data is still required",
            "target_storage_price": 0,
        },
        "athena_query_results_bucket_no_lifecycle_policy": {
            "savings_ratio": 0.7,
            "note": "Athena query-result buckets can grow over time. Lifecycle expiration commonly reduces retained result storage",
            "target_storage_price": 0,
        },
    }
    config = check_configs.get(check_id, {})
    savings_ratio = config.get("savings_ratio", 0)
    note = config.get("note", "S3 pricing is usage-based and varies by storage class")
    for resource in resources:
        metadata = resource.get("metadata") or {}
        bucket_name = resource.get("resource_id") or resource.get("resource_name")
        region = resource.get("region", "us-east-1")
        metrics = estimate_s3_storage_cost(bucket_name, region)
        bucket_size_gb = metrics["bucket_size_gb"]
        object_count = metrics["object_count"]
        metadata["bucket_size_gb"] = round(bucket_size_gb, 2)
        metadata["object_count"] = object_count
        metadata["estimated_monthly_cost"] = round(metrics["estimated_monthly_cost"], 4)
        if bucket_size_gb > 0:
            current_monthly_cost = metrics["estimated_monthly_cost"]
            if savings_ratio > 0:
                monthly_savings = current_monthly_cost * savings_ratio
                metadata["potential_savings_monthly"] = round(monthly_savings, 4)
                metadata["potential_savings_yearly"] = round(monthly_savings * 12, 4)
            if savings_ratio > 0:
                metadata["cost_note"] = (
                    f"{note}. Current storage: {round(bucket_size_gb, 2)} GB (~${round(current_monthly_cost, 2)}/month)"
                )
            else:
                metadata["cost_note"] = note
        else:
            metadata["cost_note"] = f"{note}. (No storage data available - bucket may be empty or metrics not yet published)"
