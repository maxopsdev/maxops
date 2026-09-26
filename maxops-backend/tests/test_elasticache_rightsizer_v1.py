from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.adapters.aws.adapter import AWSAdapter, _percent_parameter_fraction
from app.models.inventory import ElasticacheInventory, MaxOpsInventory
from app.models.settings import ResourceExemption
from app.pricing.baseline import estimate_elasticache_baseline_monthly_cost
from app.services.inventory_service import get_elasticache_inventory_overview
from app.services.scan_service import _enrich_elasticache_rightsizing_metrics
from app.services.iam_onboarding_service import READ_ONLY_SCAN_POLICY
from app.services.ec2_trend_cache import ec2_trend_cache
from app.services.elasticache_node_catalog import (
    ElastiCacheCatalogEntry,
    engine_supports_node_type,
)
from app.services.elasticache_rightsizer import ElastiCacheRightsizer
from app.utils.elasticache import (
    is_elasticache_member_cluster,
    purge_legacy_elasticache_member_rows,
)
from rightsizers.elasticache.elasticache_rightsizer.models import (
    ElastiCachePerformanceWarningPolicy,
)


def _entry(
    node_type: str, monthly: float, *, vcpus: int, memory: float
) -> ElastiCacheCatalogEntry:
    family = node_type.split(".")[1]
    return ElastiCacheCatalogEntry(
        node_type=node_type,
        engine="redis",
        region="us-east-1",
        hourly_usd=monthly / 730,
        monthly_usd=monthly,
        vcpus=vcpus,
        memory_gib=memory,
        maxmemory_bytes=int(memory * 1024**3),
        maxmemory_source="pricing_attribute",
        max_clients=65_000,
        family=family,
        family_class=family[0],
        architecture="arm64" if "g" in family else "x86_64",
        data_tiering=False,
        burstable=False,
        network_baseline_mbps=1000,
        network_reliable_max_mbps=5000,
        network_capacity_kind="BASELINE",
        network_performance_label="Up to 10 Gigabit",
    )


class Catalog:
    def __init__(self):
        self.entries = {
            "cache.m5.xlarge": _entry("cache.m5.xlarge", 200, vcpus=4, memory=16),
            "cache.m6g.large": _entry("cache.m6g.large", 100, vcpus=2, memory=12),
            "cache.m6g.xlarge": _entry("cache.m6g.xlarge", 150, vcpus=4, memory=16),
        }

    def list_region(self, region, engine):
        return self.entries


def _db(metadata):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(
        ElasticacheInventory(
            inventory_id=1,
            resource_id="rg-prod",
            resource_name="Production cache",
            resource_type="elasticache_replication_group",
            region="us-east-1",
            state="available",
            engine="redis",
            engine_version="7.1.0",
            cache_node_type="cache.m5.xlarge",
            metadata_json=metadata,
        )
    )
    session.commit()
    return session


def _metadata():
    return {
        "MemberClusters": ["cache-a", "cache-b", "cache-c"],
        "NumNodeGroups": 1,
        "ReplicasPerNodeGroup": [2],
        "auto_scaling_attached": False,
        "reserved_memory_percent": 0.25,
        "allowed_scale_down_types": ["cache.m6g.large", "cache.m6g.xlarge"],
        "rightsizing_metrics": {
            "engine_cpu_percent": {"p99": 30, "max": 40},
            "host_cpu_percent": {"p99": 25, "max": 35},
            "memory_percent": {"p99": 45, "max": 55},
            "network_in_mbps": {"p99": 50, "max": 100},
            "network_out_mbps": {"p99": 75, "max": 125},
            "evictions": {"p99": 0, "max": 0},
            "traffic_management_active": {"p99": 0, "max": 0},
            "swap_usage_bytes": {"p99": 0, "max": 0},
            "replication_lag_seconds": {"p99": 0.1, "max": 0.2},
            "per_node": {
                "cache-a": {
                    "role": "primary",
                    "node_group_id": "0001",
                    "engine_cpu_p99": 30,
                    "memory_p99": 40,
                    "read_ops_p99": 100,
                    "read_ops_max": 100,
                    "read_ops_sum": 1000,
                    "read_coverage_ratio": 1,
                    "read_observed_days": 60,
                    "read_latest_sample_age_seconds": 0,
                    "member_created_at": "2026-01-01T00:00:00+00:00",
                },
                "cache-b": {
                    "role": "replica",
                    "node_group_id": "0001",
                    "engine_cpu_p99": 10,
                    "memory_p99": 60,
                    "read_ops_p99": 0,
                    "read_ops_max": 0,
                    "read_ops_sum": 0,
                    "read_coverage_ratio": 0.95,
                    "read_observed_days": 30,
                    "read_latest_sample_age_seconds": 600,
                    "member_created_at": "2026-01-01T00:00:00+00:00",
                },
                "cache-c": {
                    "role": "replica",
                    "node_group_id": "0001",
                    "engine_cpu_p99": 12,
                    "memory_p99": 50,
                    "read_ops_p99": 1,
                    "read_ops_max": 1,
                    "read_ops_sum": 300,
                    "read_coverage_ratio": 1,
                    "read_observed_days": 60,
                    "read_latest_sample_age_seconds": 60,
                    "member_created_at": "2026-01-01T00:00:00+00:00",
                },
            },
        },
    }


