from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.pricing import PricingCache
from app.pricing.base import PricingContext
from app.pricing import pricing_ec2, pricing_elasticache
from app.services.aws_pricing_cache import AwsPricingCacheService
from app.services.street_pricing import StreetPricingService

EC2_SYNTHETIC_MODULE_PATH = Path(__file__).resolve().parents[1] / "synthetic_data" / "ec2_syn_data.py"
EC2_SYNTHETIC_CONFIG_PATH = Path(__file__).resolve().parents[1] / "synthetic_data" / "ec2_syn_config.json"


def _create_street_pricing_db(database_path: Path) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE street_pricing_ec2 (
                id INTEGER PRIMARY KEY,
                region_code TEXT NOT NULL,
                region_name TEXT NOT NULL,
                instance_type TEXT NOT NULL,
                platform TEXT NOT NULL,
                platform_normalized TEXT NOT NULL,
                hourly_usd REAL NOT NULL,
                monthly_usd REAL NOT NULL,
                currency TEXT NOT NULL,
                pretty_name TEXT,
                family TEXT,
                attributes_json TEXT NOT NULL,
                pricing_json TEXT NOT NULL
            );
            CREATE TABLE street_pricing_rds (
                id INTEGER PRIMARY KEY,
                region_code TEXT NOT NULL,
                region_name TEXT NOT NULL,
                instance_type TEXT NOT NULL,
                database_engine TEXT NOT NULL,
                engine_normalized TEXT NOT NULL,
                hourly_usd REAL NOT NULL,
                monthly_usd REAL NOT NULL,
                currency TEXT NOT NULL,
                pretty_name TEXT,
                family TEXT,
                attributes_json TEXT NOT NULL,
                pricing_json TEXT NOT NULL
            );
            CREATE TABLE street_pricing_elasticache (
                id INTEGER PRIMARY KEY,
                region_code TEXT NOT NULL,
                region_name TEXT NOT NULL,
                instance_type TEXT NOT NULL,
                cache_engine TEXT NOT NULL,
                engine_normalized TEXT NOT NULL,
                hourly_usd REAL NOT NULL,
                monthly_usd REAL NOT NULL,
                currency TEXT NOT NULL,
                pretty_name TEXT,
                family TEXT,
                attributes_json TEXT NOT NULL,
                pricing_json TEXT NOT NULL
            );
            CREATE TABLE street_pricing_redshift (
                id INTEGER PRIMARY KEY,
                region_code TEXT NOT NULL,
                region_name TEXT NOT NULL,
                instance_type TEXT NOT NULL,
                hourly_usd REAL NOT NULL,
                monthly_usd REAL NOT NULL,
                currency TEXT NOT NULL,
                pretty_name TEXT,
                family TEXT,
                attributes_json TEXT NOT NULL,
                pricing_json TEXT NOT NULL
            );
            CREATE TABLE street_pricing_opensearch (
                id INTEGER PRIMARY KEY,
                region_code TEXT NOT NULL,
                region_name TEXT NOT NULL,
                instance_type TEXT NOT NULL,
                hourly_usd REAL NOT NULL,
                monthly_usd REAL NOT NULL,
                currency TEXT NOT NULL,
                pretty_name TEXT,
                family TEXT,
                attributes_json TEXT NOT NULL,
                pricing_json TEXT NOT NULL
            );
            """
        )


def _insert_ec2_row(database_path: Path, monthly: float = 101.25) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO street_pricing_ec2 (
                region_code, region_name, instance_type, platform, platform_normalized,
                hourly_usd, monthly_usd, currency, pretty_name, family, attributes_json, pricing_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "us-east-1",
                "US East (N. Virginia)",
                "m6i.large",
                "linux",
                "linux",
                0.1387,
                monthly,
                "USD",
                "M6i Large",
                "General purpose",
                json.dumps({"vcpu": 2, "memory": 8}),
                json.dumps({"ondemand": 0.1387}),
            ),
        )
        connection.commit()


def _insert_elasticache_row(database_path: Path, monthly: float = 240.0) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO street_pricing_elasticache (
                region_code, region_name, instance_type, cache_engine, engine_normalized,
                hourly_usd, monthly_usd, currency, pretty_name, family, attributes_json, pricing_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "us-east-1",
                "US East (N. Virginia)",
                "cache.r6g.large",
                "redis",
                "redis",
                0.3288,
                monthly,
                "USD",
                "cache.r6g.large Redis",
                "memory optimized",
                json.dumps({"memory_gb": 13.07}),
                json.dumps({"ondemand": 0.3288}),
            ),
        )
        connection.commit()


def _build_session():
    engine = create_engine("sqlite:///:memory:")
    PricingCache.__table__.create(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def _build_service(street_db_path: Path) -> AwsPricingCacheService:
    service = AwsPricingCacheService.__new__(AwsPricingCacheService)
    service.pricing_client = object()
    service.street_pricing_service = StreetPricingService(street_db_path)
    return service


def _load_ec2_synthetic_module():
    spec = importlib.util.spec_from_file_location("ec2_syn_data", EC2_SYNTHETIC_MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_ec2_synthetic_config() -> dict:
    with EC2_SYNTHETIC_CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_street_pricing_service_returns_monthly_ec2_price(tmp_path: Path) -> None:
    street_db = tmp_path / "maxops_pricing.db"
    _create_street_pricing_db(street_db)
    _insert_ec2_row(street_db, monthly=88.8)

    service = StreetPricingService(street_db)
    price = service.get_price_for_resource(
        {
            "resource_type": "ec2",
            "region": "us-east-1",
            "metadata": {"InstanceType": "m6i.large"},
        }
    )

    assert price is not None
    assert price["source"] == "street_pricing"
    assert price["unit"] == "month"
    assert price["price_per_unit"] == pytest.approx(88.8)
    assert price["parameters"]["attributes"]["vcpu"] == 2


def test_aws_pricing_prefers_cur_over_street_and_aws(tmp_path: Path) -> None:
    street_db = tmp_path / "maxops_pricing.db"
    _create_street_pricing_db(street_db)
    _insert_ec2_row(street_db, monthly=88.8)
    db = _build_session()
    service = _build_service(street_db)
    service._fetch_price_from_aws = lambda params: pytest.fail("AWS pricing should not be called")

    price = service.get_price_for_resource(
        db,
        {
            "resource_type": "ec2",
            "resource_id": "i-cur-first",
            "region": "us-east-1",
            "metadata": {
                "InstanceType": "m6i.large",
                "cur_monthly_cost": 321.09,
            },
        },
    )

    assert price is not None
    assert price["source"] == "cur"
    assert price["price_per_unit"] == pytest.approx(321.09)


def test_aws_pricing_prefers_street_before_aws(tmp_path: Path) -> None:
    street_db = tmp_path / "maxops_pricing.db"
    _create_street_pricing_db(street_db)
    _insert_ec2_row(street_db, monthly=111.0)
    db = _build_session()
    service = _build_service(street_db)
    service._fetch_price_from_aws = lambda params: pytest.fail("AWS pricing should not be called")

    price = service.get_price_for_resource(
        db,
        {
            "resource_type": "ec2",
            "resource_id": "i-street-first",
            "region": "us-east-1",
            "metadata": {"InstanceType": "m6i.large"},
        },
    )

    assert price is not None
    assert price["source"] == "street_pricing"
    assert price["price_per_unit"] == pytest.approx(111.0)


def test_aws_pricing_falls_back_to_public_api_when_street_missing(tmp_path: Path) -> None:
    street_db = tmp_path / "maxops_pricing.db"
    _create_street_pricing_db(street_db)
    db = _build_session()
    service = _build_service(street_db)
    service._fetch_price_from_aws = lambda params: (0.2, "Hrs")

    price = service.get_price_for_resource(
        db,
        {
            "resource_type": "ec2",
            "resource_id": "i-aws-fallback",
            "region": "us-east-1",
            "metadata": {"InstanceType": "m6i.large"},
        },
    )

    assert price is not None
    assert price["source"] == "aws_pricing"
    assert price["price_per_unit"] == pytest.approx(0.2)
    cached_rows = db.query(PricingCache).all()
    assert len(cached_rows) == 1


def test_handle_ec2_pricing_keeps_cur_for_existing_resource_and_uses_street_for_new_ones(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    street_db = tmp_path / "maxops_pricing.db"
    _create_street_pricing_db(street_db)
    _insert_ec2_row(street_db, monthly=111.0)
    db = _build_session()
    service = _build_service(street_db)
    service._fetch_price_from_aws = lambda params: pytest.fail("AWS pricing should not be called")
    monkeypatch.setattr(pricing_ec2, "AwsPricingCacheService", lambda: service)

    generator = _load_ec2_synthetic_module()
    config = _load_ec2_synthetic_config()
    artifacts = generator.generate_dataset(config, count_override=1, seed_override=1234)
    existing_resource = artifacts.inventory["instances"][0]
    existing_resource["metadata"]["cur_monthly_cost"] = 321.09
    existing_resource["metadata"]["pricing_source"] = "cur"
    existing_resource["metadata"]["InstanceType"] = existing_resource["instance_type"]

    new_street_resources = [
        {
            "resource_type": "ec2",
            "resource_id": "i-street-new-1",
            "region": "us-east-1",
            "metadata": {"InstanceType": "m6i.large"},
        },
        {
            "resource_type": "ec2",
            "resource_id": "i-street-new-2",
            "region": "us-east-1",
            "metadata": {"instance_type": "m6i.large"},
        },
    ]
    resources = [existing_resource, *new_street_resources]

    pricing_ec2.handle_ec2_pricing(
        PricingContext(
            check_id="ec2_unused_instances",
            resources=resources,
            db=db,
            savings_ratio=0.7,
        )
    )

    assert resources[0]["pricing"]["source"] == "cur"
    assert resources[0]["metadata"]["estimated_monthly_cost"] == pytest.approx(321.09)
    assert resources[1]["pricing"]["source"] == "street_pricing"
    assert resources[1]["metadata"]["estimated_monthly_cost"] == pytest.approx(111.0)
    assert resources[2]["pricing"]["source"] == "street_pricing"
    assert resources[2]["metadata"]["estimated_monthly_cost"] == pytest.approx(111.0)


def test_handle_elasticache_pricing_uses_street_pricing_for_new_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    street_db = tmp_path / "maxops_pricing.db"
    _create_street_pricing_db(street_db)
    _insert_elasticache_row(street_db, monthly=240.0)
    db = _build_session()
    service = _build_service(street_db)
    service._fetch_price_from_aws = lambda params: pytest.fail("AWS pricing should not be called")
    monkeypatch.setattr(pricing_elasticache, "AwsPricingCacheService", lambda: service)

    resources = [
        {
            "resource_type": "elasticache_cluster",
            "resource_id": "cache-street-new-1",
            "region": "us-east-1",
            "metadata": {
                "CacheNodeType": "cache.r6g.large",
                "Engine": "redis",
                "NumCacheNodes": 2,
            },
        },
        {
            "resource_type": "elasticache_replication_group",
            "resource_id": "cache-street-new-2",
            "region": "us-east-1",
            "metadata": {
                "cache_node_type": "cache.r6g.large",
                "engine": "redis",
                "MemberClusters": ["0001", "0002", "0003"],
            },
        },
    ]

    pricing_elasticache.handle_elasticache_pricing(
        PricingContext(
            check_id="elasticache_low_item_count",
            resources=resources,
            db=db,
            savings_ratio=1.0,
        )
    )

    assert resources[0]["pricing"]["source"] == "street_pricing"
    assert resources[0]["metadata"]["estimated_monthly_cost"] == pytest.approx(480.0)
    assert resources[1]["pricing"]["source"] == "street_pricing"
    assert resources[1]["metadata"]["estimated_monthly_cost"] == pytest.approx(720.0)
