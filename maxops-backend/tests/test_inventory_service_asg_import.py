from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.inventory import AsgInventory, MaxOpsInventory
from app.services.inventory_service import SyntheticInventoryImporter


MODULE_PATH = Path(__file__).resolve().parents[1] / "synthetic_data" / "asg_syn_data.py"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "synthetic_data" / "asg_syn_config.json"


def load_generator_module():
    spec = importlib.util.spec_from_file_location("asg_syn_data", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_config():
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_asg_synthetic_inventory_import_persists_split_and_maxops_rows(tmp_path):
    module = load_generator_module()
    config = load_config()
    artifacts = module.generate_dataset(config, count_override=12, seed_override=2027)
    module.write_artifacts(artifacts, tmp_path)

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        summary = SyntheticInventoryImporter(db).import_directory("asg", tmp_path)

        assert summary["imported_rows"] == 12

        asg_rows = db.query(AsgInventory).order_by(AsgInventory.inventory_id.asc()).all()
        maxops_rows = (
            db.query(MaxOpsInventory)
            .filter(MaxOpsInventory.resource_type == "asg")
            .order_by(MaxOpsInventory.inventory_id.asc())
            .all()
        )

    assert len(asg_rows) == 12
    assert len(maxops_rows) == 12

    first_row = asg_rows[0]
    assert first_row.resource_type == "asg"
    assert first_row.account_id
    assert first_row.region
    assert first_row.min_size >= 0
    assert first_row.desired_capacity >= 0
    assert first_row.max_size >= first_row.desired_capacity
    assert first_row.instance_type
    assert first_row.metadata_json
    assert first_row.aws_payload_json["AutoScalingGroupName"] == first_row.resource_id

    imported_check_ids = {row.check_id for row in maxops_rows}
    assert "asg_low_cpu_overprovisioned" in imported_check_ids
    assert "asg_idle_capacity_high" in imported_check_ids
    assert "asg_non_graviton_candidates" in imported_check_ids
    assert None in imported_check_ids