def test_engine_support_uses_three_part_boundary_and_valkey_policy():
    assert engine_supports_node_type("redis", "5.0.5", "cache.m6g.large") is False
    assert engine_supports_node_type("redis", "5.0.6", "cache.m6g.large") is True
    assert engine_supports_node_type("valkey", "7.2.0", "cache.m7g.large") is True
    assert engine_supports_node_type("redis", "7.2.0", "cache.unknown.large") is None


def test_service_returns_balanced_node_and_independent_actionable_replica_option():
    result = ElastiCacheRightsizer(
        _db(_metadata()), catalog=Catalog()
    ).get_recommendation(1)
    assert result is not None
    assert result["tiers"]["default"] == "balanced"
    assert result["tiers"]["balanced"]["target_node_type"] in {
        "cache.m6g.large",
        "cache.m6g.xlarge",
    }
    assert result["recommendations"][0]["kind"] == "NODE_TYPE_CHANGE"
    assert "savings_previews" not in result
    replica = result["replica_recommendation"]
    assert replica["kind"] == "REPLICA_COUNT_REDUCTION"
    assert replica["classification"] == "ACTIONABLE"
    assert replica["removed_node_count"] == 1
    assert replica["target_replicas_per_node_group"] == 1


def test_positive_replica_reads_are_never_actionable():
    metadata = _metadata()
    metadata["rightsizing_metrics"]["per_node"]["cache-b"]["read_ops_max"] = 0.1
    metadata["rightsizing_metrics"]["per_node"]["cache-b"]["read_ops_sum"] = 1
    result = ElastiCacheRightsizer(_db(metadata), catalog=Catalog()).get_recommendation(
        1
    )
    replica = result["replica_recommendation"]
    assert replica["classification"] == "CONDITIONAL"
    assert "REPLICA_READ_TRAFFIC_OBSERVED" in replica["reason_codes"]


def test_freshness_is_policy_driven_and_equality_is_actionable():
    policy = ElastiCachePerformanceWarningPolicy(
        replica_actionable_max_sample_age_seconds=60
    )
    metadata = _metadata()
    metadata["rightsizing_metrics"]["per_node"]["cache-b"][
        "read_latest_sample_age_seconds"
    ] = 60
    result = ElastiCacheRightsizer(
        _db(metadata), catalog=Catalog(), warning_policy=policy
    ).get_recommendation(1)
    assert result["replica_recommendation"]["classification"] == "ACTIONABLE"
    metadata["rightsizing_metrics"]["per_node"]["cache-b"][
        "read_latest_sample_age_seconds"
    ] = 60.01
    result = ElastiCacheRightsizer(
        _db(metadata), catalog=Catalog(), warning_policy=policy
    ).get_recommendation(1)
    assert result["replica_recommendation"]["classification"] == "CONDITIONAL"
    assert (
        "REPLICA_READ_COVERAGE_INSUFFICIENT"
        in result["replica_recommendation"]["reason_codes"]
    )


class TrendAdapter:
    def __init__(self):
        self.selected = None

    def get_elasticache_confidence_trend(self, selected, *args, **kwargs):
        self.selected = selected
        return {
            metric: [
                {
                    "cache_cluster_id": item["cache_cluster_id"],
                    "node_group_id": item.get("node_group_id"),
                    "role": item.get("role"),
                    "selection_reason": item["selection_reason"],
                    "daily": [],
                    "buckets": {},
                }
                for item in items
            ]
            for metric, items in selected.items()
        }


