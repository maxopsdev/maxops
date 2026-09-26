"""Tests for the DB-oriented ASG synthetic data generator."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.inventory import AsgInventory
from app.services.asg_rightsizer import ASGRightsizer
from app.services.rightsizer_service import get_rightsizer_detail


MODULE_PATH = Path(__file__).resolve().parents[1] / "synthetic_data" / "asg_syn_data.py"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "synthetic_data" / "asg_syn_config.json"


def load_module():
    spec = importlib.util.spec_from_file_location("asg_syn_data", MODULE_PATH)
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


def test_inventory_document_contains_aws_payload_and_flattened_resources():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=12, seed_override=55)
    inventory = artifacts.inventory
    aws_group = inventory["aws_payload"]["AutoScalingGroups"][0]
    flat_resource = inventory["resources"][0]

    assert set(inventory.keys()) == {
        "account_id",
        "aws_payload",
        "generated_at",
        "inventory_count",
        "resources",
    }
    assert inventory["inventory_count"] == len(inventory["resources"])
    assert inventory["inventory_count"] == len(inventory["aws_payload"]["AutoScalingGroups"])
    assert "MetricDetails" in inventory["aws_payload"]

    assert "InventoryId" in aws_group
    assert "AutoScalingGroupName" in aws_group
    assert "MinSize" in aws_group
    assert "DesiredCapacity" in aws_group
    assert "MaxSize" in aws_group
    assert "Instances" in aws_group
    assert "LaunchTemplate" in aws_group

    assert flat_resource["resource_type"] == "asg"
    assert "inventory_id" in flat_resource
    assert "resource_id" in flat_resource
    assert "resource_name" in flat_resource
    assert "account_id" in flat_resource
    assert "region" in flat_resource
    assert "state" in flat_resource
    assert "min_size" in flat_resource
    assert "desired_capacity" in flat_resource
    assert "max_size" in flat_resource
    assert "instance_type" in flat_resource
    assert "platform_normalized" in flat_resource
    assert "tags" in flat_resource
    assert "metadata" in flat_resource
    assert "metric_history" in flat_resource["metadata"]
    assert "memoryutilization" in flat_resource["metadata"]["metric_history"]
    assert "rightsizing_metrics" in flat_resource["metadata"]
    assert "telemetry_summary" in flat_resource["metadata"]
    assert "effective_instance_types" in flat_resource["metadata"]
    assert "in_service_instance_ids" in flat_resource["metadata"]


def test_write_artifacts_emits_exactly_two_files(tmp_path):
    module = load_module()
    config = load_config()
    artifacts = module.generate_dataset(config, count_override=10, seed_override=8)

    module.write_artifacts(artifacts, tmp_path)

    emitted = sorted(path.name for path in tmp_path.glob("*.json"))
    assert emitted == ["asg_maxops.json", "inventory.json"]


def test_maxops_rows_join_one_to_one_with_inventory():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=30, seed_override=7)
    resource_ids = {item["inventory_id"] for item in artifacts.inventory["resources"]}
    maxops_ids = [item["inventory_id"] for item in artifacts.maxops]

    assert len(artifacts.maxops) == artifacts.inventory["inventory_count"]
    assert set(maxops_ids) == resource_ids
    assert len(maxops_ids) == len(set(maxops_ids))


def test_default_dataset_covers_all_asg_checks_and_healthy_rows():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config)
    check_ids = {row["check_id"] for row in artifacts.maxops}

    expected = {
        "asg_low_cpu_overprovisioned",
        "asg_idle_capacity_high",
        "asg_low_traffic_with_running_instances",
        "asg_non_graviton_candidates",
        "asg_on_demand_heavy_mix",
        "asg_launch_template_old_generation",
        "asg_scale_in_never_triggered",
        "asg_scheduled_scaling_mismatch",
        "asg_warm_pool_oversized",
        None,
    }
    assert expected.issubset(check_ids)


def test_default_dataset_covers_asg_rightsizer_outcomes():
    module = load_module()
    config = load_config()
    artifacts = module.generate_dataset(config)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        for row in artifacts.inventory["resources"]:
            db.add(
                AsgInventory(
                    inventory_id=row["inventory_id"],
                    resource_id=row["resource_id"],
                    resource_name=row["resource_name"],
                    resource_type="asg",
                    account_id=row["account_id"],
                    region=row["region"],
                    state=row["state"],
                    min_size=row["min_size"],
                    desired_capacity=row["desired_capacity"],
                    max_size=row["max_size"],
                    instance_type=row["instance_type"],
                    platform_normalized=row["platform_normalized"],
                    tags_json=row["tags"],
                    metadata_json=row["metadata"],
                )
            )
        db.commit()

        rows = ASGRightsizer(db).list_recommendations(state="")
        classifications = {row.get("classification") for row in rows}
        reason_codes = {
            code
            for row in rows
            for code in [
                *(row.get("deferred_reason_codes") or []),
                *(row.get("blocking_reasons") or []),
            ]
        }

        assert {
            "ACTIONABLE",
            "CONDITIONAL",
            "PREVIEW",
            "DEFERRED",
            "INSUFFICIENT_DATA",
            None,
        }.issubset(classifications)
        assert "HETEROGENEOUS_INSTANCE_TYPES_UNSUPPORTED" in reason_codes
        assert "WARM_POOL_REQUIRES_SEPARATE_OPTIMIZATION" in reason_codes
        assert "SCHEDULED_SCALING_REQUIRES_SEPARATE_OPTIMIZATION" in reason_codes
        assert "ASG_CPU_METRIC_UNAVAILABLE" in reason_codes

        detail = ASGRightsizer(db).get_recommendation(1)
        tier_targets = [
            detail["tiers"][key]["target_desired_capacity"]
            for key in ("conservative", "balanced", "aggressive")
        ]
        assert tier_targets == [5, 4, 3]
        api_detail = get_rightsizer_detail("asg", 1, db)
        assert api_detail["memory_chart"]["points"]
    finally:
        db.close()


def test_check_specific_realism_guards_hold():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=36, seed_override=88)
    resources_by_id = {
        item["inventory_id"]: item for item in artifacts.inventory["resources"]
    }

    for row in artifacts.maxops:
        resource = resources_by_id[row["inventory_id"]]
        metadata = resource["metadata"]
        if row["check_id"] == "asg_non_graviton_candidates":
            assert not module.is_graviton(metadata["instance_type"])
        if row["check_id"] == "asg_launch_template_old_generation":
            assert metadata["old_generation_instance_types"]
            assert module.parse_generation(metadata["instance_type"]) < 6
        if row["check_id"] == "asg_on_demand_heavy_mix":
            assert metadata["on_demand_ratio"] >= 0.8
        if row["check_id"] == "asg_warm_pool_oversized":
            assert metadata["warm_pool_instances"] >= 2
        if row["check_id"] == "asg_low_traffic_with_running_instances":
            assert metadata["network_gb_total"] <= 1.0


def test_healthy_rows_still_get_neutral_maxops_records():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=20, seed_override=321)
    healthy_rows = [row for row in artifacts.maxops if row["check_id"] is None]

    assert healthy_rows
    assert all(row["recommended_action"] is None for row in healthy_rows)
    assert all(row["metadata"]["status"] == "healthy" for row in healthy_rows)
