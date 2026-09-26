"""Import reviewed RDS hardware and exact Price List rows into the release DB.

The inputs are deliberately normalized release artifacts rather than runtime
scrapes. This keeps provenance reviewable and prevents the rightsizer from
guessing capacity or pricing dimensions.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


def _rows(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise SystemExit(f"Expected a JSON array of objects: {path}")
    return payload


def _decimal(value: Any, label: str) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SystemExit(f"Invalid decimal {label}: {value!r}") from exc
    if not number.is_finite() or number < 0:
        raise SystemExit(f"Invalid decimal {label}: {value!r}")
    return str(number)


def import_catalog(
    database: Path,
    specs_path: Path,
    class_prices_path: Path,
    storage_prices_path: Path,
) -> dict[str, int]:
    specs = _rows(specs_path)
    class_prices = _rows(class_prices_path)
    storage_prices = _rows(storage_prices_path)
    generated_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS rds_instance_specs (
                instance_type TEXT PRIMARY KEY,
                spec_json TEXT NOT NULL,
                source TEXT NOT NULL,
                source_version TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                generated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rds_rightsizer_class_prices (
                region_code TEXT NOT NULL,
                database_engine TEXT NOT NULL,
                license_model TEXT NOT NULL,
                deployment TEXT NOT NULL,
                instance_type TEXT NOT NULL,
                hourly_usd TEXT NOT NULL,
                monthly_usd TEXT NOT NULL,
                currency TEXT NOT NULL,
                source_version TEXT NOT NULL,
                PRIMARY KEY (region_code, database_engine, license_model, deployment, instance_type)
            );
            CREATE TABLE IF NOT EXISTS rds_rightsizer_storage_prices (
                region_code TEXT NOT NULL,
                database_engine TEXT NOT NULL,
                deployment TEXT NOT NULL,
                storage_type TEXT NOT NULL,
                billing_dimension TEXT NOT NULL,
                price_per_unit TEXT NOT NULL,
                unit TEXT NOT NULL,
                included_quantity TEXT NOT NULL DEFAULT '0',
                source_version TEXT NOT NULL,
                PRIMARY KEY (region_code, database_engine, deployment, storage_type, billing_dimension)
            );
            """
        )
        connection.execute("DELETE FROM rds_instance_specs")
        connection.execute("DELETE FROM rds_rightsizer_class_prices")
        connection.execute("DELETE FROM rds_rightsizer_storage_prices")
        for row in specs:
            instance_type = str(row.get("instance_type") or "")
            if not instance_type.startswith("db.") or row.get("vcpus") is None or row.get("memory_gib") is None:
                raise SystemExit(f"Incomplete RDS class specification: {instance_type or row!r}")
            connection.execute(
                "INSERT INTO rds_instance_specs VALUES (?, ?, ?, ?, ?, ?)",
                (
                    instance_type,
                    json.dumps(row, sort_keys=True, separators=(",", ":")),
                    str(row.get("source") or "AWS_RDS_HARDWARE_TABLES"),
                    str(row.get("source_version") or "unknown"),
                    SCHEMA_VERSION,
                    generated_at,
                ),
            )
        for row in class_prices:
            required = ("region_code", "database_engine", "license_model", "deployment", "instance_type", "hourly_usd", "source_version")
            if any(row.get(field) in (None, "") for field in required):
                raise SystemExit(f"Incomplete RDS class-price row: {row!r}")
            if str(row["database_engine"]).lower() in {"oracle", "sql server", "db2"}:
                raise SystemExit(
                    "Editioned RDS class-price rows must use the exact RDS Engine value "
                    f"(for example oracle-ee or sqlserver-se): {row!r}"
                )
            hourly = _decimal(row["hourly_usd"], "hourly_usd")
            monthly = _decimal(row.get("monthly_usd", Decimal(hourly) * Decimal("730")), "monthly_usd")
            connection.execute(
                "INSERT INTO rds_rightsizer_class_prices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["region_code"], row["database_engine"], row["license_model"],
                    row["deployment"], row["instance_type"], hourly, monthly,
                    str(row.get("currency") or "USD"), row["source_version"],
                ),
            )
        for row in storage_prices:
            required = ("region_code", "database_engine", "deployment", "storage_type", "billing_dimension", "price_per_unit", "unit", "source_version")
            if any(row.get(field) in (None, "") for field in required):
                raise SystemExit(f"Incomplete RDS storage-price row: {row!r}")
            connection.execute(
                "INSERT INTO rds_rightsizer_storage_prices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["region_code"], row["database_engine"], row["deployment"],
                    row["storage_type"], row["billing_dimension"],
                    _decimal(row["price_per_unit"], "price_per_unit"), row["unit"],
                    _decimal(row.get("included_quantity", 0), "included_quantity"), row["source_version"],
                ),
            )
        connection.commit()
    return {"specs": len(specs), "class_prices": len(class_prices), "storage_prices": len(storage_prices)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import the reviewed RDS rightsizer release catalog")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--specs", required=True, type=Path)
    parser.add_argument("--class-prices", required=True, type=Path)
    parser.add_argument("--storage-prices", required=True, type=Path)
    args = parser.parse_args()
    counts = import_catalog(args.database, args.specs, args.class_prices, args.storage_prices)
    print(f"Imported RDS rightsizer catalog: {counts}")


if __name__ == "__main__":
    main()
