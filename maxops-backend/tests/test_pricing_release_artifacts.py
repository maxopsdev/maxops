from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from staging_pricing.release_utils import (
    build_db_metadata,
    atomic_replace,
    compress_xz,
    decompress_xz,
    parse_sha256_text,
    sha256_file,
    temporary_file_path,
    write_sha256_file,
)
from staging_pricing.package_pricing_release import (
    require_ec2_capability_catalog,
    require_rds_rightsizer_catalog,
)
from staging_pricing.import_rds_rightsizer_catalog import import_catalog
from rightsizers.rds.rds_rightsizer.catalogs import RdsClassCatalog


def test_release_utils_round_trip_compression(tmp_path: Path) -> None:
    source = tmp_path / "sample.db"
    source.write_bytes(b"pricing-data" * 128)
    compressed = tmp_path / "sample.db.xz"
    restored = tmp_path / "restored.db"

    compress_xz(source, compressed, preset=1)
    decompress_xz(compressed, restored)

    assert restored.read_bytes() == source.read_bytes()


def test_sha256_file_format_helpers(tmp_path: Path) -> None:
    target = tmp_path / "artifact.xz"
    target.write_bytes(b"artifact-bytes")
    digest = sha256_file(target)
    sha_path = tmp_path / "artifact.xz.sha256"

    write_sha256_file(sha_path, digest, target.name)

    assert parse_sha256_text(sha_path.read_text()) == digest


def test_atomic_replace_and_temp_file(tmp_path: Path) -> None:
    temp_path = temporary_file_path(tmp_path, ".db")
    temp_path.write_bytes(b"new-bytes")
    target = tmp_path / "target.db"
    target.write_bytes(b"old-bytes")

    atomic_replace(temp_path, target)

    assert target.read_bytes() == b"new-bytes"
    assert not temp_path.exists()


def test_build_db_metadata_counts_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "maxops_pricing.db"
    with sqlite3.connect(db_path) as connection:
        for table_name in (
            "street_pricing_ec2",
            "street_pricing_rds",
            "street_pricing_elasticache",
            "street_pricing_redshift",
            "street_pricing_opensearch",
        ):
            connection.execute(f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY)")
            connection.executemany(f"INSERT INTO {table_name} DEFAULT VALUES", [(), ()])
        connection.commit()

    metadata = build_db_metadata(db_path, ["us-east-1", "us-west-2"])
    assert metadata["regions"] == ["us-east-1", "us-west-2"]
    assert metadata["tables"]["street_pricing_ec2"]["row_count"] == 2
    assert metadata["tables"]["ec2_instance_specs"]["row_count"] == 0
    assert metadata["tables"]["rds_instance_specs"]["row_count"] == 0


def test_pricing_release_requires_structured_ec2_capabilities() -> None:
    with pytest.raises(SystemExit, match="capability catalog is empty"):
        require_ec2_capability_catalog(
            {"tables": {"ec2_instance_specs": {"row_count": 0}}}
        )
    require_ec2_capability_catalog({"tables": {"ec2_instance_specs": {"row_count": 1}}})


