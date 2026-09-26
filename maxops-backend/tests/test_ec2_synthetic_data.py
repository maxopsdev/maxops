"""Tests for the DB-oriented EC2 synthetic data generator."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.services.ec2_rightsizer import EC2Rightsizer, EC2ScopePolicy
from app.services.inventory_service import SyntheticInventoryImporter
from app.services.rightsizer_service import get_rightsizer_detail
from rightsizers.ec2.ec2_rightsizer.models import PerformanceWarningPolicy


MODULE_PATH = Path(__file__).resolve().parents[1] / "synthetic_data" / "ec2_syn_data.py"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "synthetic_data" / "ec2_syn_config.json"


def load_module():
    spec = importlib.util.spec_from_file_location("ec2_syn_data", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
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


def test_inventory_document_contains_aws_payload_and_flattened_instances():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=12, seed_override=55)
    inventory = artifacts.inventory
    aws_instance = inventory["aws_payload"]["Reservations"][0]["Instances"][0]
    flat_instance = inventory["instances"][0]

    assert set(inventory.keys()) == {
        "account_id",
        "aws_payload",
        "generated_at",
        "instances",
        "inventory_count",
    }
    assert inventory["inventory_count"] == len(inventory["instances"])
    assert inventory["inventory_count"] == len(inventory["aws_payload"]["Reservations"])

    assert "InventoryId" in aws_instance
    assert "InstanceId" in aws_instance
    assert "InstanceType" in aws_instance
    assert "State" in aws_instance
    assert "Placement" in aws_instance
    assert "Tags" in aws_instance
    assert "VpcId" in aws_instance
    assert "SubnetId" in aws_instance
    assert "SecurityGroups" in aws_instance
    assert "LaunchTime" in aws_instance

    assert "inventory_id" in flat_instance
    assert "resource_id" in flat_instance
    assert "resource_name" in flat_instance
    assert "resource_type" in flat_instance
    assert "account_id" in flat_instance
    assert "region" in flat_instance
    assert "availability_zone" in flat_instance
    assert "state" in flat_instance
    assert "instance_type" in flat_instance
    assert "launch_time" in flat_instance
    assert "tags" in flat_instance
    assert "metadata" in flat_instance
    assert "metric_history" in flat_instance["metadata"]
    assert "rightsizing_metrics" in flat_instance["metadata"]
    cpu_history = flat_instance["metadata"]["metric_history"]["cpuutilization"]
    assert len(cpu_history["timestamps"]) == 15
    assert len(cpu_history["average"]) == 15
    assert len(cpu_history["maximum"]) == 15
    assert len(cpu_history["p90"]) == 15
    assert len(cpu_history["p95"]) == 15
    assert len(cpu_history["p99"]) == 15
    normalized = flat_instance["metadata"]["rightsizing_metrics"]["30d"]["normalized"]
    assert normalized["cpu_percent"]["p99"] is not None
    assert normalized["memory_percent"]["p99"] is not None


def test_write_artifacts_emits_exactly_two_files(tmp_path):
    module = load_module()
    config = load_config()
    artifacts = module.generate_dataset(config, count_override=10, seed_override=8)

    module.write_artifacts(artifacts, tmp_path)

    emitted = sorted(path.name for path in tmp_path.glob("*.json"))
    assert emitted == ["ec2_maxops.json", "inventory.json"]


def test_maxops_rows_join_one_to_one_with_inventory():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=80, seed_override=7)
    instance_ids = {item["inventory_id"] for item in artifacts.inventory["instances"]}
    maxops_ids = [item["inventory_id"] for item in artifacts.maxops]

    assert len(artifacts.maxops) == artifacts.inventory["inventory_count"]
    assert set(maxops_ids) == instance_ids
    assert len(maxops_ids) == len(set(maxops_ids))


def test_stopped_instances_map_to_unused_style_maxops_rows():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=120, seed_override=19)
    by_inventory_id = {item["inventory_id"]: item for item in artifacts.maxops}
    stopped_rows = [
        by_inventory_id[item["inventory_id"]]
        for item in artifacts.inventory["instances"]
        if item["state"] == "stopped"
    ]

    assert stopped_rows
    assert all(item["check_id"] == "ec2_unused_instances" for item in stopped_rows)


def test_stopped_instances_generate_low_cpu_spectrum_not_constant_zero():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=160, seed_override=19)
    stopped_instances = [
        item for item in artifacts.inventory["instances"]
        if item["state"] == "stopped" and item["metadata"]["usage_profile"] == "stopped_unused"
    ]

    assert stopped_instances

    cpu_values = [item["metadata"]["avg_cpu_utilization"] for item in stopped_instances]
    network_in_values = [item["metadata"]["avg_network_in"] for item in stopped_instances]
    network_out_values = [item["metadata"]["avg_network_out"] for item in stopped_instances]

    assert all(0.05 <= value <= 0.9 for value in cpu_values)
    assert len(set(cpu_values)) > 1
    assert all(value == 0.0 for value in network_in_values)
    assert all(value == 0.0 for value in network_out_values)


def test_graviton_findings_apply_only_to_non_graviton_instances():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=160, seed_override=88)
    instances_by_id = {
        item["inventory_id"]: item for item in artifacts.inventory["instances"]
    }
    graviton_rows = [item for item in artifacts.maxops if item["check_id"] == "ec2_graviton_candidate"]

    assert graviton_rows
    assert all(
        not module.is_graviton(instances_by_id[item["inventory_id"]]["instance_type"])
        for item in graviton_rows
    )


def test_healthy_instances_still_get_neutral_maxops_rows():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=60, seed_override=123)
    healthy_rows = [item for item in artifacts.maxops if item["check_id"] is None]

    assert healthy_rows
    assert all(item["recommended_action"] is None for item in healthy_rows)
    assert all(item["metadata"]["status"] == "healthy" for item in healthy_rows)


def test_maxops_rows_do_not_duplicate_full_metric_history():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=20, seed_override=321)
    sample_maxops = artifacts.maxops[0]

    assert "metric_history" not in sample_maxops["metadata"]


def test_required_examples_exercise_distinct_ec2_rightsizer_trends(tmp_path):
    module = load_module()
    config = load_config()
    artifacts = module.generate_dataset(config, count_override=12)
    module.write_artifacts(artifacts, tmp_path)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        SyntheticInventoryImporter(db).import_directory("ec2", tmp_path)
        engine_rows = EC2Rightsizer(
            db,
            warning_policy=PerformanceWarningPolicy(),
            scope_policy=EC2ScopePolicy(),
        ).list_recommendations(state="")
        tiered_rows = [
            row
            for row in engine_rows
            if all(isinstance((row.get("tiers") or {}).get(key), dict) for key in ("conservative", "balanced", "aggressive"))
        ]
        detail = get_rightsizer_detail("ec2", 1, db)

    assert tiered_rows
    targets = [
        detail["tiers"][key]["target_instance_type"]
        for key in ("conservative", "balanced", "aggressive")
    ]
    scale_ratios = [
        detail["tiers"][key]["chart"]["points"][0]["scale_ratio"]
        for key in ("conservative", "balanced", "aggressive")
    ]
    memory_scale_ratios = [
        detail["tiers"][key]["memory_chart"]["points"][0]["scale_ratio"]
        for key in ("conservative", "balanced", "aggressive")
    ]
    assert len(set(targets)) >= 2
    assert len({round(float(value), 4) for value in scale_ratios if value is not None}) >= 2
    assert len({round(float(value), 4) for value in memory_scale_ratios if value is not None}) >= 2
