"""Offline DynamoDB check tests backed by saved payload fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.checks.dynamodb.best_fit_ondemand import check_dynamodb_best_fit_on_demand
from app.checks.dynamodb.best_fit_provisoned import check_dynamodb_best_fit_provisioned
from app.checks.dynamodb.gsi_unused import check_dynamodb_gsi_unused
from app.checks.dynamodb.underutilized_rcu import check_dynamodb_underutilized_rcu
from app.checks.dynamodb.underutilized_tables import check_dynamodb_underutilized_tables
from app.checks.dynamodb.underutilized_wcu import check_dynamodb_underutilized_wcu
from tests.payload_helpers import build_dynamodb_payload_adapter, load_scenario_payloads


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "tests" / "payloads" / "dynamodb"
pytestmark = [pytest.mark.unit, pytest.mark.payload, pytest.mark.dynamodb]


class FakeDynamoDBAdapter:
    def __init__(self, utilization):
        self._utilization = utilization

    def get_resources(self, resource_type, filters=None, region=None):
        assert resource_type == "dynamodb"
        return [
            {
                "resource_id": "raw-sum-table",
                "resource_type": "dynamodb_table",
                "metadata": {
                    "BillingMode": "PROVISIONED",
                    "read_capacity_units": 1,
                    "write_capacity_units": 1,
                },
            }
        ]

    def get_resource_utilization(self, resource_id, resource_type, start_date, end_date, region=None):
        assert resource_id == "raw-sum-table"
        assert resource_type == "dynamodb"
        return self._utilization


def test_underutilized_wcu_normalizes_consumed_sum_by_period():
    adapter = FakeDynamoDBAdapter(
        {
            "ConsumedWriteCapacityUnits": [180.0],
            "ConsumedWriteCapacityUnits_statistic": "Sum",
            "ConsumedWriteCapacityUnits_period_seconds": 3600,
            "ProvisionedWriteCapacityUnits": [1.0],
            "ProvisionedReadCapacityUnits": [1.0],
        }
    )

    results = check_dynamodb_underutilized_wcu(
        aws_adapter=adapter,
        wcu_utilization_threshold=10.0,
        min_provisioned_wcu=1,
        region="us-east-1",
    )

    assert [resource["resource_id"] for resource in results] == ["raw-sum-table"]
    assert results[0]["metadata"]["avg_consumed_wcu"] == 0.05
    assert results[0]["metadata"]["wcu_utilization_pct"] == 5.0


def test_underutilized_wcu_does_not_compare_raw_consumed_sum_to_provisioned_capacity():
    adapter = FakeDynamoDBAdapter(
        {
            "ConsumedWriteCapacityUnits": [3600.0],
            "ConsumedWriteCapacityUnits_statistic": "Sum",
            "ConsumedWriteCapacityUnits_period_seconds": 3600,
            "ProvisionedWriteCapacityUnits": [1.0],
            "ProvisionedReadCapacityUnits": [1.0],
        }
    )

    results = check_dynamodb_underutilized_wcu(
        aws_adapter=adapter,
        wcu_utilization_threshold=10.0,
        min_provisioned_wcu=1,
        region="us-east-1",
    )

    assert results == []


def test_best_fit_on_demand_uses_consumed_sum_period_math_for_low_utilization():
    adapter = FakeDynamoDBAdapter(
        {
            "ConsumedReadCapacityUnits": [180.0],
            "ConsumedReadCapacityUnits_statistic": "Sum",
            "ConsumedReadCapacityUnits_period_seconds": 3600,
            "ConsumedWriteCapacityUnits": [180.0],
            "ConsumedWriteCapacityUnits_statistic": "Sum",
            "ConsumedWriteCapacityUnits_period_seconds": 3600,
            "ProvisionedReadCapacityUnits": [1.0],
            "ProvisionedWriteCapacityUnits": [1.0],
            "ReadThrottleEvents": [0.0],
            "WriteThrottleEvents": [0.0],
        }
    )

    results = check_dynamodb_best_fit_on_demand(
        aws_adapter=adapter,
        low_utilization_threshold_pct=10.0,
        region="us-east-1",
    )

    assert [resource["resource_id"] for resource in results] == ["raw-sum-table"]
    assert results[0]["metadata"]["avg_rcu_utilization_pct"] == 5.0
    assert results[0]["metadata"]["avg_wcu_utilization_pct"] == 5.0


@pytest.mark.skipif(not FIXTURE_ROOT.exists(), reason="DynamoDB payload fixtures have not been captured from AWS yet.")
class TestDynamoDBPayloadBackedChecks:
    def test_gsi_unused_flags_zero_read_traffic(self):
        payloads = load_scenario_payloads("dynamodb", "dynamodb_gsi_unused", "flag_unused_gsi")
        adapter = build_dynamodb_payload_adapter("dynamodb_gsi_unused", "flag_unused_gsi")
        metadata = payloads["capture_metadata.json"]

        results = check_dynamodb_gsi_unused(aws_adapter=adapter, **metadata["check_parameters"])

        assert [resource["resource_id"] for resource in results] == metadata["expected_matches"]

    def test_gsi_unused_skips_positive_read_traffic(self):
        adapter = build_dynamodb_payload_adapter("dynamodb_gsi_unused", "pass_gsi_with_reads")

        results = check_dynamodb_gsi_unused(aws_adapter=adapter, lookback_days=30, period_seconds=86400, region="us-east-1")

        assert results == []

    def test_underutilized_rcu_flags_low_read_utilization(self):
        payloads = load_scenario_payloads("dynamodb", "dynamodb_underutilized_rcu", "flag_low_rcu")
        adapter = build_dynamodb_payload_adapter("dynamodb_underutilized_rcu", "flag_low_rcu")
        metadata = payloads["capture_metadata.json"]

        results = check_dynamodb_underutilized_rcu(aws_adapter=adapter, **metadata["check_parameters"])

        assert [resource["resource_id"] for resource in results] == metadata["expected_matches"]
        assert results[0]["metadata"]["avg_provisioned_rcu"] == 1.0

    def test_underutilized_rcu_skips_busy_reads(self):
        adapter = build_dynamodb_payload_adapter("dynamodb_underutilized_rcu", "pass_busy_rcu")

        results = check_dynamodb_underutilized_rcu(
            aws_adapter=adapter,
            lookback_days=14,
            rcu_utilization_threshold=10.0,
            min_provisioned_rcu=1,
            region="us-east-1",
        )

        assert results == []

    def test_underutilized_wcu_flags_low_write_utilization(self):
        payloads = load_scenario_payloads("dynamodb", "dynamodb_underutilized_wcu", "flag_low_wcu")
        adapter = build_dynamodb_payload_adapter("dynamodb_underutilized_wcu", "flag_low_wcu")
        metadata = payloads["capture_metadata.json"]

        results = check_dynamodb_underutilized_wcu(aws_adapter=adapter, **metadata["check_parameters"])

        assert [resource["resource_id"] for resource in results] == metadata["expected_matches"]
        assert results[0]["metadata"]["avg_provisioned_wcu"] == 1.0

    def test_underutilized_wcu_skips_busy_writes(self):
        adapter = build_dynamodb_payload_adapter("dynamodb_underutilized_wcu", "pass_busy_wcu")

        results = check_dynamodb_underutilized_wcu(
            aws_adapter=adapter,
            lookback_days=14,
            wcu_utilization_threshold=10.0,
            min_provisioned_wcu=1,
            region="us-east-1",
        )

        assert results == []

    def test_underutilized_tables_flags_real_low_item_count(self):
        payloads = load_scenario_payloads("dynamodb", "dynamodb_underutilized_tables", "flag_low_item_table")
        adapter = build_dynamodb_payload_adapter("dynamodb_underutilized_tables", "flag_low_item_table")
        metadata = payloads["capture_metadata.json"]

        results = check_dynamodb_underutilized_tables(aws_adapter=adapter, **metadata["check_parameters"])

        assert [resource["resource_id"] for resource in results] == metadata["expected_matches"]

    def test_underutilized_tables_respects_zero_threshold(self):
        adapter = build_dynamodb_payload_adapter("dynamodb_underutilized_tables", "pass_threshold_zero")

        results = check_dynamodb_underutilized_tables(
            aws_adapter=adapter,
            lookback_days=14,
            item_count_threshold=0,
            region="us-east-1",
        )

        assert results == []

    def test_best_fit_on_demand_flags_low_utilization_provisioned_table(self):
        payloads = load_scenario_payloads("dynamodb", "dynamodb_best_fit_on_demand", "flag_low_utilization_provisioned")
        adapter = build_dynamodb_payload_adapter("dynamodb_best_fit_on_demand", "flag_low_utilization_provisioned")
        metadata = payloads["capture_metadata.json"]

        results = check_dynamodb_best_fit_on_demand(aws_adapter=adapter, **metadata["check_parameters"])

        assert [resource["resource_id"] for resource in results] == metadata["expected_matches"]

    def test_best_fit_on_demand_skips_already_on_demand_table(self):
        adapter = build_dynamodb_payload_adapter("dynamodb_best_fit_on_demand", "pass_already_on_demand")

        results = check_dynamodb_best_fit_on_demand(aws_adapter=adapter, region="us-east-1")

        assert results == []

    def test_best_fit_provisioned_flags_steady_on_demand_table(self):
        payloads = load_scenario_payloads("dynamodb", "dynamodb_best_fit_provisioned", "flag_steady_on_demand")
        adapter = build_dynamodb_payload_adapter("dynamodb_best_fit_provisioned", "flag_steady_on_demand")
        metadata = payloads["capture_metadata.json"]

        results = check_dynamodb_best_fit_provisioned(aws_adapter=adapter, **metadata["check_parameters"])

        assert [resource["resource_id"] for resource in results] == metadata["expected_matches"]

    def test_best_fit_provisioned_skips_already_provisioned_table(self):
        adapter = build_dynamodb_payload_adapter("dynamodb_best_fit_provisioned", "pass_already_provisioned")

        results = check_dynamodb_best_fit_provisioned(aws_adapter=adapter, region="us-east-1")

        assert results == []
