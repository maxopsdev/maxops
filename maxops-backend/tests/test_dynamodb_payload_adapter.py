"""Offline adapter tests backed by checked-in DynamoDB payload fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.payload_helpers import build_dynamodb_payload_adapter


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "tests" / "payloads" / "dynamodb"
pytestmark = [pytest.mark.unit, pytest.mark.payload, pytest.mark.dynamodb]


@pytest.mark.skipif(not FIXTURE_ROOT.exists(), reason="DynamoDB payload fixtures have not been captured from AWS yet.")
class TestDynamoDBPayloadAdapter:
    def test_table_payload_is_normalized(self):
        adapter = build_dynamodb_payload_adapter("dynamodb_underutilized_rcu", "flag_low_rcu")

        resources = adapter.get_resources("dynamodb", {}, region="us-east-1")

        assert len(resources) == 1
        resource = resources[0]
        assert resource["resource_id"] == "maxops-payload-dynamodb-provisioned"
        assert resource["resource_type"] == "dynamodb_table"
        assert resource["metadata"]["BillingMode"] == "PROVISIONED"
        assert resource["metadata"]["read_capacity_units"] == 1
        assert resource["metadata"]["write_capacity_units"] == 1

    def test_gsi_payload_is_normalized(self):
        adapter = build_dynamodb_payload_adapter("dynamodb_gsi_unused", "flag_unused_gsi")

        resources = adapter.get_resources("dynamodb_gsi", {}, region="us-east-1")

        assert len(resources) == 1
        resource = resources[0]
        assert resource["resource_id"] == "maxops-payload-dynamodb-provisioned/maxops_payload_unused_gsi"
        assert resource["resource_type"] == "dynamodb_gsi"
        assert resource["metadata"]["TableName"] == "maxops-payload-dynamodb-provisioned"
        assert resource["metadata"]["IndexName"] == "maxops_payload_unused_gsi"

    def test_table_utilization_parses_metric_statistics_payloads(self):
        adapter = build_dynamodb_payload_adapter("dynamodb_underutilized_rcu", "flag_low_rcu")

        utilization = adapter.get_resource_utilization(
            "maxops-payload-dynamodb-provisioned",
            "dynamodb",
            start_date=None,
            end_date=None,
            region="us-east-1",
        )

        # Consumed capacity is reported as CloudWatch's Sum over the period,
        # with the period alongside it. Checks divide the two to get the
        # per-second rate that compares against provisioned capacity
        # (see app/checks/dynamodb/capacity_math.py).
        assert utilization["consumedreadcapacityunits"] == 180.0
        assert utilization["consumedreadcapacityunits_period_seconds"] == 3600
        assert (
            utilization["consumedreadcapacityunits"]
            / utilization["consumedreadcapacityunits_period_seconds"]
            == 0.05
        )
        assert utilization["ProvisionedReadCapacityUnits"] == [1.0]
        assert utilization["itemcount"] == 0.0

    def test_gsi_utilization_uses_table_and_index_dimensions(self):
        adapter = build_dynamodb_payload_adapter("dynamodb_gsi_unused", "pass_gsi_with_reads")

        utilization = adapter.get_resource_utilization(
            "maxops-payload-dynamodb-provisioned/maxops_payload_unused_gsi",
            "dynamodb_gsi",
            start_date=None,
            end_date=None,
        )

        assert utilization["consumedreadcapacityunits"] == 4.0
