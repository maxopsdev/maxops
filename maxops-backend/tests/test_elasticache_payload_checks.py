"""Offline ElastiCache check tests backed by saved payload fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.checks.elasticache.low_items_count import check_elasticache_low_item_count
from app.checks.elasticache.non_graviton import check_elasticache_non_graviton_instance_class
from app.checks.elasticache.valkey_compatible import check_elasticache_redis_convertible_to_valkey
from tests.payload_helpers import build_elasticache_payload_adapter, load_scenario_payloads


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "tests" / "payloads" / "elasticache"
pytestmark = [pytest.mark.unit, pytest.mark.payload, pytest.mark.elasticache]


@pytest.mark.skipif(not FIXTURE_ROOT.exists(), reason="ElastiCache payload fixtures have not been captured from AWS yet.")
class TestElastiCachePayloadBackedChecks:
    def test_low_item_check_matches_low_item_cluster(self):
        payloads = load_scenario_payloads("elasticache", "elasticache_low_item_count", "flag_low_item_cluster")
        adapter = build_elasticache_payload_adapter("elasticache_low_item_count", "flag_low_item_cluster")
        metadata = payloads["capture_metadata.json"]

        results = check_elasticache_low_item_count(aws_adapter=adapter, **metadata["check_parameters"])

        assert [resource["resource_id"] for resource in results] == metadata["expected_matches"]

    def test_low_item_check_excludes_cluster_with_items(self):
        adapter = build_elasticache_payload_adapter("elasticache_low_item_count", "pass_cluster_with_items")

        results = check_elasticache_low_item_count(
            aws_adapter=adapter,
            lookback_days=7,
            low_item_threshold=1000,
            region="us-east-1",
        )

        assert results == []

    def test_low_item_check_skips_missing_metrics(self):
        adapter = build_elasticache_payload_adapter("elasticache_low_item_count", "edge_missing_item_metrics")

        results = check_elasticache_low_item_count(
            aws_adapter=adapter,
            lookback_days=7,
            low_item_threshold=1000,
            region="us-east-1",
        )

        assert results == []

    def test_non_graviton_check_flags_non_graviton_cluster(self):
        payloads = load_scenario_payloads("elasticache", "elasticache_non_graviton_instance_class", "flag_non_graviton_cluster")
        adapter = build_elasticache_payload_adapter("elasticache_non_graviton_instance_class", "flag_non_graviton_cluster")
        metadata = payloads["capture_metadata.json"]

        results = check_elasticache_non_graviton_instance_class(aws_adapter=adapter, **metadata["check_parameters"])

        assert [resource["resource_id"] for resource in results] == metadata["expected_matches"]

    def test_non_graviton_check_skips_graviton_cluster(self):
        adapter = build_elasticache_payload_adapter("elasticache_non_graviton_instance_class", "pass_graviton_cluster")

        results = check_elasticache_non_graviton_instance_class(aws_adapter=adapter, region="us-east-1")

        assert results == []

    def test_valkey_check_flags_eligible_redis_replication_group(self):
        payloads = load_scenario_payloads("elasticache", "elasticache_redis_convertible_to_valkey", "flag_redis_replication_group")
        adapter = build_elasticache_payload_adapter("elasticache_redis_convertible_to_valkey", "flag_redis_replication_group")
        metadata = payloads["capture_metadata.json"]

        results = check_elasticache_redis_convertible_to_valkey(aws_adapter=adapter, **metadata["check_parameters"])

        assert [resource["resource_id"] for resource in results] == metadata["expected_matches"]

    def test_valkey_check_skips_ineligible_engine_version(self):
        adapter = build_elasticache_payload_adapter(
            "elasticache_redis_convertible_to_valkey",
            "pass_ineligible_engine_version",
        )

        results = check_elasticache_redis_convertible_to_valkey(
            aws_adapter=adapter,
            eligible_min_redis_version="6.0",
            region="us-east-1",
        )

        assert results == []
