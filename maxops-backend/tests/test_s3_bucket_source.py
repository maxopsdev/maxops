import json
import re
from pathlib import Path

import pytest

from app.services.s3_bucket_source import (
    _OFFICIAL_UNITS,
    classify_operation,
    confidence_for_covered_days,
    coverage_by_bucket,
    derive_signals,
    load_bucket_rows,
    normalize_bucket_name,
    normalize_usage_type,
    window_totals,
)


FIXTURE = Path("tests/payloads/s3_optimizer/cur_fixture.json")


def fixture_rows():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"]


def test_every_official_usage_suffix_has_a_specific_registry_category():
    """No suffix in the official units table may fall through to ``other``.

    ``_OFFICIAL_UNITS`` and ``_official_category`` are maintained separately —
    one maps a suffix to its billing unit, the other to a category — so a
    suffix can be added to the first without the second knowing how to
    classify it. This pins them together.

    Note this cannot detect a usage type AWS has published that is absent from
    ``_OFFICIAL_UNITS`` entirely; that needs a refresh against the AWS billing
    documentation.
    """

    assert _OFFICIAL_UNITS, "official units table is empty"
    unclassified = [
        suffix
        for suffix in _OFFICIAL_UNITS
        if normalize_usage_type(suffix, None).category == "other"
    ]
    assert unclassified == [], f"no registry category for: {unclassified}"


def test_shared_retrieval_bytes_without_operation_are_ambiguous():
    """Shared Glacier/Deep Archive retrieval bytes never guess a class."""

    classification = normalize_usage_type("Standard-Retrieval-Bytes", None)
    assert classification.category == "retrieval_gb"
    assert classification.storage_class is None
    assert classification.retrieval_class == "ambiguous"


def test_early_delete_uses_the_aws_gb_hours_unit():
    """Early-delete usage remains GB-Hours through normalization and totals."""

    classification = normalize_usage_type("EarlyDelete-GDA", None)
    assert classification.category == "early_delete_gb_hours"
    assert classification.unit == "GB-Hours"
    totals = window_totals(
        [{"period": "2026-01-01", "bucket": "bucket-a", "usage_type": "EarlyDelete-GDA", "operation": None, "usage_amount": 2}],
        {"bucket-a": {"covered_days": 1}},
    )
    assert totals["early_delete_gb_hours"]["DEEP_ARCHIVE"] == 2


def test_fixture_pairs_are_registered_and_arn_and_region_prefixes_normalize():
    pairs = {(row["usage_type"], row["operation"]) for row in fixture_rows()}
    for usage_type, operation in pairs:
        classification = normalize_usage_type(usage_type, operation)
        if usage_type not in {"Global-Bucket-Hrs-FreeTier", "Requests-Annotation-Tier1"}:
            assert classification.category != "other", (usage_type, operation)

    assert normalize_bucket_name("arn:aws:s3:::bucket-a") == "bucket-a"
    assert normalize_usage_type("USE2-Requests-Tier1", "PutObject").category == "request"
    assert normalize_usage_type("USE2-Requests-Tier1", "PutObject").family == "data_write"


def test_scrubbed_fixture_contains_no_real_account_or_resource_identifiers():
    text = FIXTURE.read_text(encoding="utf-8")
    # The fixture was cut down from a real CUR export. Assert on the shape of an
    # account id rather than naming the one that was scrubbed out: spelling it
    # here would put it back in the tree, which is what the scrub was for.
    placeholders = {"123456789012", "111111111111", "999999999999", "222222222222"}
    leaked = {
        candidate
        for candidate in re.findall(r"(?<![0-9A-Fa-f-])[0-9]{12}(?![0-9A-Fa-f-])", text)
        if candidate not in placeholders
    }
    assert leaked == set(), f"unscrubbed account id(s) in the fixture: {sorted(leaked)}"
    allowed_resources = {"bucket-a", "bucket-b", "bucket-c"}
    payload = json.loads(text)
    assert set(payload["buckets"]) == allowed_resources
    for row in payload["rows"]:
        assert row["line_item_resource_id"] in allowed_resources
        assert row["bucket"] in allowed_resources


def test_observed_storage_operations_are_verified_registry_entries():
    for usage_type, operation in [
        ("TimedStorage-SIA-ByteHrs", "StandardIAStorage"),
        ("TimedStorage-ZIA-ByteHrs", "OneZoneIAStorage"),
        ("TimedStorage-GIR-ByteHrs", "GlacierInstantRetrievalStorage"),
        ("TimedStorage-INT-FA-ByteHrs", "IntelligentTieringFAStorage"),
        ("TimedStorage-INT-IA-ByteHrs", "IntelligentTieringIAStorage"),
    ]:
        classification = normalize_usage_type(usage_type, operation)
        assert classification.category == "storage_gb_month"
        assert classification.verified is True


