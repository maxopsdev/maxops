"""Tests for the DB-oriented EBS synthetic data generator."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "synthetic_data" / "ebs_syn_data.py"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "synthetic_data" / "ebs_syn_config.json"


def load_module():
    spec = importlib.util.spec_from_file_location("ebs_syn_data", MODULE_PATH)
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


def test_inventory_document_contains_aws_payload_and_flattened_volumes():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=12, seed_override=55)
    inventory = artifacts.inventory
    aws_volume = inventory["aws_payload"]["Volumes"][0]
    flat_volume = inventory["volumes"][0]

    assert set(inventory.keys()) == {
        "account_id",
        "aws_payload",
        "generated_at",
        "inventory_count",
        "volumes",
    }
    assert inventory["inventory_count"] == len(inventory["volumes"])
    assert inventory["inventory_count"] == len(inventory["aws_payload"]["Volumes"])

    assert "InventoryId" in aws_volume
    assert "VolumeId" in aws_volume
    assert "VolumeType" in aws_volume
    assert "Size" in aws_volume
    assert "Attachments" in aws_volume

    assert "inventory_id" in flat_volume
    assert "resource_id" in flat_volume
    assert "resource_name" in flat_volume
    assert "resource_type" in flat_volume
    assert "account_id" in flat_volume
    assert "region" in flat_volume
    assert "availability_zone" in flat_volume
    assert "state" in flat_volume
    assert "attached" in flat_volume
    assert "tags" in flat_volume
    assert "metadata" in flat_volume
    assert "metric_history" in flat_volume["metadata"]


def test_write_artifacts_emits_exactly_two_files(tmp_path):
    module = load_module()
    config = load_config()
    artifacts = module.generate_dataset(config, count_override=10, seed_override=8)

    module.write_artifacts(artifacts, tmp_path)

    emitted = sorted(path.name for path in tmp_path.glob("*.json"))
    assert emitted == ["ebs_maxops.json", "inventory.json"]


def test_maxops_rows_join_one_to_one_with_inventory():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=24, seed_override=7)
    volume_ids = {item["inventory_id"] for item in artifacts.inventory["volumes"]}
    maxops_ids = [item["inventory_id"] for item in artifacts.maxops]

    assert len(artifacts.maxops) == artifacts.inventory["inventory_count"]
    assert set(maxops_ids) == volume_ids
    assert len(maxops_ids) == len(set(maxops_ids))


def test_default_dataset_covers_volume_variety_and_findings():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config)
    volume_types = {item["metadata"]["volume_type"] for item in artifacts.inventory["volumes"]}
    check_ids = {row["check_id"] for row in artifacts.maxops}

    assert {"gp2", "gp3", "io1", "io2"}.issubset(volume_types)
    assert {
        "ebs_underutilized_volume",
        "ebs_large_volumes_low_utilization",
        "ebs_iops_overprovisioned_volume",
        "ebs_underutilized_provisioned_iops",
        None,
    }.issubset(check_ids)


def test_provisioned_iops_findings_only_target_io_families():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=28, seed_override=88)
    by_id = {item["inventory_id"]: item for item in artifacts.inventory["volumes"]}
    rows = [item for item in artifacts.maxops if item["check_id"] == "ebs_underutilized_provisioned_iops"]

    assert rows
    assert all(by_id[row["inventory_id"]]["metadata"]["volume_type"] in {"io1", "io2"} for row in rows)


def test_large_volume_findings_only_target_large_volumes():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=24, seed_override=91)
    by_id = {item["inventory_id"]: item for item in artifacts.inventory["volumes"]}
    rows = [item for item in artifacts.maxops if item["check_id"] == "ebs_large_volumes_low_utilization"]

    assert rows
    assert all(by_id[row["inventory_id"]]["metadata"]["size"] >= 500 for row in rows)


def test_outputs_do_not_leak_capture_identifiers():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=16, seed_override=222)
    rendered = json.dumps({"inventory": artifacts.inventory, "maxops": artifacts.maxops})

    assert "vol-ATTACHEDGP2" not in rendered
    assert "vol-PROVISIONEDIO1" not in rendered
    assert "123456789012" not in rendered


def test_maxops_rows_do_not_duplicate_full_metric_history():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=16, seed_override=321)
    sample_maxops = artifacts.maxops[0]

    assert "metric_history" not in sample_maxops["metadata"]
