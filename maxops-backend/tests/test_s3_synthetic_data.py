"""Tests for the DB-oriented S3 synthetic data generator."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "synthetic_data" / "s3_syn_data.py"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "synthetic_data" / "s3_syn_config.json"
SUPPORTED_CHECK_IDS = {
    "s3_no_lifecycle_policy",
    "s3_no_expiration_policy",
    "s3_inventory_enabled",
    "s3_logging_enabled",
    "s3_log_buckets_without_expiration_policy",
    "s3_no_archival_policy",
    "s3_no_mpu_policy",
    "s3_no_noncurrent_expiration",
    "s3_no_delete_marker_expiration",
    "s3_no_noncurrent_version_transition",
    "s3_replication_enabled",
}
VERSIONED_CHECKS = {
    "s3_no_noncurrent_expiration",
    "s3_no_delete_marker_expiration",
    "s3_no_noncurrent_version_transition",
}


def load_module():
    spec = importlib.util.spec_from_file_location("s3_syn_data", MODULE_PATH)
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

    left = module.generate_dataset(config, count_override=20, seed_override=1234)
    right = module.generate_dataset(config, count_override=20, seed_override=1234)

    assert left.inventory == right.inventory
    assert left.maxops == right.maxops
    assert left.inventory["inventory_count"] == 20


def test_inventory_document_contains_aws_payload_and_flattened_buckets():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=18, seed_override=55)
    inventory = artifacts.inventory
    aws_bucket = inventory["aws_payload"]["Buckets"][0]
    details = inventory["aws_payload"]["BucketDetails"][aws_bucket["Name"]]
    flat_bucket = inventory["buckets"][0]

    assert set(inventory.keys()) == {
        "account_id",
        "aws_payload",
        "buckets",
        "generated_at",
        "inventory_count",
    }
    assert inventory["inventory_count"] == len(inventory["buckets"])
    assert inventory["inventory_count"] == len(inventory["aws_payload"]["Buckets"])

    assert "Name" in aws_bucket
    assert "CreationDate" in aws_bucket
    assert "get_bucket_location" in details
    assert "get_bucket_tagging" in details
    assert "get_bucket_lifecycle_configuration" in details

    assert "inventory_id" in flat_bucket
    assert "resource_id" in flat_bucket
    assert "resource_name" in flat_bucket
    assert "resource_type" in flat_bucket
    assert "account_id" in flat_bucket
    assert "region" in flat_bucket
    assert "state" in flat_bucket
    assert "tags" in flat_bucket
    assert "metadata" in flat_bucket

    metadata = flat_bucket["metadata"]
    for key in [
        "creation_date",
        "versioning_status",
        "lifecycle_rules",
        "logging_enabled",
        "inventory_configuration_count",
        "replication_rule_count",
        "bucket_size_gb",
        "object_count",
        "storage_class_mix",
        "estimated_monthly_cost",
        "environment",
        "owner",
        "team",
        "cost_center",
        "business_unit",
        "criticality",
    ]:
        assert key in metadata


def test_write_artifacts_emits_exactly_two_files(tmp_path):
    module = load_module()
    config = load_config()
    artifacts = module.generate_dataset(config, count_override=10, seed_override=8)

    module.write_artifacts(artifacts, tmp_path)

    emitted = sorted(path.name for path in tmp_path.glob("*.json"))
    assert emitted == ["inventory.json", "s3_maxops.json"]


def test_maxops_rows_join_one_to_one_with_inventory():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=32, seed_override=7)
    bucket_ids = {item["inventory_id"] for item in artifacts.inventory["buckets"]}
    maxops_ids = [item["inventory_id"] for item in artifacts.maxops]

    assert len(artifacts.maxops) == artifacts.inventory["inventory_count"]
    assert set(maxops_ids) == bucket_ids
    assert len(maxops_ids) == len(set(maxops_ids))


def test_default_dataset_covers_all_supported_checks_and_healthy_buckets():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config)
    check_counts = {}
    for row in artifacts.maxops:
        check_counts[row["check_id"]] = check_counts.get(row["check_id"], 0) + 1

    assert artifacts.inventory["inventory_count"] >= 50
    for check_id in SUPPORTED_CHECK_IDS:
        assert check_counts.get(check_id, 0) >= 2

    healthy_rows = [item for item in artifacts.maxops if item["check_id"] is None]
    assert healthy_rows
    assert all(item["recommended_action"] is None for item in healthy_rows)


def test_versioning_findings_apply_only_to_versioned_buckets():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=50, seed_override=88)
    inventory_by_id = {item["inventory_id"]: item for item in artifacts.inventory["buckets"]}

    for row in artifacts.maxops:
        if row["check_id"] not in VERSIONED_CHECKS:
            continue
        bucket = inventory_by_id[row["inventory_id"]]
        assert bucket["metadata"]["versioning_status"] == "Enabled"


def test_logging_inventory_and_replication_findings_include_targets():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=40, seed_override=91)

    by_check = {}
    for row in artifacts.maxops:
        if row["check_id"]:
            by_check.setdefault(row["check_id"], []).append(row)

    logging_row = by_check["s3_logging_enabled"][0]
    inventory_row = by_check["s3_inventory_enabled"][0]
    replication_row = by_check["s3_replication_enabled"][0]

    assert logging_row["target_config"]["logging_enabled"] is False
    assert inventory_row["target_config"]["inventory_configuration_count"] == 0
    assert replication_row["target_config"]["replication_rule_count"] == 0
    assert logging_row["available_actions"]
    assert inventory_row["available_actions"]
    assert replication_row["available_actions"]


def test_maxops_rows_do_not_duplicate_full_inventory_details():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=20, seed_override=321)
    sample_maxops = artifacts.maxops[0]

    assert "lifecycle_rules" not in sample_maxops["metadata"]
    assert "BucketDetails" not in sample_maxops["metadata"]
