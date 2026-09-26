"""Offline S3 CUR source and signal foundations.

This module deliberately has no boto3 dependency.  It reads the existing CUR
Parquet cache and keeps the S3-specific interpretation in one registry so a
new AWS usage type can be added without changing the aggregation loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from pricing.cur.reader import parquet_glob


ARN_PREFIX = "arn:aws:s3:::"
STORAGE_OVERHEAD_BYTES = 32768


@dataclass(frozen=True)
class UsageTypeRegistryRow:
    """Describe one usage-type/operation registry rule."""

    usage_type_suffix: str
    operation: Optional[str]
    category: str
    storage_class: Optional[str]
    retrieval_speed: Optional[str]
    unit: Optional[str]
    verified: bool = True
    family: Optional[str] = None
    source: str = "aws_docs"
    retrieval_class: Optional[str] = None


@dataclass(frozen=True)
class UsageTypeClassification(UsageTypeRegistryRow):
    """A registry row annotated with the raw usage type and operation."""

    raw_usage_type: str = ""
    raw_operation: str = ""


@dataclass(frozen=True)
class Coverage:
    """Per-bucket storage-line day membership used for safe normalization."""

    bucket: str
    days: frozenset[str]

    @property
    def count(self) -> int:
        """Return the number of covered days, including zero for no coverage."""

        return len(self.days)


def _row(
    suffix: str,
    operation: Optional[str],
    category: str,
    storage_class: Optional[str],
    speed: Optional[str],
    unit: Optional[str],
    *,
    verified: bool = True,
    family: Optional[str] = None,
    source: str = "aws_docs",
    retrieval_class: Optional[str] = None,
) -> UsageTypeRegistryRow:
    """Build one immutable registry row from the compact table notation."""

    return UsageTypeRegistryRow(
        suffix,
        operation,
        category,
        storage_class,
        speed,
        unit,
        verified,
        family,
        source,
        retrieval_class,
    )


# These meanings are intentionally kept next to the registry.  They prevent a
# future addition from being classified as ``other`` just because its price is
# outside the V1 optimizer model.
CATEGORY_MEANINGS = {
    "storage_gb_month": "retained bytes priced by storage class",
    "storage_padding_gb_month": "small-object 128 KiB billing overhead observed in CUR",
    "storage_staging_gb_month": "temporary archive staging storage",
    "storage_overhead_gb_month": "archive index overhead kept separate from data bytes",
    "restore_copy_gb_month": "temporary restored-copy storage",
    "request": "API request count, split into access families",
    "transition": "lifecycle transition request count",
    "transition_or_restore_ambiguous": "empty Tier3 operation that cannot distinguish a transition from a restore",
    "transition_generic": "empty Tier4 operation with no destination class",
    "restore_request": "archive restore request count by speed",
    "retrieval_gb": "archive or IA bytes retrieved",
    "early_delete_gb_hours": "minimum-duration charge measured in GB-hours",
    "select_bytes": "S3 Select bytes scanned or returned",
    "batch_operations": "S3 Batch Operations jobs and object operations",
    "overwrite_gb": "bytes overwritten by CopyObject or PutObject",
    "deleted_gb": "bytes deleted by storage class",
    "data_transfer_in_gb": "bytes transferred into S3",
    "data_transfer_regional_gb": "same-region or regional transfer bytes",
    "data_transfer_other_gb": "transfer bytes outside the regional/inbound families",
    "monitoring_other": "non-optimizer monitoring, metadata, inventory, or account usage",
    "it_monitoring_objects": "Intelligent-Tiering monitored object count",
    "express_one_zone": "S3 Express One Zone usage kept distinct from V1 classes",
    "s3_tables": "S3 Tables storage, requests, and compaction usage",
    "annotation": "S3 annotation storage, requests, and processing",
    "other": "unknown usage retained for unmapped-usage diagnostics",
}


def _official_units(unit: str, *suffixes: str) -> dict[str, str]:
    """Build a compact unit table from the verbatim AWS suffix groups."""

    return {suffix: unit for suffix in suffixes}


_OFFICIAL_UNITS = {
    **_official_units("GB", "AWS-In-ABytes", "AWS-In-ABytes-T1", "AWS-In-ABytes-T2", "AWS-In-Bytes", "AWS-Out-ABytes", "AWS-Out-ABytes-T1", "AWS-Out-ABytes-T2", "AWS-Out-Bytes", "Bulk-Retrieval-Bytes", "BytesDeleted-GDA", "BytesDeleted-GIR", "BytesDeleted-GLACIER", "BytesDeleted-INT", "BytesDeleted-RRS", "BytesDeleted-SIA", "BytesDeleted-STANDARD", "BytesDeleted-ZIA", "C3DataTransfer-In-Bytes", "C3DataTransfer-Out-Bytes", "CloudFront-In-Bytes", "CloudFront-Out-Bytes", "DataTransfer-In-Bytes", "DataTransfer-Out-Bytes", "DataTransfer-Regional-Bytes", "Expedited-Retrieval-Bytes", "MRAP-Out-Bytes", "MRAP-In-Bytes", "S3RTC-In-Bytes", "S3RTC-Out-Bytes", "S3DSSE-In-Bytes", "S3DSSE-Out-Bytes", "S3G-DataTransfer-In-Bytes", "S3G-DataTransfer-Out-Bytes", "OverwriteBytes-Copy-GDA", "OverwriteBytes-Copy-GIR", "OverwriteBytes-Copy-GLACIER", "OverwriteBytes-Copy-INT", "OverwriteBytes-Copy-RRS", "OverwriteBytes-Copy-SIA", "OverwriteBytes-Copy-STANDARD", "OverwriteBytes-Copy-ZIA", "OverwriteBytes-Put-GDA", "OverwriteBytes-Put-GIR", "OverwriteBytes-Put-GLACIER", "OverwriteBytes-Put-INT", "OverwriteBytes-Put-RRS", "OverwriteBytes-Put-SIA", "OverwriteBytes-Put-STANDARD", "OverwriteBytes-Put-ZIA", "Select-Returned-Bytes", "Select-Returned-GIR-Bytes", "Select-Returned-INT-Bytes", "Select-Returned-SIA-Bytes", "Select-Returned-ZIA-Bytes", "Select-Scanned-Bytes", "Select-Scanned-GIR-Bytes", "Select-Scanned-INT-Bytes", "Select-Scanned-SIA-Bytes", "Select-Scanned-ZIA-Bytes", "Standard-Retrieval-Bytes", "Retrieval-GIR", "Retrieval-SIA", "Retrieval-XZ", "Retrieval-ZIA", "Tables-ProcessedBytes", "Upload-XZ"),
    **_official_units("Count", "BatchOperations-Jobs", "BatchOperations-Objects", "Requests-Annotation-Tier1", "Requests-Annotation-Tier2", "Requests-GDA-Tier1", "Requests-GDA-Tier2", "Requests-GDA-Tier3", "Requests-GDA-Tier5", "Requests-GIR-Tier1", "Requests-GIR-Tier2", "Requests-GLACIER-Tier1", "Requests-GLACIER-Tier2", "Requests-INT-Tier1", "Requests-INT-Tier2", "Requests-SIA-Tier1", "Requests-SIA-Tier2", "Requests-Tier1", "Requests-Tier2", "Requests-Tier3", "Requests-Tier4", "Requests-Tier5", "Requests-Tier6", "Requests-Tier8", "Requests-XZ-Tier1", "Requests-XZ-Tier2", "Requests-ZIA-Tier1", "Requests-ZIA-Tier2", "StorageObjectCount", "Tables-Requests-Tier1", "Tables-Requests-Tier2"),
    **_official_units("GB-Hours", "EarlyDelete-ByteHrs", "EarlyDelete-GDA", "EarlyDelete-GIR", "EarlyDelete-GIR-SmObjects", "EarlyDelete-SIA", "EarlyDelete-SIA-SmObjects", "EarlyDelete-ZIA", "EarlyDelete-ZIA-SmObjects"),
    **_official_units("Bucket", "Global-Bucket-Hrs-FreeTier", "Global-Bucket-Hrs"),
    **_official_units("Objects", "Inventory-ObjectsListed", "Monitoring-Automation-INT", "StorageAnalytics-ObjCount", "StorageLens-ObjCount", "StorageLensFreeTier-ObjCount", "Tables-CompactedObjects", "Tables-SortCompactedObjects-", "Tables-MonitoredObjects"),
    **_official_units("Updates", "Metadata-Updates"),
    **_official_units("Tag-Hours", "TagStorage-TagHrs"),
    **_official_units("GB-Month", "Annotation-TimedStorage-ByteHrs", "Tables-TimedStorage-ByteHrs", "TimedStorage-ByteHrs", "TimedStorage-GDA-ByteHrs", "TimedStorage-GDA-Staging", "TimedStorage-GIR-ByteHrs", "TimedStorage-GIR-SmObjects", "TimedStorage-GlacierByteHrs", "TimedStorage-GlacierStaging", "TimedStorage-INT-FA-ByteHrs", "TimedStorage-INT-IA-ByteHrs", "TimedStorage-INT-AA-ByteHrs", "TimedStorage-INT-AIA-ByteHrs", "TimedStorage-INT-DAA-ByteHrs", "TimedStorage-RRS-ByteHrs", "TimedStorage-SIA-ByteHrs", "TimedStorage-SIA-SmObjects", "TimedStorage-XZ-ByteHrs", "TimedStorage-ZIA-ByteHrs", "TimedStorage-ZIA-SmObjects"),
}
_OFFICIAL_UNITS["Metadata-Annotation-Processed-Bytes"] = "GB"

_STORAGE_CLASSES = {
    "TimedStorage-ByteHrs": "STANDARD",
    "TimedStorage-GDA-ByteHrs": "DEEP_ARCHIVE",
    "TimedStorage-GIR-ByteHrs": "GLACIER_IR",
    "TimedStorage-GIR-SmObjects": "GLACIER_IR",
    "TimedStorage-GlacierByteHrs": "GLACIER",
    "TimedStorage-GlacierStaging": "GLACIER",
    "TimedStorage-GDA-Staging": "DEEP_ARCHIVE",
    "TimedStorage-INT-FA-ByteHrs": "INTELLIGENT_TIERING",
    "TimedStorage-INT-IA-ByteHrs": "INTELLIGENT_TIERING",
    "TimedStorage-INT-AA-ByteHrs": "INTELLIGENT_TIERING",
    "TimedStorage-INT-AIA-ByteHrs": "INTELLIGENT_TIERING",
    "TimedStorage-INT-DAA-ByteHrs": "INTELLIGENT_TIERING",
    "TimedStorage-RRS-ByteHrs": "RRS",
    "TimedStorage-SIA-ByteHrs": "STANDARD_IA",
    "TimedStorage-SIA-SmObjects": "STANDARD_IA",
    "TimedStorage-XZ-ByteHrs": "EXPRESS_ONE_ZONE",
    "TimedStorage-ZIA-ByteHrs": "ONEZONE_IA",
    "TimedStorage-ZIA-SmObjects": "ONEZONE_IA",
}
for _suffix, _class in {
    "BytesDeleted-GDA": "DEEP_ARCHIVE", "BytesDeleted-GIR": "GLACIER_IR", "BytesDeleted-GLACIER": "GLACIER", "BytesDeleted-INT": "INTELLIGENT_TIERING", "BytesDeleted-RRS": "RRS", "BytesDeleted-SIA": "STANDARD_IA", "BytesDeleted-STANDARD": "STANDARD", "BytesDeleted-ZIA": "ONEZONE_IA",
    "OverwriteBytes-Copy-GDA": "DEEP_ARCHIVE", "OverwriteBytes-Copy-GIR": "GLACIER_IR", "OverwriteBytes-Copy-GLACIER": "GLACIER", "OverwriteBytes-Copy-INT": "INTELLIGENT_TIERING", "OverwriteBytes-Copy-RRS": "RRS", "OverwriteBytes-Copy-SIA": "STANDARD_IA", "OverwriteBytes-Copy-STANDARD": "STANDARD", "OverwriteBytes-Copy-ZIA": "ONEZONE_IA", "OverwriteBytes-Put-GDA": "DEEP_ARCHIVE", "OverwriteBytes-Put-GIR": "GLACIER_IR", "OverwriteBytes-Put-GLACIER": "GLACIER", "OverwriteBytes-Put-INT": "INTELLIGENT_TIERING", "OverwriteBytes-Put-RRS": "RRS", "OverwriteBytes-Put-SIA": "STANDARD_IA", "OverwriteBytes-Put-STANDARD": "STANDARD", "OverwriteBytes-Put-ZIA": "ONEZONE_IA",
}.items():
    _STORAGE_CLASSES[_suffix] = _class

_REQUEST_CLASSES = {
    "Requests-GDA-Tier1": "DEEP_ARCHIVE", "Requests-GDA-Tier2": "DEEP_ARCHIVE", "Requests-GDA-Tier3": "DEEP_ARCHIVE", "Requests-GDA-Tier5": "DEEP_ARCHIVE", "Requests-GIR-Tier1": "GLACIER_IR", "Requests-GIR-Tier2": "GLACIER_IR", "Requests-GLACIER-Tier1": "GLACIER", "Requests-GLACIER-Tier2": "GLACIER", "Requests-INT-Tier1": "INTELLIGENT_TIERING", "Requests-INT-Tier2": "INTELLIGENT_TIERING", "Requests-SIA-Tier1": "STANDARD_IA", "Requests-SIA-Tier2": "STANDARD_IA", "Requests-XZ-Tier1": "EXPRESS_ONE_ZONE", "Requests-XZ-Tier2": "EXPRESS_ONE_ZONE", "Requests-ZIA-Tier1": "ONEZONE_IA", "Requests-ZIA-Tier2": "ONEZONE_IA",
}

_TRANSFER_IN = {"AWS-In-ABytes", "AWS-In-ABytes-T1", "AWS-In-ABytes-T2", "AWS-In-Bytes", "CloudFront-In-Bytes", "DataTransfer-In-Bytes", "MRAP-In-Bytes", "S3DSSE-In-Bytes"}
_TRANSFER_REGIONAL = {"C3DataTransfer-In-Bytes", "C3DataTransfer-Out-Bytes", "DataTransfer-Regional-Bytes"}
_TRANSFER_OTHER = {"AWS-Out-ABytes", "AWS-Out-ABytes-T1", "AWS-Out-ABytes-T2", "AWS-Out-Bytes", "CloudFront-Out-Bytes", "DataTransfer-Out-Bytes", "MRAP-Out-Bytes", "S3DSSE-Out-Bytes", "S3G-DataTransfer-In-Bytes", "S3G-DataTransfer-Out-Bytes", "S3RTC-In-Bytes", "S3RTC-Out-Bytes"}
_SELECT = {suffix for suffix in _OFFICIAL_UNITS if suffix.startswith("Select-")}
_EXPRESS = {"Requests-XZ-Tier1", "Requests-XZ-Tier2", "Retrieval-XZ", "TimedStorage-XZ-ByteHrs", "Upload-XZ"}
_TABLES = {suffix for suffix in _OFFICIAL_UNITS if suffix.startswith("Tables-")}
_ANNOTATION = {"Annotation-TimedStorage-ByteHrs", "Requests-Annotation-Tier1", "Requests-Annotation-Tier2", "Metadata-Annotation-Processed-Bytes"}
_MONITORING = {"Global-Bucket-Hrs-FreeTier", "Global-Bucket-Hrs", "Inventory-ObjectsListed", "Metadata-Updates", "Monitoring-Automation-INT", "StorageAnalytics-ObjCount", "StorageLens-ObjCount", "StorageLensFreeTier-ObjCount", "StorageObjectCount", "TagStorage-TagHrs"}
_CAPTURED_SUFFIXES = {
    "Global-Bucket-Hrs-FreeTier", "Monitoring-Automation-INT", "Requests-Annotation-Tier1",
    "Requests-GIR-Tier2", "Requests-GLACIER-Tier2", "Requests-INT-Tier2",
    "Requests-SIA-Tier1", "Requests-SIA-Tier2", "Requests-Tier1", "Requests-Tier2",
    "Requests-Tier3", "Requests-Tier4", "Requests-ZIA-Tier1", "Requests-ZIA-Tier2",
    "Retrieval-GIR", "Retrieval-SIA", "Retrieval-ZIA", "TimedStorage-ByteHrs",
    "TimedStorage-GIR-ByteHrs", "TimedStorage-GlacierByteHrs", "TimedStorage-INT-FA-ByteHrs",
    "TimedStorage-INT-IA-ByteHrs", "TimedStorage-SIA-ByteHrs", "TimedStorage-ZIA-ByteHrs",
}
_VERIFIED_GENERIC_SUFFIXES = {
    "Global-Bucket-Hrs-FreeTier", "Requests-GIR-Tier2", "Requests-GLACIER-Tier2",
    "Requests-INT-Tier1", "Requests-INT-Tier2", "Requests-SIA-Tier1", "Requests-SIA-Tier2",
    "Requests-Tier1", "Requests-Tier2", "Requests-ZIA-Tier1", "Requests-ZIA-Tier2",
}


def _official_category(suffix: str) -> tuple[str, Optional[str], Optional[str]]:
    """Return category/class/speed for one official suffix."""

    if suffix in _STORAGE_CLASSES:
        if "SmObjects" in suffix:
            return "storage_padding_gb_month", _STORAGE_CLASSES[suffix], None
        if suffix.endswith("-Staging"):
            return "storage_staging_gb_month", _STORAGE_CLASSES[suffix], None
        if suffix.startswith("TimedStorage-"):
            return "storage_gb_month", _STORAGE_CLASSES[suffix], None
        return ("overwrite_gb" if suffix.startswith("OverwriteBytes-") else "deleted_gb", _STORAGE_CLASSES[suffix], None)
    if suffix.startswith("EarlyDelete-"):
        token = suffix.split("-")[1]
        return "early_delete_gb_hours", {"ByteHrs": "GLACIER", "GDA": "DEEP_ARCHIVE", "GIR": "GLACIER_IR", "SIA": "STANDARD_IA", "ZIA": "ONEZONE_IA"}.get(token), None
    if suffix in _REQUEST_CLASSES or suffix in {"Requests-Tier1", "Requests-Tier2", "Requests-Tier3", "Requests-Tier4", "Requests-Tier5", "Requests-Tier6", "Requests-Tier8"}:
        speed = {"Requests-GDA-Tier3": "standard", "Requests-GDA-Tier5": "bulk", "Requests-Tier5": "bulk", "Requests-Tier6": "expedited"}.get(suffix)
        storage_class = _REQUEST_CLASSES.get(suffix, "STANDARD")
        if suffix in {"Requests-Tier3", "Requests-Tier4"}:
            return "transition", None, None
        if suffix in {"Requests-GDA-Tier3", "Requests-GDA-Tier5", "Requests-Tier5", "Requests-Tier6"}:
            return "restore_request", storage_class if suffix.startswith("Requests-GDA") else "GLACIER", speed
        return "request", storage_class, None
    if suffix in {"Standard-Retrieval-Bytes", "Bulk-Retrieval-Bytes", "Expedited-Retrieval-Bytes"}:
        return "retrieval_gb", None, suffix.split("-")[0].lower()
    if suffix in {"Retrieval-SIA", "Retrieval-ZIA", "Retrieval-GIR"}:
        return "retrieval_gb", {"Retrieval-SIA": "STANDARD_IA", "Retrieval-ZIA": "ONEZONE_IA", "Retrieval-GIR": "GLACIER_IR"}[suffix], "instant"
    if suffix == "Retrieval-XZ":
        return "express_one_zone", "EXPRESS_ONE_ZONE", "instant"
    if suffix in _SELECT:
        return "select_bytes", None, None
    if suffix.startswith("BatchOperations-"):
        return "batch_operations", None, None
    if suffix in _TRANSFER_IN:
        return "data_transfer_in_gb", None, None
    if suffix in _TRANSFER_REGIONAL:
        return "data_transfer_regional_gb", None, None
    if suffix in _TRANSFER_OTHER:
        return "data_transfer_other_gb", None, None
    if suffix in _MONITORING:
        if suffix == "Monitoring-Automation-INT":
            return "it_monitoring_objects", None, None
        return "monitoring_other", None, None
    if suffix in _EXPRESS:
        return "express_one_zone", "EXPRESS_ONE_ZONE", None
    if suffix in _TABLES:
        return "s3_tables", None, None
    if suffix in _ANNOTATION:
        return "annotation", None, None
    return "monitoring_other", None, None


def _official_rows() -> list[UsageTypeRegistryRow]:
    """Create one registry row for every unique AWS suffix in the source table."""

    rows = []
    for suffix, unit in _OFFICIAL_UNITS.items():
        category, storage_class, speed = _official_category(suffix)
        source = "captured" if suffix in _CAPTURED_SUFFIXES else "aws_docs"
        rows.append(_row(suffix, None, category, storage_class, speed, unit, verified=suffix in _VERIFIED_GENERIC_SUFFIXES, source=source))
    return rows


# Operation-specific captured rows precede the official generic rows.  AWS
# reuses several suffixes for storage overhead and restores, so the operation
# is the only safe discriminator for those captured CUR lines.
_CAPTURED_ROWS = [
    _row("Global-Bucket-Hrs-FreeTier", "GeneralPurposeBuckets", "monitoring_other", None, None, "Bucket", source="captured"),
    _row("TimedStorage-ByteHrs", "StandardStorage", "storage_gb_month", "STANDARD", None, "GB-Month", source="captured"),
    _row("TimedStorage-ByteHrs", "GlacierS3ObjectOverhead", "storage_overhead_gb_month", "GLACIER", None, "GB-Month", source="captured"),
    _row("TimedStorage-ByteHrs", "DeepArchiveS3ObjectOverhead", "storage_overhead_gb_month", "DEEP_ARCHIVE", None, "GB-Month", source="captured"),
    _row("TimedStorage-ByteHrs", "RestoreObject", "restore_copy_gb_month", "GLACIER", None, "GB-Month", source="captured"),
    _row("TimedStorage-ByteHrs", "DeepArchiveRestoreObject", "restore_copy_gb_month", "DEEP_ARCHIVE", None, "GB-Month", source="captured"),
    _row("TimedStorage-GlacierByteHrs", "GlacierStorage", "storage_gb_month", "GLACIER", None, "GB-Month", source="captured"),
    _row("TimedStorage-GlacierByteHrs", "GlacierObjectOverhead", "storage_overhead_gb_month", "GLACIER", None, "GB-Month", source="captured"),
    _row("TimedStorage-SIA-ByteHrs", "StandardIAStorage", "storage_gb_month", "STANDARD_IA", None, "GB-Month", source="captured"),
    _row("TimedStorage-ZIA-ByteHrs", "OneZoneIAStorage", "storage_gb_month", "ONEZONE_IA", None, "GB-Month", source="captured"),
    _row("TimedStorage-GIR-ByteHrs", "GlacierInstantRetrievalStorage", "storage_gb_month", "GLACIER_IR", None, "GB-Month", source="captured"),
    _row("TimedStorage-INT-FA-ByteHrs", "IntelligentTieringFAStorage", "storage_gb_month", "INTELLIGENT_TIERING", None, "GB-Month", source="captured"),
    _row("TimedStorage-INT-IA-ByteHrs", "IntelligentTieringIAStorage", "storage_gb_month", "INTELLIGENT_TIERING", None, "GB-Month", source="captured"),
    _row("Retrieval-SIA", "GetObject", "retrieval_gb", "STANDARD_IA", "instant", "GB", source="captured"),
    _row("Retrieval-ZIA", "GetObject", "retrieval_gb", "ONEZONE_IA", "instant", "GB", source="captured"),
    _row("Retrieval-GIR", "GetObject", "retrieval_gb", "GLACIER_IR", "instant", "GB", source="captured"),
    _row("Monitoring-Automation-INT", "IntelligentTieringStorage", "it_monitoring_objects", None, None, None, source="captured"),
    _row("Requests-Annotation-Tier1", "ListObjectAnnotations", "annotation", None, None, "Count", source="captured"),
    _row("Requests-Tier3", "S3-GDATransition", "transition", "DEEP_ARCHIVE", None, "Count", source="captured"),
    _row("Requests-Tier3", "S3-GlacierTransition", "transition", "GLACIER", None, "Count", source="captured"),
    _row("Requests-Tier3", "RestoreObject", "restore_request", "GLACIER", "standard", "Count", source="captured"),
    _row("Requests-Tier4", "S3-SIATransition", "transition", "STANDARD_IA", None, "Count", source="captured"),
    _row("Requests-Tier4", "S3-ZIATransition", "transition", "ONEZONE_IA", None, "Count", source="captured"),
    _row("Requests-Tier4", "S3-GIRTransition", "transition", "GLACIER_IR", None, "Count", source="captured"),
    _row("Requests-Tier4", "S3-INTTransition", "transition", "INTELLIGENT_TIERING", None, "Count", source="captured"),
]


# This is the source of truth for CUR interpretation.  Rules with operation
# None are intentionally broad: AWS uses one usage type for several operations
# and line_item_operation is the reliable discriminator for those rows.
USAGE_TYPE_REGISTRY: list[UsageTypeRegistryRow] = _CAPTURED_ROWS + _official_rows()


DATA_READ_OPERATIONS = frozenset(
    {"GetObject", "HeadObject", "SelectObjectContent", "GetObjectTagging", "GetObjectAttributes"}
)
DATA_WRITE_OPERATIONS = frozenset(
    {"PutObject", "CopyObject", "PostObject", "UploadPart", "CompleteMultipartUpload", "InitiateMultipartUpload"}
)
LIST_OPERATIONS = frozenset({"ListBucket", "ListBucketVersions", "ListMultipartUploads"})


def classify_operation(operation: Optional[str]) -> str:
    """Return the §2.3 family, or ``tier_unsplit`` for an empty operation."""

    operation = (operation or "").strip()
    if not operation:
        # This fallback is tier-agnostic because window_totals keeps Tier1 and Tier2 separate.
        return "tier_unsplit"
    if operation in DATA_READ_OPERATIONS:
        return "data_read"
    if operation in DATA_WRITE_OPERATIONS:
        return "data_write"
    if operation in LIST_OPERATIONS:
        return "list"
    if operation in {"RestoreObject", "DeepArchiveRestoreObject"}:
        return "restore"
    if operation.startswith("S3-") and operation.endswith("Transition"):
        return "transition"
    return "config"


def _usage_suffix(usage_type: Optional[str]) -> str:
    """Return the registry suffix after removing an optional region prefix."""

    value = str(usage_type or "")
    for row in USAGE_TYPE_REGISTRY:
        if value == row.usage_type_suffix or value.endswith("-" + row.usage_type_suffix):
            return row.usage_type_suffix
    return value


def _registry_match(suffix: str, operation: str) -> Optional[UsageTypeRegistryRow]:
    """Select the most specific registry row for a suffix and operation."""

    exact = [row for row in USAGE_TYPE_REGISTRY if row.usage_type_suffix == suffix and row.operation == operation]
    if exact:
        return exact[0]
    if not operation and suffix in {"Requests-Tier3", "Requests-Tier4"}:
        category = "transition_or_restore_ambiguous" if suffix == "Requests-Tier3" else "transition_generic"
        return _row(suffix, "", category, None, None, "Count", verified=False)
    generic = [row for row in USAGE_TYPE_REGISTRY if row.usage_type_suffix == suffix and row.operation is None]
    if generic:
        return generic[0]
    return None


def normalize_usage_type(
    usage_type: Optional[str],
    operation: Optional[str],
    pricing_unit: Optional[str] = None,
) -> UsageTypeClassification:
    """Map a raw CUR pair to a registry classification.

    Unknown pairs return category ``other`` and preserve the raw values.  A
    NULL pricing unit uses the registry unit; it never invents a unit for an
    unknown usage type.
    """

    raw_usage_type = str(usage_type or "")
    raw_operation = str(operation or "")
    suffix = _usage_suffix(raw_usage_type)
    row = _registry_match(suffix, raw_operation)
    if row is None:
        row = _row(suffix, None, "other", None, None, None, verified=False, family="other")
    family = row.family
    if row.category == "request":
        family = row.family or classify_operation(raw_operation)
        if suffix == "Requests-Tier8":
            family = "config"
    elif row.category in {"transition", "restore_request", "restore_copy_gb_month"}:
        family = classify_operation(raw_operation)
    if suffix == "Requests-Tier3" and raw_operation == "DeepArchiveRestoreObject":
        row = _row(suffix, raw_operation, "restore_request", "DEEP_ARCHIVE", "standard", row.unit, verified=False)
        family = "restore"
    if row.category == "retrieval_gb" and suffix in {"Standard-Retrieval-Bytes", "Bulk-Retrieval-Bytes", "Expedited-Retrieval-Bytes"}:
        if raw_operation == "RestoreObject":
            storage_class = "GLACIER"
            retrieval_class = "glacier"
        elif raw_operation == "DeepArchiveRestoreObject":
            storage_class = "DEEP_ARCHIVE"
            retrieval_class = "deep_archive"
        else:
            storage_class = None
            retrieval_class = "ambiguous"
        values = dict(row.__dict__)
        values.update(storage_class=storage_class, retrieval_class=retrieval_class)
        row = UsageTypeRegistryRow(**values)
    unit = pricing_unit if pricing_unit not in (None, "") else row.unit
    if row.category == "it_monitoring_objects" and pricing_unit in (None, ""):
        unit = None
    values = dict(row.__dict__)
    values.update(
        unit=unit,
        family=family,
        raw_usage_type=raw_usage_type,
        raw_operation=raw_operation,
    )
    return UsageTypeClassification(**values)


def normalize_bucket_name(resource_id: Optional[str]) -> str:
    """Strip the S3 ARN prefix; return an empty string for a missing id."""

    value = str(resource_id or "")
    return value[len(ARN_PREFIX) :] if value.startswith(ARN_PREFIX) else value


def load_bucket_rows(cache_root: Path, start: str, end: str, grain: str) -> Iterable[dict[str, Any]]:
    """Read grouped S3 CUR rows for ``[start, end)`` from the local cache.

    ``grain`` must be ``daily`` or ``monthly``.  An empty/missing cache returns
    an empty iterable; no AWS or other external service is contacted.
    """

    definitions = {
        "daily": ("resource_daily", "usage_date"),
        "monthly": ("resource_monthly", "billing_month"),
    }
    if grain not in definitions:
        raise ValueError("grain must be 'daily' or 'monthly'")
    dataset, date_column = definitions[grain]
    dataset_root = cache_root / dataset
    if not dataset_root.exists() or not any(dataset_root.glob("year=*/month=*/*.parquet")):
        return []
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("duckdb is required to query the CUR cache") from exc

    sql = f"""
        SELECT
          {date_column} AS period,
          line_item_usage_account_id AS account_id,
          line_item_resource_id AS bucket,
          product_region_code AS region,
          line_item_usage_type AS usage_type,
          line_item_operation AS operation,
          pricing_unit,
          SUM(usage_amount) AS usage_amount,
          SUM(unblended_cost) AS unblended_cost,
          SUM(net_amortized_cost) AS net_amortized_cost
        FROM read_parquet(?, hive_partitioning=true, union_by_name=true)
        WHERE service_code = 'AmazonS3'
          AND line_item_line_item_type = 'Usage'
          AND {date_column} >= ?
          AND {date_column} < ?
        GROUP BY 1, 2, 3, 4, 5, 6, 7
        ORDER BY 1, 3, 5, 6
    """
    with duckdb.connect(database=":memory:") as connection:
        glob = parquet_glob(cache_root, dataset)
        schema = connection.execute(
            "DESCRIBE SELECT * FROM read_parquet(?, hive_partitioning=true, union_by_name=true)",
            [glob],
        ).fetchall()
        pricing_unit_expression = "pricing_unit" if any(item[0] == "pricing_unit" for item in schema) else "NULL"
        sql = sql.replace("          pricing_unit,", f"          {pricing_unit_expression} AS pricing_unit,")
        result = connection.execute(sql, [glob, start, end])
        columns = [item[0] for item in result.description]
        rows = []
        for values in result.fetchall():
            row = dict(zip(columns, values))
            row["bucket"] = normalize_bucket_name(row.get("bucket"))
            row["period"] = _period_string(row.get("period"))
            # Keep concise names for the optimizer while retaining the CUR
            # names from §3.3 for callers that pass rows between CUR layers.
            row["line_item_resource_id"] = row["bucket"]
            row["line_item_usage_account_id"] = row["account_id"]
            row["product_region_code"] = row["region"]
            row["line_item_usage_type"] = row["usage_type"]
            row["line_item_operation"] = row["operation"]
            rows.append(row)
        return rows


def _period_string(value: Any) -> str:
    """Convert DuckDB date-like values to ISO day strings."""

    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    return str(value)[:10]


def _value(row: Any, name: str, default: Any = None) -> Any:
    """Read a field from either a mapping row or an attribute row."""

    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def _is_storage_usage(usage_type: Optional[str]) -> bool:
    """Return whether a usage type is a billed TimedStorage line."""

    suffix = _usage_suffix(usage_type)
    return suffix.startswith("TimedStorage-")


def coverage_by_bucket(rows: Iterable[Any], start: str, end: str) -> dict[str, Coverage]:
    """Return storage-line day membership for each bucket in the window."""

    start_day = _period_string(start)
    end_day = _period_string(end)
    result: dict[str, set[str]] = {}
    for row in rows:
        period = _period_string(_value(row, "period"))
        bucket = normalize_bucket_name(_value(row, "bucket", _value(row, "line_item_resource_id")))
        usage_type = _value(row, "usage_type", _value(row, "line_item_usage_type"))
        if start_day <= period < end_day and bucket and _is_storage_usage(usage_type):
            result.setdefault(bucket, set()).add(period)
    return {bucket: Coverage(bucket, frozenset(days)) for bucket, days in result.items()}


def covered_days(
    rows: Iterable[Any],
    start: str,
    end: str,
    bucket: Optional[str] = None,
) -> int:
    """Count covered storage-line days for one bucket, or all buckets.

    No matching storage line returns ``0``; callers must keep that unknown
    result distinct from a bucket with covered days and zero requests.
    """

    coverage = coverage_by_bucket(rows, start, end)
    if bucket is not None:
        return coverage.get(normalize_bucket_name(bucket), Coverage(str(bucket), frozenset())).count
    return sum(item.count for item in coverage.values())


def confidence_for_covered_days(day_count: int) -> str:
    """Return ``low``, ``medium``, or ``high`` using the §2.1 thresholds."""

    if day_count < 30:
        return "low"
    if day_count < 90:
        return "medium"
    return "high"


def _coverage_for_rows(rows: list[Any], coverage: Any) -> tuple[Optional[frozenset[str]], int]:
    """Accept the public coverage forms and return day membership plus count."""

    if isinstance(coverage, Coverage):
        return coverage.days, coverage.count
    if isinstance(coverage, Mapping):
        buckets = {
            normalize_bucket_name(_value(row, "bucket", _value(row, "line_item_resource_id")))
            for row in rows
        }
        item: Any = None
        for bucket in buckets:
            if bucket in coverage:
                item = coverage[bucket]
                break
        if item is None and "days" in coverage:
            item = coverage
        if isinstance(item, Coverage):
            return item.days, item.count
        if isinstance(item, (int, float)):
            return None, int(item)
        if isinstance(item, Mapping):
            days = item.get("days")
            count = item.get("covered_days", len(days or []))
            return (frozenset(str(day) for day in days) if days is not None else None), int(count)
    if isinstance(coverage, int):
        return None, coverage
    return None, 0


def monthly_total(total: Optional[float], day_count: int) -> Optional[float]:
    """Normalize a raw covered-window total to a 30-day month.

    Returns ``None`` when coverage is zero or the input total is unknown.
    """

    if total is None or day_count <= 0:
        return None
    return float(total) * 30.0 / day_count


def _sum_by(values: Mapping[str, float], key: str, amount: Any) -> None:
    """Add a numeric row amount to a class/family accumulator."""

    if amount is not None:
        values[key] = values.get(key, 0.0) + float(amount)


def window_totals(
    bucket_rows: Iterable[Any],
    coverage: Any,
    bucket: Optional[str] = None,
) -> dict[str, Any]:
    """Aggregate raw covered-window usage by family, class, and speed.

    The empty/unknown case is represented by ``covered_days == 0``; numeric
    fields in a known covered window are zero when no row belongs to them.
    """

    rows = list(bucket_rows)
    if bucket is not None:
        wanted = normalize_bucket_name(bucket)
        rows = [
            row
            for row in rows
            if normalize_bucket_name(_value(row, "bucket", _value(row, "line_item_resource_id"))) == wanted
        ]
    covered, day_count = _coverage_for_rows(rows, coverage)
    if covered is not None:
        rows = [row for row in rows if _period_string(_value(row, "period")) in covered]

    totals: dict[str, Any] = {
        "covered_days": day_count,
        "tier1": {"data_read": 0.0, "data_write": 0.0, "list": 0.0, "config": 0.0, "tier_unsplit": 0.0},
        "tier2": {"data_read": 0.0, "data_write": 0.0, "list": 0.0, "config": 0.0, "tier_unsplit": 0.0},
        "requests": {"data_read": 0.0, "data_write": 0.0, "list": 0.0, "config": 0.0, "tier_unsplit": 0.0},
        "retrieval_gb": {},
        "restore_requests": {},
        "transition_requests_by_destination": {},
        "storage_gb_month": {},
        "storage_overhead_gb_month": {},
        "restore_copy_gb_month": {},
        "storage_padding_gb_month": {},
        "storage_staging_gb_month": {},
        "early_delete_gb_hours": {},
        "select_bytes": 0.0,
        "batch_operations": {},
        "overwrite_gb": {},
        "deleted_gb": {},
        "data_transfer_in_gb": 0.0,
        "data_transfer_regional_gb": 0.0,
        "data_transfer_other_gb": 0.0,
        "data_transfer_out_gb": 0.0,
        "monitoring_other": {},
        "it_monitoring_objects": 0.0,
        "express_one_zone": 0.0,
        "s3_tables": {},
        "annotation": {},
        "ambiguous_tier3_activity": False,
        "unmapped_usage_types": set(),
    }
    for row in rows:
        usage_type = _value(row, "usage_type", _value(row, "line_item_usage_type"))
        operation = _value(row, "operation", _value(row, "line_item_operation"))
        classification = normalize_usage_type(usage_type, operation, _value(row, "pricing_unit"))
        amount = _value(row, "usage_amount", 0.0)
        amount = float(amount or 0.0)
        if classification.category == "request":
            family = classification.family or classify_operation(operation)
            totals["requests"][family] = totals["requests"].get(family, 0.0) + amount
            suffix = classification.usage_type_suffix
            tier_key = "tier1" if suffix.endswith("Tier1") else "tier2" if suffix.endswith("Tier2") else None
            if tier_key:
                totals[tier_key][family] = totals[tier_key].get(family, 0.0) + amount
        elif classification.category == "retrieval_gb":
            key = f"{classification.storage_class or 'UNKNOWN'}.{classification.retrieval_speed or 'standard'}"
            _sum_by(totals["retrieval_gb"], key, amount)
        elif classification.category == "restore_request":
            key = f"{classification.storage_class}.{classification.retrieval_speed or 'standard'}"
            _sum_by(totals["restore_requests"], key, amount)
        elif classification.category == "transition":
            _sum_by(totals["transition_requests_by_destination"], classification.storage_class or "UNKNOWN", amount)
        elif classification.category in {"storage_gb_month", "storage_overhead_gb_month", "restore_copy_gb_month", "storage_padding_gb_month", "storage_staging_gb_month", "early_delete_gb_hours"}:
            target = {
                "storage_gb_month": "storage_gb_month",
                "storage_overhead_gb_month": "storage_overhead_gb_month",
                "restore_copy_gb_month": "restore_copy_gb_month",
                "storage_padding_gb_month": "storage_padding_gb_month",
                "storage_staging_gb_month": "storage_staging_gb_month",
                "early_delete_gb_hours": "early_delete_gb_hours",
            }[classification.category]
            _sum_by(totals[target], classification.storage_class or "UNKNOWN", amount)
        elif classification.category == "select_bytes":
            totals["select_bytes"] += amount
        elif classification.category == "batch_operations":
            key = classification.usage_type_suffix.rsplit("-", 1)[-1]
            _sum_by(totals["batch_operations"], key, amount)
        elif classification.category in {"overwrite_gb", "deleted_gb"}:
            _sum_by(totals[classification.category], classification.storage_class or "UNKNOWN", amount)
        elif classification.category in {"data_transfer_in_gb", "data_transfer_regional_gb", "data_transfer_other_gb"}:
            totals[classification.category] += amount
            if any(classification.usage_type_suffix.endswith(suffix) for suffix in ("DataTransfer-Out-Bytes", "AWS-Out-Bytes", "CloudFront-Out-Bytes", "MRAP-Out-Bytes")):
                totals["data_transfer_out_gb"] += amount
        elif classification.category == "express_one_zone":
            totals["express_one_zone"] += amount
        elif classification.category == "it_monitoring_objects":
            if classification.unit is None:
                totals["it_monitoring_objects"] = None
            elif totals["it_monitoring_objects"] is not None:
                totals["it_monitoring_objects"] += amount
        elif classification.category in {"monitoring_other", "s3_tables", "annotation"}:
            _sum_by(totals[classification.category], classification.usage_type_suffix, amount)
        elif classification.category in {"transition_or_restore_ambiguous", "transition_generic"}:
            # A generic Tier3/Tier4 row may be a restore.  It cannot prove
            # inactivity, so the unused check must suppress itself rather than
            # treating this unknown activity as a zero.
            totals["ambiguous_tier3_activity"] = True
        elif classification.category == "other":
            totals["unmapped_usage_types"].add(str(usage_type))
    totals["unmapped_usage_types"] = sorted(totals["unmapped_usage_types"])
    return totals


def _monthly_mapping(values: Mapping[str, float], day_count: int) -> dict[str, Optional[float]]:
    """Normalize every value in a class mapping to a 30-day month."""

    return {key: monthly_total(value, day_count) for key, value in values.items()}


def derive_signals(bucket_rows: Iterable[Any], coverage: Any) -> dict[str, Any]:
    """Derive Phase-1 S3 access, storage, retrieval, and coverage signals.

    With zero covered days every window-derived numeric signal is ``None``;
    this is the absent-is-not-zero rule.  A covered bucket with no matching
    family has a real numeric zero.
    """

    rows = list(bucket_rows)
    totals = window_totals(rows, coverage)
    day_count = totals["covered_days"]
    covered_membership, _ = _coverage_for_rows(rows, coverage)
    first_day = min(covered_membership) if covered_membership else None
    last_day = max(covered_membership) if covered_membership else None
    if day_count == 0:
        return {
            "telemetry_status": "unknown",
            "confidence": "low",
            "cur_days_covered": 0,
            "cur_first_day": None,
            "cur_last_day": None,
            "covered_days": [],
            "cloudwatch_days_covered": None,
            "window_totals": None,
            "monthly_data_read_requests": None,
            "monthly_data_write_requests": None,
            "monthly_list_requests": None,
            "monthly_config_requests": None,
            "monthly_tier1_requests": None,
            "monthly_tier2_requests": None,
            "monthly_access_requests_by_family": None,
            "requests_per_object_per_month": None,
            "retrieval_gb_per_month_by_class": None,
            "monthly_retrieval_gb_by_class": None,
            "retrieval_ratio_by_class": None,
            "monthly_storage_gb_by_class": None,
            "transition_requests_by_destination": None,
            "transition_counts_by_destination": None,
            "restore_requests_by_class": None,
            "monthly_restore_requests": None,
            "restore_copy_gb_month_by_class": None,
            "restore_copy_gb_month": None,
            "config_requests": None,
            "object_count_crosscheck": None,
            "unmapped_usage_types": totals["unmapped_usage_types"],
            "ambiguous_tier3_activity": totals["ambiguous_tier3_activity"],
        }

    requests = totals["requests"]
    monthly_read = monthly_total(requests["data_read"], day_count)
    monthly_write = monthly_total(requests["data_write"], day_count)
    monthly_list = monthly_total(requests["list"], day_count)
    monthly_config = monthly_total(requests["config"], day_count)
    monthly_tier1 = monthly_total(sum(totals["tier1"].values()), day_count)
    monthly_tier2 = monthly_total(sum(totals["tier2"].values()), day_count)
    storage = _monthly_mapping(totals["storage_gb_month"], day_count)
    retrieval = _monthly_mapping(totals["retrieval_gb"], day_count)
    restore_copy = _monthly_mapping(totals["restore_copy_gb_month"], day_count)
    access = None
    if monthly_read is not None and monthly_write is not None and monthly_list is not None:
        access = monthly_read + monthly_write + monthly_list
    object_count = _first_number(rows, ("total_objects", "object_count"))
    requests_per_object = access / object_count if access is not None and object_count not in (None, 0) else None
    storage_by_class = {key: value for key, value in storage.items() if value is not None}
    retrieval_by_class: dict[str, float] = {}
    for key, value in retrieval.items():
        storage_class = str(key).split(".", 1)[0]
        if value is not None:
            retrieval_by_class[storage_class] = retrieval_by_class.get(storage_class, 0.0) + value
    retrieval_ratio = {
        storage_class: (
            value / storage_by_class[storage_class]
            if storage_by_class.get(storage_class) not in (None, 0)
            else None
        )
        for storage_class, value in retrieval_by_class.items()
    }
    overhead = _monthly_mapping(totals["storage_overhead_gb_month"], day_count)
    object_crosscheck = {
        key: value * (2**30) / STORAGE_OVERHEAD_BYTES
        for key, value in overhead.items()
        if value is not None
    }
    return {
        "telemetry_status": "usable",
        "confidence": confidence_for_covered_days(day_count),
        "cur_days_covered": day_count,
        "cur_first_day": first_day,
        "cur_last_day": last_day,
        "covered_days": sorted(covered_membership),
        "cloudwatch_days_covered": None,
        "window_totals": totals,
        "monthly_data_read_requests": monthly_read,
        "monthly_data_write_requests": monthly_write,
        "monthly_list_requests": monthly_list,
        "monthly_config_requests": monthly_config,
        "monthly_tier1_requests": monthly_tier1,
        "monthly_tier2_requests": monthly_tier2,
        "monthly_access_requests_by_family": {
            "data_read": monthly_read,
            "data_write": monthly_write,
            "list": monthly_list,
        },
        "requests_per_object_per_month": requests_per_object,
        "retrieval_gb_per_month_by_class": retrieval,
        "monthly_retrieval_gb_by_class": retrieval,
        "retrieval_ratio_by_class": retrieval_ratio,
        "monthly_storage_gb_by_class": storage,
        "monthly_storage_padding_gb_month_by_class": _monthly_mapping(totals["storage_padding_gb_month"], day_count),
        "monthly_storage_staging_gb_month_by_class": _monthly_mapping(totals["storage_staging_gb_month"], day_count),
        "transition_requests_by_destination": _monthly_mapping(totals["transition_requests_by_destination"], day_count),
        "transition_counts_by_destination": _monthly_mapping(totals["transition_requests_by_destination"], day_count),
        "restore_requests_by_class": _monthly_mapping(totals["restore_requests"], day_count),
        "monthly_restore_requests": _monthly_mapping(totals["restore_requests"], day_count),
        "restore_copy_gb_month_by_class": restore_copy,
        "restore_copy_gb_month": sum(value for value in restore_copy.values() if value is not None),
        "config_requests": monthly_config,
        "object_count_crosscheck": object_crosscheck,
        "monthly_early_delete_gb_hours_by_class": _monthly_mapping(totals["early_delete_gb_hours"], day_count),
        # Compatibility alias; the registry's authoritative unit is GB-Hours.
        "monthly_early_delete_gb_by_class": _monthly_mapping(totals["early_delete_gb_hours"], day_count),
        "monthly_data_transfer_out_gb": monthly_total(totals["data_transfer_out_gb"], day_count),
        "monthly_it_monitoring_objects": monthly_total(totals["it_monitoring_objects"], day_count),
        "ambiguous_tier3_activity": totals["ambiguous_tier3_activity"],
        "unmapped_usage_types": totals["unmapped_usage_types"],
    }


def _first_number(rows: list[Any], names: tuple[str, ...]) -> Optional[float]:
    """Return the first optional object-count value supplied by a row."""

    for row in rows:
        for name in names:
            value = _value(row, name)
            if value is not None:
                return float(value)
    return None