def test_trend_caps_each_metric_and_discloses_omitted_primary_count():
    metadata = _metadata()
    per_node = {}
    for index in range(100):
        per_node[f"cache-{index:03d}"] = {
            "role": "primary",
            "node_group_id": f"{index:04d}",
            "engine_cpu_p99": index,
            "memory_p99": 100 - index,
        }
    metadata["rightsizing_metrics"]["per_node"] = per_node
    adapter = TrendAdapter()
    ec2_trend_cache.clear()
    result = ElastiCacheRightsizer(
        _db(metadata), catalog=Catalog()
    ).get_confidence_trend(
        1, adapter=adapter, end_date=datetime(2026, 7, 15, tzinfo=timezone.utc)
    )
    assert len(adapter.selected["engine_cpu"]) == 8
    assert len(adapter.selected["memory"]) == 8
    assert result["selection_summary"]["engine_cpu"] == {
        "primary_total": 100,
        "primary_returned": 8,
        "primary_omitted": 92,
        "primary_context_limit": 8,
    }
    assert adapter.selected["engine_cpu"][0]["cache_cluster_id"] == "cache-099"
    assert adapter.selected["memory"][0]["cache_cluster_id"] == "cache-000"


def test_member_cluster_identity_is_rejected_at_persistence_boundary():
    assert is_elasticache_member_cluster(
        {
            "resource_type": "elasticache_cluster",
            "metadata": {"ReplicationGroupId": "rg-prod"},
        }
    )
    assert is_elasticache_member_cluster(
        {
            "resource_type": "elasticache",
            "metadata": {"replication_group_id": "rg-prod"},
        }
    )
    assert not is_elasticache_member_cluster(
        {"resource_type": "elasticache_cluster", "metadata": {}}
    )


def test_frontend_and_backend_iam_policies_include_rightsizer_reads():
    required = {
        "application-autoscaling:DescribeScalableTargets",
        "elasticache:DescribeCacheParameters",
        "elasticache:DescribeCacheParameterGroups",
        "elasticache:ListAllowedNodeTypeModifications",
    }
    backend_actions = {
        action
        for statement in READ_ONLY_SCAN_POLICY["Statement"]
        for action in statement.get("Action", [])
    }
    frontend = (
        Path(__file__).resolve().parents[2]
        / "maxops-frontend/src/constants/iamReadOnlyPolicy.ts"
    ).read_text()
    assert required <= backend_actions
    assert all(action in frontend for action in required)


class CloudWatch:
    def __init__(self):
        self.query_counts = []

    def get_metric_data(self, **kwargs):
        self.query_counts.append(len(kwargs["MetricDataQueries"]))
        return {"MetricDataResults": []}


def test_trend_adapter_emits_fifteen_query_ids_per_series_and_chunks_at_500():
    cloudwatch = CloudWatch()
    adapter = AWSAdapter.__new__(AWSAdapter)
    adapter._use_simulator = False
    adapter.cloudwatch_client = cloudwatch
    descriptor = lambda index, reason: {
        "cache_cluster_id": f"cache-{index:03d}",
        "node_group_id": f"{index:04d}",
        "role": "primary",
        "selection_reason": reason,
    }
    selected = {
        "engine_cpu": [
            descriptor(index, "hottest_engine_cpu" if index == 0 else "primary")
            for index in range(101)
        ],
        "memory": [
            descriptor(index, "hottest_memory" if index == 0 else "primary")
            for index in range(101)
        ],
    }
    adapter.get_elasticache_confidence_trend(
        selected,
        datetime(2025, 4, 16, tzinfo=timezone.utc),
        datetime(2026, 7, 15, tzinfo=timezone.utc),
    )
    assert sum(cloudwatch.query_counts) == 15 * 202
    assert max(cloudwatch.query_counts) == 500
    assert cloudwatch.query_counts == [500, 500, 500, 500, 500, 500, 30]


def test_missing_telemetry_retains_capacity_in_each_unmeasured_dimension():
    metadata = _metadata()
    metadata.pop("rightsizing_metrics")
    result = ElastiCacheRightsizer(_db(metadata), catalog=Catalog()).get_recommendation(
        1
    )
    assert result is not None
    assert all(
        item["target_node_type"] != "cache.m6g.large"
        for item in result["recommendations"]
    )
    assert result["tiers"]["balanced"]["target_node_type"] == "cache.m6g.xlarge"
    assert result["rejection_summary"]["CPU_TELEMETRY_MISSING_CAPACITY_RETAINED"] >= 1
    assert (
        result["rejection_summary"]["MEMORY_TELEMETRY_MISSING_CAPACITY_RETAINED"] >= 1
    )


