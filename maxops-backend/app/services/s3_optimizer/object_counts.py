"""CloudWatch storage metrics and conservative object-count estimates."""

from __future__ import annotations

from typing import Any, Mapping, Optional


BYTES_PER_GIB = 2**30
ARCHIVE_OVERHEAD_BYTES = 32 * 1024
LIFECYCLE_FLOOR_BYTES = 128 * 1024

STORAGE_TYPE_TO_CLASS = {
    "StandardStorage": "STANDARD",
    "StandardIAStorage": "STANDARD_IA",
    "OneZoneIAStorage": "ONEZONE_IA",
    "GlacierInstantRetrievalStorage": "GLACIER_IR",
    "GlacierStorage": "GLACIER",
    "DeepArchiveStorage": "DEEP_ARCHIVE",
    "IntelligentTieringStorage": "INTELLIGENT_TIERING",
    "IntelligentTieringFAStorage": "INTELLIGENT_TIERING",
    "IntelligentTieringIAStorage": "INTELLIGENT_TIERING",
    "IntelligentTieringAIAStorage": "INTELLIGENT_TIERING",
    "IntelligentTieringAAStorage": "INTELLIGENT_TIERING",
    "IntelligentTieringDAAStorage": "INTELLIGENT_TIERING",
}
ARCHIVE_OVERHEAD_TYPES = {
    "GlacierObjectOverhead": "GLACIER",
    "DeepArchiveObjectOverhead": "DEEP_ARCHIVE",
    "IntAAObjectOverhead": "INTELLIGENT_TIERING",
    "IntDAAObjectOverhead": "INTELLIGENT_TIERING",
}
PADDING_OVERHEAD_TYPES = {
    "StandardIASizeOverhead": "STANDARD_IA",
    "OneZoneIASizeOverhead": "ONEZONE_IA",
    "GlacierIRSizeOverhead": "GLACIER_IR",
}
STORAGE_CLASSES = (
    "STANDARD",
    "STANDARD_IA",
    "ONEZONE_IA",
    "GLACIER_IR",
    "INTELLIGENT_TIERING",
    "GLACIER",
    "DEEP_ARCHIVE",
)


def _metric_bucket(cloudwatch: Mapping[str, Any], bucket: Optional[str]) -> Mapping[str, Any]:
    """Return one bucket's metric mapping from either accepted fixture form."""

    metrics = cloudwatch.get("metrics", cloudwatch)
    if bucket is not None:
        return metrics.get(bucket, {})
    if "NumberOfObjects" in metrics or any(key in metrics for key in STORAGE_TYPE_TO_CLASS):
        return metrics
    if len(metrics) == 1:
        return next(iter(metrics.values()))
    return {}


def _latest(series: Any) -> Optional[float]:
    """Return the latest datapoint value, or ``None`` for an absent series."""

    if not series:
        return None
    point = series[-1]
    if isinstance(point, Mapping):
        value = point.get("value")
    else:
        value = point
    return None if value is None else float(value)


def _series(metric_bucket: Mapping[str, Any], storage_type: str) -> list[dict[str, Any]]:
    """Normalize a metric series to date/value dictionaries."""

    result = []
    for point in metric_bucket.get(storage_type, []) or []:
        if isinstance(point, Mapping):
            result.append({"date": str(point.get("date", "")), "value": float(point["value"])})
        else:
            result.append({"date": "", "value": float(point)})
    return result


def stored_bytes_by_class(cloudwatch: Mapping[str, Any], bucket: Optional[str] = None) -> dict[str, Optional[float]]:
    """Return latest ``BucketSizeBytes`` by class, preserving missing metrics as ``None``.

    CloudWatch's `StorageType` dimensions are sparse: no datapoint means the
    class is unknown, not empty.  Intelligent-Tiering component dimensions are
    added together only when each present component is known.
    """

    metric_bucket = _metric_bucket(cloudwatch, bucket)
    result = {storage_class: None for storage_class in STORAGE_CLASSES}
    components: dict[str, list[float]] = {}
    for storage_type, storage_class in STORAGE_TYPE_TO_CLASS.items():
        value = _latest(metric_bucket.get(storage_type))
        if value is not None:
            components.setdefault(storage_class, []).append(value)
    for storage_class, values in components.items():
        result[storage_class] = sum(values)
    return result


def overhead_bytes_by_class(cloudwatch: Mapping[str, Any], bucket: Optional[str] = None) -> dict[str, Optional[float]]:
    """Return observed archive-index and small-object padding bytes by class."""

    metric_bucket = _metric_bucket(cloudwatch, bucket)
    result: dict[str, Optional[float]] = {}
    for storage_type, storage_class in {**ARCHIVE_OVERHEAD_TYPES, **PADDING_OVERHEAD_TYPES}.items():
        value = _latest(metric_bucket.get(storage_type))
        if value is not None:
            previous = result.get(storage_class)
            result[storage_class] = (0.0 if previous is None else previous) + value
    return result