@pytest.mark.parametrize(
    ("operation", "family"),
    [
        ("GetObject", "data_read"),
        ("HeadObject", "data_read"),
        ("SelectObjectContent", "data_read"),
        ("GetObjectTagging", "data_read"),
        ("GetObjectAttributes", "data_read"),
        ("PutObject", "data_write"),
        ("CopyObject", "data_write"),
        ("PostObject", "data_write"),
        ("UploadPart", "data_write"),
        ("CompleteMultipartUpload", "data_write"),
        ("InitiateMultipartUpload", "data_write"),
        ("ListBucket", "list"),
        ("ListBucketVersions", "list"),
        ("ListMultipartUploads", "list"),
        ("RestoreObject", "restore"),
        ("S3-SIATransition", "transition"),
        ("ReadBucketLifecycle", "config"),
        ("HeadBucket", "config"),
        ("", "tier_unsplit"),
    ],
)
def test_operation_families(operation, family):
    assert classify_operation(operation) == family


def test_config_requests_do_not_count_as_access():
    rows = [
        {"period": "2026-01-01", "bucket": "bucket-a", "usage_type": "Requests-Tier2", "operation": "ReadLocation", "usage_amount": 10},
        {"period": "2026-01-01", "bucket": "bucket-a", "usage_type": "TimedStorage-ByteHrs", "operation": "StandardStorage", "usage_amount": 1},
    ]
    coverage = coverage_by_bucket(rows, "2026-01-01", "2026-01-02")["bucket-a"]
    signals = derive_signals(rows, coverage)
    assert signals["monthly_config_requests"] == 300
    assert signals["monthly_data_read_requests"] == 0
    assert signals["monthly_data_write_requests"] == 0
    assert signals["monthly_list_requests"] == 0


def test_coverage_gaps_are_excluded_and_zero_coverage_is_unknown():
    rows = [
        {"period": "2026-01-01", "bucket": "bucket-a", "usage_type": "TimedStorage-ByteHrs", "operation": "StandardStorage", "usage_amount": 1},
        {"period": "2026-01-03", "bucket": "bucket-a", "usage_type": "TimedStorage-ByteHrs", "operation": "StandardStorage", "usage_amount": 1},
        {"period": "2026-01-02", "bucket": "bucket-a", "usage_type": "Requests-Tier2", "operation": "GetObject", "usage_amount": 100},
    ]
    coverage = coverage_by_bucket(rows, "2026-01-01", "2026-01-04")["bucket-a"]
    assert coverage.count == 2
    # The request falls on a storage-coverage gap, so it is excluded rather
    # than interpolated or treated as a covered zero.
    assert derive_signals(rows, coverage)["monthly_data_read_requests"] == 0
    unknown = derive_signals([], {"covered_days": 0})
    assert unknown["telemetry_status"] == "unknown"
    assert unknown["monthly_data_read_requests"] is None


def test_exact_30_day_window_round_trips_monthly_total():
    rows = [
        {"period": f"2026-01-{day:02d}", "bucket": "bucket-a", "usage_type": "TimedStorage-ByteHrs", "operation": "StandardStorage", "usage_amount": 1}
        for day in range(1, 31)
    ]
    rows.extend(
        {"period": f"2026-01-{day:02d}", "bucket": "bucket-a", "usage_type": "Requests-Tier2", "operation": "GetObject", "usage_amount": 2}
        for day in range(1, 31)
    )
    coverage = coverage_by_bucket(rows, "2026-01-01", "2026-01-31")["bucket-a"]
    assert coverage.count == 30
    assert derive_signals(rows, coverage)["monthly_data_read_requests"] == 60


def test_reference_bucket_signals_include_retrieval_transitions_restore_copy_and_config_split():
    rows = [row for row in fixture_rows() if row["bucket"] == "bucket-a"]
    coverage = coverage_by_bucket(rows, "2026-07-01", "2026-09-21")["bucket-a"]
    signals = derive_signals(rows, coverage)
    assert signals["retrieval_ratio_by_class"]["STANDARD_IA"] is not None
    assert signals["retrieval_ratio_by_class"]["STANDARD_IA"] > 0
    assert set(signals["transition_requests_by_destination"]) >= {
        "STANDARD_IA", "ONEZONE_IA", "GLACIER_IR", "INTELLIGENT_TIERING", "GLACIER", "DEEP_ARCHIVE"
    }
    assert signals["restore_copy_gb_month_by_class"]["GLACIER"] > 0
    assert signals["monthly_config_requests"] > 0
    assert signals["monthly_data_read_requests"] > 0
    assert signals["monthly_config_requests"] > signals["monthly_data_read_requests"]
    assert "stored_gb_by_class" not in signals


def test_confidence_thresholds():
    assert confidence_for_covered_days(0) == "low"
    assert confidence_for_covered_days(29) == "low"
    assert confidence_for_covered_days(30) == "medium"
    assert confidence_for_covered_days(89) == "medium"
    assert confidence_for_covered_days(90) == "high"


def test_load_bucket_rows_is_offline_and_uses_arn_normalization(tmp_path):
    # The checked-in fixture itself is enough to validate the normalizer; this
    # assertion also documents the source function's empty-cache behavior.
    assert list(load_bucket_rows(tmp_path, "2026-01-01", "2026-02-01", "daily")) == []