def test_zero_telemetry_is_observed_data_not_a_missing_metric():
    metadata = _metadata()
    for metric in (
        "engine_cpu_percent",
        "host_cpu_percent",
        "memory_percent",
        "network_in_mbps",
        "network_out_mbps",
    ):
        metadata["rightsizing_metrics"][metric]["p99"] = 0
    result = ElastiCacheRightsizer(_db(metadata), catalog=Catalog()).get_recommendation(
        1
    )
    candidate = next(
        item
        for item in result["recommendations"]
        if item["target_node_type"] == "cache.m6g.large"
    )
    assert "ENGINE_CPU_METRIC_UNAVAILABLE" not in candidate["reason_codes"]
    assert not any("TELEMETRY_MISSING" in code for code in candidate["reason_codes"])


def test_incomplete_network_telemetry_rejects_unknown_capacity_target():
    catalog = Catalog()
    catalog.entries["cache.m6g.large"] = replace(
        catalog.entries["cache.m6g.large"],
        network_baseline_mbps=None,
        network_reliable_max_mbps=None,
        network_capacity_kind="UNKNOWN",
    )
    metadata = _metadata()
    del metadata["rightsizing_metrics"]["network_out_mbps"]
    result = ElastiCacheRightsizer(_db(metadata), catalog=catalog).get_recommendation(1)
    assert (
        result["rejection_summary"]["NETWORK_TELEMETRY_MISSING_CAPACITY_RETAINED"] >= 1
    )


def test_sparse_safety_counter_maximum_is_not_lost_in_a_p99_tie():
    class MetricsAdapter:
        def get_elasticache_rightsizing_metrics(self, *args, **kwargs):
            return {
                "period_seconds": 300,
                "per_node": {
                    "cache-a": {"evictions": {"values": [0.0] * 101}},
                    "cache-b": {"evictions": {"values": [0.0] * 100 + [500.0]}},
                },
            }

    resource = {
        "resource_id": "rg-prod",
        "metadata": {"MemberClusters": ["cache-a", "cache-b"]},
    }
    _enrich_elasticache_rightsizing_metrics(
        MetricsAdapter(),
        resource,
        "us-east-1",
        datetime(2026, 7, 15, tzinfo=timezone.utc),
    )
    evictions = resource["metadata"]["rightsizing_metrics"]["evictions"]
    assert evictions["p99"] == 0
    assert evictions["max"] == 500


def test_missing_replication_lag_fails_replica_analysis_closed():
    metadata = _metadata()
    del metadata["rightsizing_metrics"]["replication_lag_seconds"]
    result = ElastiCacheRightsizer(_db(metadata), catalog=Catalog()).get_recommendation(
        1
    )
    assert result is not None
    assert result["replica_recommendation"] is None
    assert result["replica_deferred_reason_codes"] == ["REPLICA_EVIDENCE_INCOMPLETE"]
    assert result["recommendations"]


def test_up_to_network_target_uses_exact_family_assumed_baseline():
    catalog = Catalog()
    catalog.entries["cache.m6g.large"] = replace(
        catalog.entries["cache.m6g.large"],
        network_baseline_mbps=None,
        network_capacity_kind="BURST_OR_UP_TO",
    )
    result = ElastiCacheRightsizer(
        _db(_metadata()), catalog=catalog
    ).get_recommendation(1)
    candidate = next(
        item
        for item in result["recommendations"]
        if item["target_node_type"] == "cache.m6g.large"
    )
    assert "NETWORK_BASELINE_ASSUMED" in candidate["reason_codes"]
    assert candidate["constraint_coverage"]["network_baseline_assumed"] is True
    assert candidate["constraint_coverage"]["assumed_network_baseline_mbps"] == 2500
    assert candidate["classification"] == "CONDITIONAL"


def test_group_baseline_cost_counts_every_member():
    assert estimate_elasticache_baseline_monthly_cost(
        {
            "metadata": {
                "CacheNodeType": "cache.r6g.xlarge",
                "MemberClusters": [f"cache-{index}" for index in range(6)],
            }
        }
    ) == 6 * estimate_elasticache_baseline_monthly_cost(
        {"metadata": {"CacheNodeType": "cache.r6g.xlarge"}}
    )


def test_reserved_memory_one_is_one_percent_not_one_hundred_percent():
    assert _percent_parameter_fraction(1) == 0.01
    assert _percent_parameter_fraction(25) == 0.25
    assert _percent_parameter_fraction(0.25) == 0.25