def total_objects(cloudwatch: Mapping[str, Any], bucket: Optional[str] = None) -> Optional[float]:
    """Return the latest bucket-wide population count, or ``None`` when absent."""

    return _latest(_metric_bucket(cloudwatch, bucket).get("NumberOfObjects"))


def daily_size_series(
    cloudwatch: Mapping[str, Any], bucket: Optional[str] = None
) -> Any:
    """Return daily stored-byte totals used for the direct-write ingest slope.

    The default return is a bucket-to-series mapping when the fixture contains
    multiple buckets; passing ``bucket`` returns one sorted list.  Overhead
    dimensions are excluded because they are accounting metadata, not retained
    object bytes.  An absent metric returns an empty list, never a zero series.
    """

    metrics = cloudwatch.get("metrics", cloudwatch)
    if bucket is None and not ("NumberOfObjects" in metrics or any(key in metrics for key in STORAGE_TYPE_TO_CLASS)):
        return {name: daily_size_series(cloudwatch, name) for name in metrics}
    metric_bucket = _metric_bucket(cloudwatch, bucket)
    by_date: dict[str, float] = {}
    for storage_type, storage_class in STORAGE_TYPE_TO_CLASS.items():
        for point in _series(metric_bucket, storage_type):
            by_date[point["date"]] = by_date.get(point["date"], 0.0) + point["value"]
    return [{"date": date, "value": by_date[date]} for date in sorted(by_date)]


def ingest_gb_from_size_slope(cloudwatch: Mapping[str, Any], bucket: Optional[str] = None) -> Optional[float]:
    """Estimate positive monthly ingest from a 14-point minimum daily slope.

    The direct-write policy needs a measured growth rate, not a fabricated
    zero: fewer than 14 points or a flat/declining series is unknown.
    """

    series = daily_size_series(cloudwatch, bucket)
    if len(series) < 14:
        return None
    days = len(series) - 1
    slope = (series[-1]["value"] - series[0]["value"]) / days
    return None if slope <= 0 else slope * 30.0 / BYTES_PER_GIB


def object_counts_by_class(cloudwatch: Mapping[str, Any], bucket: Optional[str] = None) -> dict[str, dict[str, Any]]:
    """Estimate population-wide object counts by class from two free metrics.

    Archive index overhead is exactly ``bytes / 32768`` for that archive
    population.  The remaining `NumberOfObjects` population is either assigned
    directly when one non-archive class has bytes (`remainder_derived`) or
    distributed by byte share (`byte_share_estimate`).  These counts include
    current objects, noncurrent versions, delete markers, and MPU parts; the
    metrics cannot isolate current versions, so every non-overhead result is an
    estimate rather than current-object ground truth.
    """

    stored = stored_bytes_by_class(cloudwatch, bucket)
    metric_bucket = _metric_bucket(cloudwatch, bucket)
    object_total = total_objects(cloudwatch, bucket)
    archive_counts: dict[str, Optional[float]] = {}
    for storage_type, storage_class in ARCHIVE_OVERHEAD_TYPES.items():
        overhead = _latest(metric_bucket.get(storage_type))
        archive_counts[storage_class] = None if overhead is None else overhead / ARCHIVE_OVERHEAD_BYTES

    known_archive = [count for count in archive_counts.values() if count is not None]
    remainder = None if object_total is None else object_total - sum(known_archive)
    non_archive = [
        storage_class
        for storage_class in STORAGE_CLASSES
        if storage_class not in archive_counts and stored.get(storage_class) is not None
    ]
    non_archive_bytes = sum(stored[storage_class] for storage_class in non_archive if stored[storage_class] is not None)
    result: dict[str, dict[str, Any]] = {}
    for storage_class in STORAGE_CLASSES:
        value = stored.get(storage_class)
        if storage_class in archive_counts:
            objects = archive_counts[storage_class]
            method = "overhead_derived" if objects is not None else None
        elif value is None or remainder is None:
            objects = None
            method = None
        elif len(non_archive) == 1:
            objects = remainder
            method = "remainder_derived"
        elif non_archive_bytes:
            objects = remainder * value / non_archive_bytes
            method = "byte_share_estimate"
        else:
            objects = None
            method = None
        if value is None and objects in (None, 0):
            # CloudWatch dimensions are sparse.  Do not serialize an
            # unobserved class as a known zero-population class; that would
            # turn absent telemetry into a false inventory fact.
            continue
        result[storage_class] = {
            "bytes": value,
            "objects": objects,
            "object_count_method": method,
            "avg_object_bytes": None if objects in (None, 0) or value is None else value / objects,
        }
    return result
