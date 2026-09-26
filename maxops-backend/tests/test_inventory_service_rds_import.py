from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.inventory import MaxOpsInventory, RdsInventory
from app.services.inventory_service import SyntheticInventoryImporter


MODULE_PATH = Path(__file__).resolve().parents[1] / "synthetic_data" / "rds_syn_data.py"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "synthetic_data" / "rds_syn_config.json"


def load_generator_module():
    spec = importlib.util.spec_from_file_location("rds_syn_data", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_config():
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_rds_synthetic_inventory_import_persists_split_and_maxops_rows(tmp_path):
    module = load_generator_module()
    config = load_config()
    artifacts = module.generate_dataset(config, count_override=8, seed_override=2026)
    module.write_artifacts(artifacts, tmp_path)

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        summary = SyntheticInventoryImporter(db).import_directory("rds", tmp_path)

        assert summary["imported_rows"] == 8

        rds_rows = db.query(RdsInventory).order_by(RdsInventory.inventory_id.asc()).all()
        maxops_rows = (
            db.query(MaxOpsInventory)
            .filter(MaxOpsInventory.resource_type == "rds")
            .order_by(MaxOpsInventory.inventory_id.asc())
            .all()
        )

    assert len(rds_rows) == 8
    assert len(maxops_rows) == 8

    first_row = rds_rows[0]
    assert first_row.resource_type == "rds"
    assert first_row.engine
    assert first_row.db_instance_class
    assert first_row.avg_cpu_utilization is not None
    assert first_row.avg_connections is not None
    assert first_row.metric_history_json
    assert first_row.aws_payload_json["db_instance"]["DBInstanceIdentifier"] == first_row.resource_id
    assert "metrics" in first_row.aws_payload_json
    assert "tags" in first_row.aws_payload_json

    assert any(row.check_id == "rds_idle_databases" for row in maxops_rows)
    assert any(row.check_id == "rds_non_graviton_instance_class" for row in maxops_rows)