def test_legacy_member_cleanup_migrates_exemption_and_removes_cost_row():
    db = _db(_metadata())
    for inventory_id, member_id in ((2, "cache-a"), (3, "cache-b")):
        db.add(
            ElasticacheInventory(
                inventory_id=inventory_id,
                resource_id=member_id,
                resource_name=member_id,
                resource_type="elasticache",
                region="us-east-1",
                state="available",
                engine="redis",
                cache_node_type="cache.m5.xlarge",
                metadata_json={"ReplicationGroupId": "rg-prod"},
            )
        )
        db.add(
            MaxOpsInventory(
                resource_type="elasticache",
                inventory_id=inventory_id,
                resource_id=member_id,
            )
        )
        db.add(
            ResourceExemption(
                check_id="elasticache_low_item_count",
                resource_id=member_id,
                resource_type="elasticache",
                exempted=member_id == "cache-a",
                snooze_days=7 if member_id == "cache-b" else None,
            )
        )
    db.commit()
    assert purge_legacy_elasticache_member_rows(db) == 2
    db.commit()
    assert db.query(ElasticacheInventory).filter_by(inventory_id=2).first() is None
    assert db.query(ElasticacheInventory).filter_by(inventory_id=3).first() is None
    assert db.query(MaxOpsInventory).filter_by(inventory_id=2).first() is None
    assert db.query(MaxOpsInventory).filter_by(inventory_id=3).first() is None
    exemption = db.query(ResourceExemption).one()
    assert exemption.resource_id == "rg-prod"
    assert exemption.exempted is True
    assert exemption.snooze_days == 7


def test_canonical_legacy_member_is_deferred_by_direct_rightsizer_lookup():
    metadata = _metadata()
    metadata["ReplicationGroupId"] = "rg-prod"
    db = _db(metadata)
    row = db.query(ElasticacheInventory).one()
    row.resource_type = "elasticache"
    db.commit()
    result = ElastiCacheRightsizer(db, catalog=Catalog()).get_recommendation(1)
    assert result["classification"] == "DEFERRED"
    assert result["deferred_reason_codes"] == ["MEMBER_OF_REPLICATION_GROUP"]


def test_overview_hides_legacy_member_rows_before_cleanup_runs():
    db = _db(_metadata())
    db.add(
        ElasticacheInventory(
            inventory_id=2,
            resource_id="cache-a",
            resource_name="cache-a",
            resource_type="elasticache",
            region="us-east-1",
            state="available",
            engine="redis",
            cache_node_type="cache.m5.xlarge",
            monthly_cost_estimate=200,
            metadata_json={"ReplicationGroupId": "rg-prod"},
        )
    )
    db.commit()
    overview = get_elasticache_inventory_overview(db)
    assert overview["summary"]["total_resources"] == 1
    assert [item["resource_id"] for item in overview["resources"]] == ["rg-prod"]


class _ElastiCacheInventoryClient:
    def __init__(self, fail_members=False):
        self.fail_members = fail_members
        self.member_calls = 0

    def describe_cache_clusters(self, **kwargs):
        self.member_calls += 1
        if self.fail_members:
            raise RuntimeError("transient member inventory failure")
        return {
            "CacheClusters": [
                {
                    "CacheClusterId": "cache-a",
                    "Engine": "redis",
                    "EngineVersion": "7.1",
                }
            ]
        }

    def describe_replication_groups(self, **kwargs):
        return {
            "ReplicationGroups": [
                {
                    "ReplicationGroupId": "rg-prod",
                    "Engine": "redis",
                    "EngineVersion": "7.1",
                    "CacheNodeType": "cache.m6g.large",
                    "Status": "available",
                    "MemberClusters": ["cache-a", "cache-b"],
                    "NodeGroups": [],
                }
            ]
        }

    def list_allowed_node_type_modifications(self, **kwargs):
        return {"ScaleUpModifications": [], "ScaleDownModifications": []}


class _AutoScalingClient:
    def describe_scalable_targets(self, **kwargs):
        return {"ScalableTargets": []}


class _InventorySession:
    def __init__(self, elasticache):
        self.elasticache = elasticache

    def client(self, service, **kwargs):
        return self.elasticache if service == "elasticache" else _AutoScalingClient()


def test_member_inventory_is_one_bulk_call_and_failure_does_not_drop_group():
    for fail_members in (False, True):
        client = _ElastiCacheInventoryClient(fail_members=fail_members)
        adapter = AWSAdapter.__new__(AWSAdapter)
        adapter._use_simulator = False
        adapter.session = _InventorySession(client)
        resources = adapter._get_elasticache_resources(
            "elasticache_replication_group", {}, "us-east-1"
        )
        assert [item["resource_id"] for item in resources] == ["rg-prod"]
        assert client.member_calls == 1
