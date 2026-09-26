"""Tests for the DB-oriented ECS synthetic data generator."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.inventory import MaxOpsInventory
from app.services.inventory_service import SyntheticInventoryImporter
from app.services.rightsizer_service import get_rightsizer_detail, list_rightsizer_resources


MODULE_PATH = Path(__file__).resolve().parents[1] / "synthetic_data" / "ecs_syn_data.py"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "synthetic_data" / "ecs_syn_config.json"


def load_module():
    spec = importlib.util.spec_from_file_location("ecs_syn_data", MODULE_PATH)
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

    left = module.generate_dataset(config, count_override=12, seed_override=1234)
    right = module.generate_dataset(config, count_override=12, seed_override=1234)

    assert left.inventory == right.inventory
    assert left.maxops == right.maxops
    assert left.inventory["inventory_count"] == 12


def test_inventory_document_contains_resources_and_maxops_rows():
    module = load_module()
    config = load_config()

    artifacts = module.generate_dataset(config, count_override=10, seed_override=55)
    inventory = artifacts.inventory
    resource = inventory["resources"][0]

    assert set(inventory.keys()) == {
        "account_id",
        "generated_at",
        "inventory_count",
        "resources",
    }
    assert inventory["inventory_count"] == len(inventory["resources"])
    assert len(artifacts.maxops) == len(inventory["resources"])
    assert resource["resource_type"] in {"ecs", "ecs_service", "ecs_cluster"}
    assert "aws_payload" in resource
    assert "metric_history" in resource["metadata"]
    assert "desired_count" in resource["metadata"]
    assert "cpu_reservation" in resource["metadata"]


def test_write_artifacts_emits_exactly_two_files(tmp_path):
    module = load_module()
    config = load_config()
    artifacts = module.generate_dataset(config, count_override=10, seed_override=8)

    module.write_artifacts(artifacts, tmp_path)

    emitted = sorted(path.name for path in tmp_path.glob("*.json"))
    assert emitted == ["ecs_maxops.json", "inventory.json"]


def test_importer_persists_ecs_subtypes_for_rightsizer(tmp_path):
    module = load_module()
    config = load_config()
    artifacts = module.generate_dataset(config, count_override=15, seed_override=2026)
    module.write_artifacts(artifacts, tmp_path)

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        summary = SyntheticInventoryImporter(db).import_directory("ecs", tmp_path)

        assert summary["imported_rows"] == 15
        rows = (
            db.query(MaxOpsInventory)
            .filter(MaxOpsInventory.resource_type.in_(("ecs", "ecs_service", "ecs_cluster")))
            .order_by(MaxOpsInventory.inventory_id.asc())
            .all()
        )

    assert len(rows) == 15
    assert {row.resource_type for row in rows} >= {"ecs", "ecs_service", "ecs_cluster"}
    assert any(row.check_id for row in rows)
    assert any(row.check_id is None for row in rows)


def test_rightsizer_ecs_rows_are_selectable_and_have_detail_payload(tmp_path):
    module = load_module()
    config = load_config()
    artifacts = module.generate_dataset(config, count_override=15, seed_override=2026)
    module.write_artifacts(artifacts, tmp_path)

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        SyntheticInventoryImporter(db).import_directory("ecs", tmp_path)
        response = list_rightsizer_resources("ecs", db)

        assert response["summary"]["actionable_resources"] > 0
        assert response["summary"]["inventory_only_resources"] == 0
        assert all(row["detail_available"] for row in response["resources"])

        actionable = next(
            row for row in response["resources"] if row["classification"] == "ACTIONABLE"
        )
        detail = get_rightsizer_detail("ecs", actionable["inventory_id"], db)

    assert detail["resource_type"] == "ecs"
    assert detail["classification"] == "ACTIONABLE"
    assert detail["recommendation"]["target_instance_type"]
    assert sorted(
        key for key, value in detail["tiers"].items() if isinstance(value, dict)
    ) == ["aggressive", "balanced", "conservative"]
    scale_ratios = [
        detail["tiers"][key]["chart"]["points"][0]["scale_ratio"]
        for key in ("conservative", "balanced", "aggressive")
    ]
    memory_scale_ratios = [
        detail["tiers"][key]["memory_chart"]["points"][0]["scale_ratio"]
        for key in ("conservative", "balanced", "aggressive")
    ]
    assert len({round(float(value), 4) for value in scale_ratios if value is not None}) == 3
    assert len({round(float(value), 4) for value in memory_scale_ratios if value is not None}) == 3
    assert detail["memory_chart"]["points"]
    assert detail["chart"]["points"]
    assert detail["utilization_bars"]