def test_rds_catalog_import_preserves_decimal_prices_and_release_provenance(tmp_path: Path) -> None:
    database = tmp_path / "pricing.db"
    specs = tmp_path / "specs.json"
    class_prices = tmp_path / "class-prices.json"
    storage_prices = tmp_path / "storage-prices.json"
    specs.write_text(json.dumps([{
        "instance_type": "db.m7i.large", "vcpus": 2, "memory_gib": 8,
        "source": "AWS_RDS_HARDWARE_TABLES", "source_version": "2026-07-01",
    }]))
    class_prices.write_text(json.dumps([{
        "region_code": "us-east-1", "database_engine": "PostgreSQL",
        "license_model": "No license required", "deployment": "Single-AZ",
        "instance_type": "db.m7i.large", "hourly_usd": "0.123456789",
        "source_version": "2026-07-01",
    }]))
    storage_prices.write_text(json.dumps([{
        "region_code": "us-east-1", "database_engine": "PostgreSQL",
        "deployment": "Single-AZ", "storage_type": "gp3",
        "billing_dimension": "storage", "price_per_unit": "0.115000",
        "unit": "GB-Mo", "source_version": "2026-07-01",
    }]))
    assert import_catalog(database, specs, class_prices, storage_prices) == {
        "specs": 1, "class_prices": 1, "storage_prices": 1,
    }
    metadata = build_db_metadata(database, ["us-east-1"])
    require_rds_rightsizer_catalog(metadata)
    with sqlite3.connect(database) as connection:
        hourly, monthly = connection.execute(
            "SELECT hourly_usd, monthly_usd FROM rds_rightsizer_class_prices"
        ).fetchone()
    assert hourly == "0.123456789"
    assert monthly == "90.123455970"
    catalog = RdsClassCatalog(database)
    entry = catalog.get("db.m7i.large")
    assert entry is not None
    assert (entry.vcpus, entry.memory_gib, entry.capability_source) == (2, 8, "rds_instance_specs")
    assert catalog.monthly_price(
        "us-east-1", "db.m7i.large", "postgres",
        multi_az=False, license_model="postgresql-license",
    ) == pytest.approx(90.123455970)


def test_rds_release_rejects_missing_exact_price_tables() -> None:
    with pytest.raises(SystemExit, match="catalog is incomplete"):
        require_rds_rightsizer_catalog({"tables": {"rds_instance_specs": {"row_count": 1}}})


def test_rds_release_coverage_uses_instance_type_membership_not_counts(tmp_path: Path):
    database = tmp_path / "pricing.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE rds_instance_specs (instance_type TEXT PRIMARY KEY, source_version TEXT)"
        )
        connection.executemany(
            "INSERT INTO rds_instance_specs VALUES (?, '2026-07')",
            [("db.m7i.large",), ("db.t3.micro",)],
        )
        connection.execute(
            "CREATE TABLE rds_rightsizer_class_prices (instance_type TEXT)"
        )
        connection.executemany(
            "INSERT INTO rds_rightsizer_class_prices VALUES (?)",
            [("db.m7i.large",), ("db.r7g.large",)],
        )
        connection.execute(
            "CREATE TABLE rds_rightsizer_storage_prices (storage_type TEXT)"
        )
        connection.execute("INSERT INTO rds_rightsizer_storage_prices VALUES ('gp3')")
    metadata = build_db_metadata(database, ["us-east-1"])
    specs = metadata["tables"]["rds_instance_specs"]
    assert specs["missing_priced_instance_types"] == ["db.r7g.large"]
    assert specs["coverage_ratio"] == 0.5
    with pytest.raises(SystemExit, match="coverage is incomplete"):
        require_rds_rightsizer_catalog(metadata)


def test_release_metadata_records_global_source_and_all_card_coverage(tmp_path: Path):
    db_path = tmp_path / "maxops_pricing.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE ec2_instance_specs (
                instance_type TEXT PRIMARY KEY,
                spec_json TEXT NOT NULL,
                source_region TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                generated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO ec2_instance_specs VALUES (?, ?, ?, 2, ?)",
            (
                "m7i.large",
                json.dumps(
                    {
                        "NetworkInfo": {
                            "NetworkCards": [
                                {
                                    "BaselineBandwidthInGbps": 2,
                                    "PeakBandwidthInGbps": 20,
                                },
                                {
                                    "BaselineBandwidthInGbps": 1,
                                    "PeakBandwidthInGbps": 10,
                                },
                            ]
                        }
                    }
                ),
                "us-east-1",
                "2026-07-13T00:00:00Z",
            ),
        )
    metadata = build_db_metadata(db_path, ["us-east-1", "eu-west-1"])
    specs = metadata["tables"]["ec2_instance_specs"]
    assert specs == {
        "row_count": 1,
        "source_region": "us-east-1",
        "source_regions": ["us-east-1"],
        "priced_instance_type_count": 0,
        "missing_priced_instance_type_count": 0,
        "coverage_ratio": 0.0,
        "with_network_baseline": 1,
        "with_network_peak": 1,
    }
