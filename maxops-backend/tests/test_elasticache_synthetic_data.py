"""Tests for the DB-oriented ElastiCache synthetic data generator."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "synthetic_data" / "elasticache_syn_data.py"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "synthetic_data" / "elasticache_syn_config.json"


def load_module():
    spec = importlib.util.spec_from_file_location("elasticache_syn_data", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_config():
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_generate_dataset_is_deterministic():
    module = load_module()
    config = load_config()

    left = module.generate_dataset(config, count_override=24, seed_override=1234)
    right = module.generate_dataset(config, count_override=24, seed_override=1234)

    assert left.inventory == right.inventory
    assert left.maxops == right.maxops
    assert left.inventory["inventory_count"] == 24


def test_inventory_document_contains_aws_payload_and_flattened_resources():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=18, seed_override=55)
    inventory = artifacts.inventory
    flat_resource = inventory["resources"][0]

    assert set(inventory.keys()) == {
        "account_id",
        "aws_payload",
        "generated_at",
        "inventory_count",
        "resources",
    }
    assert inventory["inventory_count"] == len(inventory["resources"])
    assert inventory["inventory_count"] == len(inventory["aws_payload"]["CacheClusters"]) + len(
        inventory["aws_payload"]["ReplicationGroups"]
    )

    assert "CacheClusters" in inventory["aws_payload"]
    assert "ReplicationGroups" in inventory["aws_payload"]
    assert "MetricDetails" in inventory["aws_payload"]

    aws_clusters = inventory["aws_payload"]["CacheClusters"]
    aws_replication_groups = inventory["aws_payload"]["ReplicationGroups"]
    assert aws_clusters or aws_replication_groups
    if aws_clusters:
        assert "InventoryId" in aws_clusters[0]
        assert "CacheClusterId" in aws_clusters[0]
        assert "CacheNodeType" in aws_clusters[0]
        assert "Engine" in aws_clusters[0]
    if aws_replication_groups:
        assert "InventoryId" in aws_replication_groups[0]
        assert "ReplicationGroupId" in aws_replication_groups[0]
        assert "CacheNodeType" in aws_replication_groups[0]
        assert "EngineVersion" in aws_replication_groups[0]

    assert "inventory_id" in flat_resource
    assert "resource_id" in flat_resource
    assert "resource_name" in flat_resource
    assert "resource_type" in flat_resource
    assert "account_id" in flat_resource
    assert "region" in flat_resource
    assert "state" in flat_resource
    assert "tags" in flat_resource
    assert "metadata" in flat_resource

    metadata = flat_resource["metadata"]
    for key in [
        "engine",
        "engine_version",
        "cache_node_type",
        "usage_profile",
        "monthly_cost_estimate",
        "owner",
        "team",
        "business_unit",
        "criticality",
        "metric_history",
    ]:
        assert key in metadata


def test_write_artifacts_emits_exactly_two_files(tmp_path):
    module = load_module()
    config = load_config()
    artifacts = module.generate_dataset(config, count_override=10, seed_override=8)

    module.write_artifacts(artifacts, tmp_path)

    emitted = sorted(path.name for path in tmp_path.glob("*.json"))
    assert emitted == ["elasticache_maxops.json", "inventory.json"]


def test_maxops_rows_join_one_to_one_with_inventory():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=32, seed_override=7)
    resource_ids = {item["inventory_id"] for item in artifacts.inventory["resources"]}
    maxops_ids = [item["inventory_id"] for item in artifacts.maxops]

    assert len(artifacts.maxops) == artifacts.inventory["inventory_count"]
    assert set(maxops_ids) == resource_ids
    assert len(maxops_ids) == len(set(maxops_ids))


def test_default_dataset_covers_resource_variety_and_findings():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config)
    resources = artifacts.inventory["resources"]
    node_types = {item["metadata"]["cache_node_type"] for item in resources}
    resource_types = {item["resource_type"] for item in resources}
    engines = {item["metadata"]["engine"] for item in resources}
    usage_profiles = {item["metadata"]["usage_profile"] for item in resources}
    engine_versions = {item["metadata"]["engine_version"] for item in resources}

    assert resource_types == {"elasticache_cluster", "elasticache_replication_group"}
    assert any(module.is_graviton(node_type) for node_type in node_types)
    assert any(not module.is_graviton(node_type) for node_type in node_types)
    assert engines >= {"redis", "valkey"}
    assert usage_profiles >= {"low_item_candidate", "non_graviton_candidate", "valkey_candidate"}
    assert any(module.version_gte(version, module.ELIGIBLE_MIN_REDIS_VERSION) for version in engine_versions if version)
    assert any(not module.version_gte(version, module.ELIGIBLE_MIN_REDIS_VERSION) for version in engine_versions if version.startswith("5"))

    check_ids = {row["check_id"] for row in artifacts.maxops}
    assert {
        "elasticache_low_item_count",
        "elasticache_non_graviton_instance_class",
        "elasticache_redis_convertible_to_valkey",
        None,
    }.issubset(check_ids)


def test_actionable_rows_match_current_elasticache_checks():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=48, seed_override=88)
    by_id = {item["inventory_id"]: item for item in artifacts.inventory["resources"]}
    low_item_rows = [item for item in artifacts.maxops if item["check_id"] == "elasticache_low_item_count"]
    non_graviton_rows = [item for item in artifacts.maxops if item["check_id"] == "elasticache_non_graviton_instance_class"]
    valkey_rows = [item for item in artifacts.maxops if item["check_id"] == "elasticache_redis_convertible_to_valkey"]

    assert low_item_rows
    assert non_graviton_rows
    assert valkey_rows
    assert all(by_id[row["inventory_id"]]["metadata"]["avg_curritems"] <= 1000 for row in low_item_rows)
    assert all(not module.is_graviton(by_id[row["inventory_id"]]["metadata"]["cache_node_type"]) for row in non_graviton_rows)
    assert all(by_id[row["inventory_id"]]["metadata"]["engine"] == "redis" for row in valkey_rows)
    assert all(
        module.version_gte(by_id[row["inventory_id"]]["metadata"]["engine_version"], module.ELIGIBLE_MIN_REDIS_VERSION)
        for row in valkey_rows
    )


def test_outputs_do_not_leak_capture_identifiers():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=20, seed_override=91)
    rendered = json.dumps({"inventory": artifacts.inventory, "maxops": artifacts.maxops})

    assert "maxops-payload-elasticache-low-items" not in rendered
    assert "maxops-payload-elasticache-non-graviton" not in rendered
    assert "maxops-payload-elasticache-redis-valkey" not in rendered
    assert "123456789012" not in rendered
    assert ".uudkh8." not in rendered


def test_maxops_rows_do_not_duplicate_full_metric_history():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=20, seed_override=321)
    sample_maxops = artifacts.maxops[0]

    assert "metric_history" not in sample_maxops["metadata"]
