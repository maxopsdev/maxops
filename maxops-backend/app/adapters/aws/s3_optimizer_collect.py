"""AWS collection helpers used by the offline S3 optimizer.

The functions in this module deliberately accept an adapter instance instead
of constructing clients themselves.  That keeps credentials, simulator
support, and the scan's per-region client lifetime in ``AWSAdapter`` while
leaving the S3 optimizer's collection rules small and testable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional

from botocore.exceptions import ClientError


def _cloudwatch_client(adapter: Any, region: str) -> Any:
    """Return a CloudWatch client in the bucket's region.

    S3 storage metrics are regional even though bucket listing is global.  A
    client created for the scan's default region can therefore return no
    datapoints for a bucket in another region.
    """

    session = getattr(adapter, "session", None)
    if session is not None:
        return session.client("cloudwatch", region_name=region)
    client = getattr(adapter, "cloudwatch_client", None)
    if client is None:
        raise AttributeError("adapter has no CloudWatch client")
    return client


def _s3_client(adapter: Any, region: Optional[str]) -> Any:
    """Return an S3 client, using the requested region when the session allows it."""

    session = getattr(adapter, "session", None)
    if session is not None:
        kwargs = {} if region is None else {"region_name": region}
        return session.client("s3", **kwargs)
    client = getattr(adapter, "s3_client", None)
    if client is None:
        raise AttributeError("adapter has no S3 client")
    return client


def discover_storage_types(adapter: Any, region: str) -> dict[str, set[str]]:
    """Discover sparse S3 ``StorageType`` dimensions once per adapter/region.

    ``ListMetrics`` is intentionally used as a single regional sweep.  Asking
    CloudWatch separately for all documented storage types for every bucket
    would multiply subsequent ``GetMetricData`` calls by the roughly nineteen
    possible dimensions, including dimensions that do not exist in the
    account.  An empty sweep returns an empty mapping, never fabricated zero
    dimensions.
    """

    cache = getattr(adapter, "_s3_optimizer_storage_types", None)
    if cache is None:
        cache = {}
        setattr(adapter, "_s3_optimizer_storage_types", cache)
    if region in cache:
        return {bucket: set(types) for bucket, types in cache[region].items()}

    client = _cloudwatch_client(adapter, region)
    discovered: dict[str, set[str]] = {}
    request: dict[str, Any] = {
        "Namespace": "AWS/S3",
        "MetricName": "BucketSizeBytes",
    }
    while True:
        response = client.list_metrics(**request)
        for metric in response.get("Metrics", []) or []:
            dimensions = {
                item.get("Name"): item.get("Value")
                for item in metric.get("Dimensions", []) or []
            }
            bucket = dimensions.get("BucketName")
            storage_type = dimensions.get("StorageType")
            if bucket and storage_type:
                discovered.setdefault(bucket, set()).add(storage_type)
        token = response.get("NextToken")
        if not token:
            break
        request["NextToken"] = token

    cache[region] = discovered
    return {bucket: set(types) for bucket, types in discovered.items()}


def _metric_query(query_id: str, bucket: str, storage_type: str) -> dict[str, Any]:
    """Build one daily-average BucketSizeBytes query."""

    return {
        "Id": query_id,
        "MetricStat": {
            "Metric": {
                "Namespace": "AWS/S3",
                "MetricName": "BucketSizeBytes",
                "Dimensions": [
                    {"Name": "BucketName", "Value": bucket},
                    {"Name": "StorageType", "Value": storage_type},
                ],
            },
            "Period": 86400,
            "Stat": "Average",
        },
        "ReturnData": True,
    }


def _object_query(bucket: str) -> dict[str, Any]:
    """Build the bucket-wide daily NumberOfObjects query."""

    return {
        "Id": "number_of_objects",
        "MetricStat": {
            "Metric": {
                "Namespace": "AWS/S3",
                "MetricName": "NumberOfObjects",
                "Dimensions": [
                    {"Name": "BucketName", "Value": bucket},
                    {"Name": "StorageType", "Value": "AllStorageTypes"},
                ],
            },
            "Period": 86400,
            "Stat": "Average",
        },
        "ReturnData": True,
    }


def _metric_points(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Convert a CloudWatch result to the Phase 2 date/value fixture shape."""

    timestamps = result.get("Timestamps", []) or []
    values = result.get("Values", []) or []
    points = []
    for timestamp, value in zip(timestamps, values):
        if value is None:
            continue
        if hasattr(timestamp, "isoformat"):
            date_value = timestamp.isoformat()[:10]
        else:
            date_value = str(timestamp)[:10]
        points.append({"date": date_value, "value": float(value)})
    return sorted(points, key=lambda point: point["date"])


