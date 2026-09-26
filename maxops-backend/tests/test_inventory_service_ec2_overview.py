from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.services.inventory_service import SyntheticInventoryImporter, get_ec2_inventory_overview


MODULE_PATH = Path(__file__).resolve().parents[1] / "synthetic_data" / "ec2_syn_data.py"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "synthetic_data" / "ec2_syn_config.json"


def load_generator_module():
    spec = importlib.util.spec_from_file_location("ec2_syn_data", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_config():
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_ec2_overview_uses_persisted_metric_history(tmp_path):
    module = load_generator_module()
    config = load_config()
    artifacts = module.generate_dataset(config, count_override=6, seed_override=2026)
    module.write_artifacts(artifacts, tmp_path)

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        summary = SyntheticInventoryImporter(db).import_directory("ec2", tmp_path)
        assert summary["imported_rows"] == 6

        overview = get_ec2_inventory_overview(db)

    assert len(overview["instances"]) == 6

    first_instance = overview["instances"][0]
    cpu_trend = first_instance["usage"]["trends"]["cpu"]
    memory_trend = first_instance["usage"]["trends"]["memory"]

    assert cpu_trend["points"]
    assert memory_trend["points"]
    assert len(cpu_trend["points"]) == 15
    assert len(memory_trend["points"]) == 15
    assert set(cpu_trend["points"][0].keys()) == {"timestamp", "average", "maximum", "p90", "p95", "p99"}
    assert first_instance["usage"]["memory_utilization"] >= 0.0
