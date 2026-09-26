"""Tests for the DB-oriented RDS synthetic data generator."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.services.inventory_service import SyntheticInventoryImporter
from app.services.rightsizer_service import get_rightsizer_detail, list_rightsizer_resources


MODULE_PATH = Path(__file__).resolve().parents[1] / "synthetic_data" / "rds_syn_data.py"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "synthetic_data" / "rds_syn_config.json"


def load_module():
    spec = importlib.util.spec_from_file_location("rds_syn_data", MODULE_PATH)
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


def test_inventory_document_contains_aws_payload_and_flattened_instances():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=18, seed_override=55)
    inventory = artifacts.inventory
    aws_instance = inventory["aws_payload"]["DBInstances"][0]
    flat_instance = inventory["instances"][0]

    assert set(inventory.keys()) == {
        "account_id",
        "aws_payload",
        "generated_at",
        "instances",
        "inventory_count",
    }
    assert inventory["inventory_count"] == len(inventory["instances"])
    assert inventory["inventory_count"] == len(inventory["aws_payload"]["DBInstances"])

    assert "InventoryId" in aws_instance
    assert "DBInstanceIdentifier" in aws_instance
    assert "DBInstanceArn" in aws_instance
    assert "DBInstanceClass" in aws_instance
    assert "Engine" in aws_instance
    assert "Endpoint" in aws_instance
    assert "DBSubnetGroup" in aws_instance
    assert "VpcSecurityGroups" in aws_instance
    assert "TagList" in aws_instance

    assert "inventory_id" in flat_instance
    assert "resource_id" in flat_instance
    assert "resource_name" in flat_instance
    assert "resource_type" in flat_instance
    assert "account_id" in flat_instance
    assert "region" in flat_instance
    assert "availability_zone" in flat_instance
    assert "engine" in flat_instance
    assert "db_instance_class" in flat_instance
    assert "tags" in flat_instance
    assert "metadata" in flat_instance

    metric_instance = next(
        item
        for item in inventory["instances"]
        if item["metadata"]["metric_history"]["cpuutilization"].get("average")
    )
    cpu_history = metric_instance["metadata"]["metric_history"]["cpuutilization"]
    memory_history = metric_instance["metadata"]["metric_history"]["memoryutilization"]
    read_iops_history = metric_instance["metadata"]["metric_history"]["readiops"]
    write_iops_history = metric_instance["metadata"]["metric_history"]["writeiops"]
    assert len(cpu_history["timestamps"]) == 15
    assert len(memory_history["timestamps"]) == 15
    assert len(read_iops_history["timestamps"]) == 15
    assert len(write_iops_history["timestamps"]) == 15
    assert "average" in cpu_history
    assert "average" in memory_history
    for history in (cpu_history, memory_history, read_iops_history, write_iops_history):
        assert "maximum" in history
        assert "p95" in history
        assert "p99" in history
        assert history["maximum"][0] is not None
        assert history["p95"][0] is not None
        assert history["p99"][0] is not None


def test_write_artifacts_emits_exactly_two_files(tmp_path):
    module = load_module()
    config = load_config()
    artifacts = module.generate_dataset(config, count_override=10, seed_override=8)

    module.write_artifacts(artifacts, tmp_path)

    emitted = sorted(path.name for path in tmp_path.glob("*.json"))
    assert emitted == ["inventory.json", "rds_maxops.json"]


def test_maxops_rows_join_one_to_one_with_inventory():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=32, seed_override=7)
    instance_ids = {item["inventory_id"] for item in artifacts.inventory["instances"]}
    maxops_ids = [item["inventory_id"] for item in artifacts.maxops]

    assert len(artifacts.maxops) == artifacts.inventory["inventory_count"]
    assert set(maxops_ids) == instance_ids
    assert len(maxops_ids) == len(set(maxops_ids))


def test_default_dataset_includes_variety_and_no_aurora():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config)
    classes = {item["db_instance_class"] for item in artifacts.inventory["instances"]}
    engines = {item["engine"] for item in artifacts.inventory["instances"]}
    usage_profiles = {item["metadata"]["usage_profile"] for item in artifacts.inventory["instances"]}

    assert any(module.is_graviton(item) for item in classes)
    assert any(not module.is_graviton(item) for item in classes)
    assert len(engines) >= 3
    assert usage_profiles >= {"idle_candidate", "busy_healthy", "non_graviton_candidate"}
    assert all(not engine.startswith("aurora") for engine in engines)


def test_actionable_rows_match_current_rds_checks():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=48, seed_override=88)
    by_id = {item["inventory_id"]: item for item in artifacts.inventory["instances"]}
    idle_rows = [item for item in artifacts.maxops if item["check_id"] == "rds_idle_databases"]
    non_graviton_rows = [item for item in artifacts.maxops if item["check_id"] == "rds_non_graviton_instance_class"]

    assert idle_rows
    assert non_graviton_rows
    assert all(by_id[row["inventory_id"]]["metadata"]["avg_cpu_utilization"] < 5.0 for row in idle_rows)
    assert all(by_id[row["inventory_id"]]["metadata"]["avg_connections"] < 5 for row in idle_rows)
    assert all(not module.is_graviton(by_id[row["inventory_id"]]["db_instance_class"]) for row in non_graviton_rows)


def test_outputs_do_not_leak_capture_identifiers():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=20, seed_override=91)
    rendered = json.dumps({"inventory": artifacts.inventory, "maxops": artifacts.maxops})

    assert "maxops-payload-rds-idle-graviton" not in rendered
    assert "maxops-payload-rds-non-graviton" not in rendered
    assert "123456789012" not in rendered


def test_maxops_rows_do_not_duplicate_full_metric_history():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=20, seed_override=321)
    sample_maxops = artifacts.maxops[0]

    assert "metric_history" not in sample_maxops["metadata"]


def test_rightsizer_rds_rows_are_selectable_and_have_cpu_memory_iops_detail(tmp_path):
    module = load_module()
    config = load_config()
    artifacts = module.generate_dataset(config, count_override=36, seed_override=20260419)
    module.write_artifacts(artifacts, tmp_path)

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        SyntheticInventoryImporter(db).import_directory("rds", tmp_path)
        response = list_rightsizer_resources("rds", db)

        assert response["summary"]["actionable_resources"] > 0
        assert response["summary"]["inventory_only_resources"] == 0
        assert all(row["detail_available"] for row in response["resources"])

        actionable = next(
            row for row in response["resources"] if row["classification"] == "ACTIONABLE"
        )
        detail = get_rightsizer_detail("rds", actionable["inventory_id"], db)

    assert detail["resource_type"] == "rds"
    assert detail["classification"] == "ACTIONABLE"
    assert len(detail["recommendations"]) == 3
    assert detail["recommendation"]["chart"]["points"]
    assert detail["recommendation"]["memory_chart"]["points"]
    assert detail["recommendation"]["iops_chart"]["points"]
    assert detail["chart"]["unit"] == "Percent"
    assert detail["memory_chart"]["unit"] == "Percent"
    assert detail["iops_chart"]["unit"] == "IOPS"