def fetch_daily_storage_metrics(
    adapter: Any,
    bucket: str,
    storage_types: Iterable[str],
    days: int = 90,
    region: Optional[str] = None,
) -> dict[str, Any]:
    """Fetch daily free S3 storage metrics in batches of at most 500 queries.

    S3 publishes these metrics with a documented 24--48 hour lag, so the
    window is sent as-is and only datapoints AWS returns are retained.  An
    empty metric result omits that metric key; it is never converted to a
    zero-valued series because absence is an unknown signal.
    """

    if days <= 0:
        raise ValueError("days must be positive")
    if region is None:
        region = getattr(adapter, "_default_region", None)
    client = _cloudwatch_client(adapter, region)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    unique_types = sorted({str(storage_type) for storage_type in storage_types if storage_type})
    queries: list[tuple[str, str]] = []
    for index, storage_type in enumerate(unique_types):
        queries.append((f"size_{index}", storage_type))
    queries.append(("number_of_objects", "NumberOfObjects"))

    metrics: dict[str, list[dict[str, Any]]] = {}
    for offset in range(0, len(queries), 500):
        batch = queries[offset : offset + 500]
        request_queries = [
            _object_query(bucket) if storage_type == "NumberOfObjects" else _metric_query(query_id, bucket, storage_type)
            for query_id, storage_type in batch
        ]
        token: Optional[str] = None
        while True:
            request: dict[str, Any] = {
                "MetricDataQueries": request_queries,
                "StartTime": start,
                "EndTime": end,
                "ScanBy": "TimestampAscending",
            }
            if token:
                request["NextToken"] = token
            response = client.get_metric_data(**request)
            for result in response.get("MetricDataResults", []) or []:
                points = _metric_points(result)
                if points:
                    metrics[result.get("Id", "")] = points
            token = response.get("NextToken")
            if not token:
                break

    output: dict[str, Any] = {"metrics": {bucket: {}}}
    bucket_metrics = output["metrics"][bucket]
    for query_id, storage_type in queries:
        points = metrics.get(query_id)
        if points:
            bucket_metrics[storage_type] = points
    return output


def _client_error_code(error: ClientError) -> str:
    """Return a stable AWS error code for typed S3 error handling."""

    return str(error.response.get("Error", {}).get("Code") or "")


def get_s3_bucket_lifecycle_typed(
    adapter: Any,
    bucket: str,
    region: Optional[str] = None,
) -> dict[str, Any]:
    """Return lifecycle configuration without collapsing access errors to absence.

    AWS uses ``NoSuchLifecycleConfiguration`` for a confirmed empty
    configuration.  Access-denied, throttling, and malformed responses stay
    unknown so the optimizer cannot price or recommend from a false empty rule.
    """

    try:
        response = _s3_client(adapter, region).get_bucket_lifecycle_configuration(Bucket=bucket)
        rules = response.get("Rules", [])
        if not isinstance(rules, list):
            raise ValueError("lifecycle response Rules is not a list")
        return {"status": "usable", "rules": rules, "error": None}
    except ClientError as exc:
        code = _client_error_code(exc)
        if code == "NoSuchLifecycleConfiguration":
            return {"status": "absent", "rules": [], "error": None}
        if "AccessDenied" in code or "Unauthorized" in code:
            return {"status": "access_denied", "rules": None, "error": str(exc)}
        return {"status": "error", "rules": None, "error": str(exc)}
    except Exception as exc:
        return {"status": "error", "rules": None, "error": str(exc)}


def _parse_intelligent_tiering_filter(filter_value: Any) -> tuple[Optional[str], dict[str, str]]:
    """Flatten AWS's Prefix/Tag/And filter variants to the stored shape."""

    if not isinstance(filter_value, Mapping):
        return None, {}
    prefix = filter_value.get("Prefix")
    tags: dict[str, str] = {}
    tag = filter_value.get("Tag")
    if isinstance(tag, Mapping) and tag.get("Key") is not None:
        tags[str(tag["Key"])] = str(tag.get("Value", ""))
    and_filter = filter_value.get("And")
    if isinstance(and_filter, Mapping):
        prefix = and_filter.get("Prefix", prefix)
        for tag_item in and_filter.get("Tags", []) or []:
            if isinstance(tag_item, Mapping) and tag_item.get("Key") is not None:
                tags[str(tag_item["Key"])] = str(tag_item.get("Value", ""))
    return (str(prefix) if prefix is not None else None), tags


def list_s3_bucket_intelligent_tiering_configurations(
    adapter: Any,
    bucket: str,
    region: Optional[str] = None,
) -> dict[str, Any]:
    """List and parse all Intelligent-Tiering configurations for a bucket.

    This API has no lifecycle-style "not configured" exception: a successful
    empty page sequence is therefore ``usable`` with ``configurations: []``.
    """

    try:
        client = _s3_client(adapter, region)
        configurations: list[dict[str, Any]] = []
        token: Optional[str] = None
        while True:
            request: dict[str, Any] = {"Bucket": bucket}
            if token:
                request["ContinuationToken"] = token
            response = client.list_bucket_intelligent_tiering_configurations(**request)
            for item in response.get("IntelligentTieringConfigurationList", []) or []:
                prefix, tags = _parse_intelligent_tiering_filter(item.get("Filter"))
                tierings = [
                    {
                        "access_tier": tiering.get("AccessTier"),
                        "days": tiering.get("Days"),
                    }
                    for tiering in item.get("Tierings", []) or []
                ]
                configurations.append(
                    {
                        "id": item.get("Id"),
                        "status": item.get("Status"),
                        "prefix": prefix,
                        "tags": tags,
                        "tierings": tierings,
                    }
                )
            if not response.get("IsTruncated"):
                break
            token = response.get("NextContinuationToken")
            if not token:
                break
        return {"status": "usable", "configurations": configurations, "error": None}
    except ClientError as exc:
        code = _client_error_code(exc)
        status = "access_denied" if "AccessDenied" in code or "Unauthorized" in code else "error"
        return {"status": status, "configurations": None, "error": str(exc)}
    except Exception as exc:
        return {"status": "error", "configurations": None, "error": str(exc)}
