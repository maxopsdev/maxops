"""Offline adapter tests backed by checked-in S3 payload fixtures."""

from __future__ import annotations

import pytest

from tests.payload_helpers import build_s3_payload_adapter


pytestmark = [pytest.mark.unit, pytest.mark.payload, pytest.mark.s3]


class TestS3PayloadAdapter:
    def test_s3_bucket_payload_is_normalized(self):
        adapter = build_s3_payload_adapter("s3_no_lifecycle_policy", "flag_bucket_without_lifecycle")

        resources = adapter.get_resources("s3", {}, region="us-east-1")

        assert len(resources) == 1
        resource = resources[0]
        assert resource["resource_id"] == "maxops-payload-s3-plain-no-lifecycle"
        assert resource["resource_type"] == "s3"
        assert resource["resource_name"] == "maxops-payload-s3-plain-no-lifecycle"
        assert resource["region"] == "us-east-1"
        assert resource["state"] == "active"
        assert resource["metadata"]["lifecycle_rules"] == []
        assert resource["tags"]["maxops_payload_profile"] == "plain_no_lifecycle"

    def test_logging_payload_exposes_target_bucket(self):
        adapter = build_s3_payload_adapter("s3_logging_enabled", "flag_bucket_with_logging")

        payload = adapter.get_s3_bucket_logging("maxops-payload-s3-logging-source", region="us-east-1")

        logging_enabled = payload["LoggingEnabled"]
        assert logging_enabled["TargetBucket"] == "maxops-payload-s3-logging-target"
        assert logging_enabled["TargetPrefix"] == "access-logs/"

    def test_inventory_payload_exposes_configuration(self):
        adapter = build_s3_payload_adapter("s3_inventory_enabled", "flag_bucket_with_inventory")

        payload = adapter.get_s3_bucket_inventory("maxops-payload-s3-inventory-source", region="us-east-1")

        configs = payload["InventoryConfigurationList"]
        assert len(configs) == 1
        assert configs[0]["Destination"]["S3BucketDestination"]["Bucket"] == "arn:aws:s3:::maxops-payload-s3-inventory-dest"

    def test_versioning_payload_exposes_enabled_status(self):
        adapter = build_s3_payload_adapter(
            "s3_no_noncurrent_expiration",
            "pass_versioned_bucket_with_noncurrent_expiration",
        )

        payload = adapter.get_s3_bucket_versioning("maxops-payload-s3-compliant-versioned", region="us-east-1")

        assert payload["Status"] == "Enabled"

    def test_replication_payload_exposes_destination_bucket(self):
        adapter = build_s3_payload_adapter("s3_replication_enabled", "flag_bucket_with_replication")

        payload = adapter.get_s3_bucket_replication("maxops-payload-s3-replication-source", region="us-east-1")

        rules = payload["ReplicationConfiguration"]["Rules"]
        assert len(rules) == 1
        assert rules[0]["Destination"]["Bucket"] == "arn:aws:s3:::maxops-payload-s3-replication-dest"
