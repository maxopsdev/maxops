"""Tests for the DB-oriented DynamoDB synthetic data generator."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "synthetic_data" / "dynamodb_syn_data.py"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "synthetic_data" / "dynamodb_syn_config.json"


def load_module():
    spec = importlib.util.spec_from_file_location("dynamodb_syn_data", MODULE_PATH)
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

    left = module.generate_dataset(config, count_override=18, seed_override=1234)
    right = module.generate_dataset(config, count_override=18, seed_override=1234)

    assert left.inventory == right.inventory
    assert left.maxops == right.maxops
    assert left.inventory["inventory_count"] == 18


def test_inventory_document_contains_tables_and_gsis():
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
    assert "TableNames" in inventory["aws_payload"]
    assert "TableDetails" in inventory["aws_payload"]
    assert "TagDetails" in inventory["aws_payload"]
    assert "MetricDetails" in inventory["aws_payload"]

    resource_types = {item["resource_type"] for item in inventory["resources"]}
    assert {"dynamodb_table", "dynamodb_gsi"}.issubset(resource_types)

    assert "inventory_id" in flat_resource
    assert "resource_id" in flat_resource
    assert "resource_name" in flat_resource
    assert "resource_type" in flat_resource
    assert "account_id" in flat_resource
    assert "region" in flat_resource
    assert "state" in flat_resource
    assert "tags" in flat_resource
    assert "metadata" in flat_resource


def test_write_artifacts_emits_exactly_two_files(tmp_path):
    module = load_module()
    config = load_config()
    artifacts = module.generate_dataset(config, count_override=10, seed_override=8)

    module.write_artifacts(artifacts, tmp_path)

    emitted = sorted(path.name for path in tmp_path.glob("*.json"))
    assert emitted == ["dynamodb_maxops.json", "inventory.json"]


def test_maxops_rows_join_one_to_one_with_inventory():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=22, seed_override=7)
    resource_ids = {item["inventory_id"] for item in artifacts.inventory["resources"]}
    maxops_ids = [item["inventory_id"] for item in artifacts.maxops]

    assert len(artifacts.maxops) == artifacts.inventory["inventory_count"]
    assert set(maxops_ids) == resource_ids
    assert len(maxops_ids) == len(set(maxops_ids))


def test_default_dataset_covers_billing_modes_and_findings():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config)
    billing_modes = {
        item["metadata"].get("BillingMode") or item["metadata"].get("billing_mode")
        for item in artifacts.inventory["resources"]
        if item["resource_type"] == "dynamodb_table"
    }
    check_ids = {row["check_id"] for row in artifacts.maxops}

    assert {"PROVISIONED", "PAY_PER_REQUEST"}.issubset(billing_modes)
    assert {
        "dynamodb_underutilized_rcu",
        "dynamodb_underutilized_wcu",
        "dynamodb_underutilized_tables",
        "dynamodb_gsi_unused",
        "dynamodb_best_fit_on_demand",
        "dynamodb_best_fit_provisioned",
        None,
    }.issubset(check_ids)


def test_gsi_unused_only_targets_gsi_resources():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=24, seed_override=88)
    by_id = {item["inventory_id"]: item for item in artifacts.inventory["resources"]}
    rows = [item for item in artifacts.maxops if item["check_id"] == "dynamodb_gsi_unused"]

    assert rows
    assert all(by_id[row["inventory_id"]]["resource_type"] == "dynamodb_gsi" for row in rows)


def test_billing_mode_recommendations_target_expected_table_modes():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=24, seed_override=91)
    by_id = {item["inventory_id"]: item for item in artifacts.inventory["resources"]}

    ondemand_rows = [item for item in artifacts.maxops if item["check_id"] == "dynamodb_best_fit_on_demand"]
    provisioned_rows = [item for item in artifacts.maxops if item["check_id"] == "dynamodb_best_fit_provisioned"]

    assert ondemand_rows
    assert provisioned_rows
    assert all((by_id[row["inventory_id"]]["metadata"].get("BillingMode") or by_id[row["inventory_id"]]["metadata"].get("billing_mode")) == "PROVISIONED" for row in ondemand_rows)
    assert all((by_id[row["inventory_id"]]["metadata"].get("BillingMode") or by_id[row["inventory_id"]]["metadata"].get("billing_mode")) == "PAY_PER_REQUEST" for row in provisioned_rows)


def test_outputs_do_not_leak_capture_identifiers():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=16, seed_override=222)
    rendered = json.dumps({"inventory": artifacts.inventory, "maxops": artifacts.maxops})

    assert "maxops-payload-dynamodb-provisioned" not in rendered
    assert "maxops_payload_unused_gsi" not in rendered
    assert "123456789012" not in rendered


def test_maxops_rows_do_not_duplicate_full_metric_history():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=16, seed_override=321)
    sample_maxops = artifacts.maxops[0]

    assert "metric_history" not in sample_maxops["metadata"]
