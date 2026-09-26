from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.inventory import ElasticacheInventory, MaxOpsInventory
from app.services.inventory_service import SyntheticInventoryImporter


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "synthetic_data" / "elasticache_syn_data.py"
)
CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "synthetic_data"
    / "elasticache_syn_config.json"
)


def load_generator_module():
    spec = importlib.util.spec_from_file_location("elasticache_syn_data", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_config():
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_elasticache_synthetic_inventory_import_persists_split_and_maxops_rows(
    tmp_path,
):
    module = load_generator_module()
    config = load_config()
    artifacts = module.generate_dataset(config, count_override=10, seed_override=2027)
    module.write_artifacts(artifacts, tmp_path)
    inventory_path = tmp_path / "inventory.json"
    inventory_doc = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory_doc["resources"].append(
        {
            "inventory_id": 999999,
            "resource_id": "legacy-member-001",
            "resource_name": "legacy-member-001",
            "resource_type": "elasticache",
            "region": "us-east-1",
            "state": "available",
            "metadata": {"ReplicationGroupId": "canonical-group"},
        }
    )
    inventory_path.write_text(json.dumps(inventory_doc), encoding="utf-8")

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        summary = SyntheticInventoryImporter(db).import_directory(
            "elasticache", tmp_path
        )

        assert summary["imported_rows"] == 10
        assert summary["skipped_member_rows"] == 1

        elasticache_rows = (
            db.query(ElasticacheInventory)
            .order_by(ElasticacheInventory.inventory_id.asc())
            .all()
        )
        maxops_rows = (
            db.query(MaxOpsInventory)
            .filter(MaxOpsInventory.resource_type == "elasticache")
            .order_by(MaxOpsInventory.inventory_id.asc())
            .all()
        )

    assert len(elasticache_rows) == 10
    assert len(maxops_rows) == 10

    first_row = elasticache_rows[0]
    assert first_row.resource_type in {
        "elasticache_cluster",
        "elasticache_replication_group",
    }
    assert first_row.engine
    assert first_row.engine_version
    assert first_row.cache_node_type
    assert first_row.metric_history_json
    assert "resource" in first_row.aws_payload_json
    assert "metrics" in first_row.aws_payload_json

    imported_types = {row.resource_type for row in elasticache_rows}
    assert imported_types == {"elasticache_cluster", "elasticache_replication_group"}
    assert any(row.check_id == "elasticache_low_item_count" for row in maxops_rows)
    assert any(
        row.check_id == "elasticache_non_graviton_instance_class" for row in maxops_rows
    )
    assert any(
        row.check_id == "elasticache_redis_convertible_to_valkey" for row in maxops_rows
    )
