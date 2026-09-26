"""Offline adapter tests backed by checked-in ElastiCache payload fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.payload_helpers import build_elasticache_payload_adapter


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "tests" / "payloads" / "elasticache"
pytestmark = [pytest.mark.unit, pytest.mark.payload, pytest.mark.elasticache]


@pytest.mark.skipif(not FIXTURE_ROOT.exists(), reason="ElastiCache payload fixtures have not been captured from AWS yet.")
class TestElastiCachePayloadAdapter:
    def test_cluster_payload_is_normalized(self):
        adapter = build_elasticache_payload_adapter("elasticache_low_item_count", "flag_low_item_cluster")

        resources = adapter.get_resources("elasticache_cluster", {}, region="us-east-1")

        assert len(resources) == 1
        resource = resources[0]
        assert resource["resource_id"]
        assert resource["resource_type"] == "elasticache_cluster"
        assert resource["metadata"]["CacheNodeType"] == "cache.t4g.micro"

    def test_replication_group_payload_is_normalized(self):
        adapter = build_elasticache_payload_adapter(
            "elasticache_redis_convertible_to_valkey",
            "flag_redis_replication_group",
        )

        resources = adapter.get_resources("elasticache_replication_group", {}, region="us-east-1")

        assert len(resources) == 1
        resource = resources[0]
        assert resource["resource_type"] == "elasticache_replication_group"
        assert resource["metadata"]["Engine"] == "redis"

    def test_elasticache_utilization_parses_metric_statistics_payloads(self):
        adapter = build_elasticache_payload_adapter("elasticache_low_item_count", "flag_low_item_cluster")

        utilization = adapter.get_resource_utilization(
            adapter.get_resources("elasticache_cluster", {}, region="us-east-1")[0]["resource_id"],
            "elasticache_cluster",
            start_date=None,
            end_date=None,
            region="us-east-1",
        )

        assert "CurrItems" in utilization
        assert "KeyCount" in utilization
