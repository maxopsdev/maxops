"""Offline S3 check tests backed by saved payload fixtures."""

from __future__ import annotations

import pytest

from app.checks.s3.access_logging_enabled import check_s3_logging_enabled
from app.checks.s3.inventory_enabled import check_s3_inventory_enabled
from app.checks.s3.logs_bucket_no_expiration import check_s3_log_buckets_without_expiration_policy
from app.checks.s3.no_archival_policies import check_s3_no_archival_policy
from app.checks.s3.no_delete_marker_expiration import check_s3_no_delete_marker_expiration
from app.checks.s3.no_expiration_set import check_s3_no_expiration_policy
from app.checks.s3.no_life_cycle_policies import check_s3_no_lifecycle_policy
from app.checks.s3.no_mpu_policy import check_s3_no_mpu_policy
from app.checks.s3.no_noncurrent_expiration import check_s3_no_noncurrent_expiration
from app.checks.s3.no_noncurrent_objects_archival import check_s3_no_noncurrent_version_transition
from app.checks.s3.replication_enabled import check_s3_replication_enabled
from tests.payload_helpers import build_s3_payload_adapter, load_scenario_payloads


pytestmark = [pytest.mark.unit, pytest.mark.payload, pytest.mark.s3]


CHECK_CASES = [
    ("s3_no_lifecycle_policy", check_s3_no_lifecycle_policy, "flag_bucket_without_lifecycle", "pass_bucket_with_lifecycle"),
    ("s3_no_expiration_policy", check_s3_no_expiration_policy, "flag_bucket_without_expiration", "pass_bucket_with_expiration"),
    ("s3_inventory_enabled", check_s3_inventory_enabled, "flag_bucket_with_inventory", "pass_bucket_without_inventory"),
    ("s3_logging_enabled", check_s3_logging_enabled, "flag_bucket_with_logging", "pass_bucket_without_logging"),
    (
        "s3_log_buckets_without_expiration_policy",
        check_s3_log_buckets_without_expiration_policy,
        "flag_log_bucket_without_expiration",
        "pass_non_log_bucket_with_expiration",
    ),
    ("s3_no_archival_policy", check_s3_no_archival_policy, "flag_bucket_without_archival", "pass_bucket_with_archival"),
    ("s3_no_mpu_policy", check_s3_no_mpu_policy, "flag_bucket_without_abort_mpu", "pass_bucket_with_abort_mpu"),
    (
        "s3_no_noncurrent_expiration",
        check_s3_no_noncurrent_expiration,
        "flag_versioned_bucket_missing_noncurrent_expiration",
        "pass_versioned_bucket_with_noncurrent_expiration",
    ),
    (
        "s3_no_delete_marker_expiration",
        check_s3_no_delete_marker_expiration,
        "flag_versioned_bucket_missing_delete_marker_cleanup",
        "pass_versioned_bucket_with_delete_marker_cleanup",
    ),
    (
        "s3_no_noncurrent_version_transition",
        check_s3_no_noncurrent_version_transition,
        "flag_versioned_bucket_missing_noncurrent_transition",
        "pass_versioned_bucket_with_noncurrent_transition",
    ),
    ("s3_replication_enabled", check_s3_replication_enabled, "flag_bucket_with_replication", "pass_bucket_without_replication"),
]


@pytest.mark.parametrize(
    ("check_id", "check_function", "positive_scenario", "negative_scenario"),
    CHECK_CASES,
)
def test_payload_backed_s3_checks_match_manifest_expectations(
    check_id,
    check_function,
    positive_scenario,
    negative_scenario,
):
    positive_payloads = load_scenario_payloads("s3", check_id, positive_scenario)
    positive_adapter = build_s3_payload_adapter(check_id, positive_scenario)
    positive_metadata = positive_payloads["capture_metadata.json"]

    positive_results = check_function(
        aws_adapter=positive_adapter,
        **positive_metadata["check_parameters"],
    )

    assert [resource["resource_id"] for resource in positive_results] == positive_metadata["expected_matches"]
    if positive_results:
        assert positive_results[0]["metadata"]["recommended_action"] == "review"

    negative_payloads = load_scenario_payloads("s3", check_id, negative_scenario)
    negative_adapter = build_s3_payload_adapter(check_id, negative_scenario)
    negative_metadata = negative_payloads["capture_metadata.json"]

    negative_results = check_function(
        aws_adapter=negative_adapter,
        **negative_metadata["check_parameters"],
    )

    assert negative_results == []
