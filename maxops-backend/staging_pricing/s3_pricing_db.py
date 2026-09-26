"""SQLite persistence helpers for the S3 street-pricing release table."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Mapping

try:
    from staging_pricing.import_vantage_pricing import REGION_NAMES
except ModuleNotFoundError:
    from import_vantage_pricing import REGION_NAMES


def write_region(
    database: Path,
    region: str,
    prices: Mapping[str, Mapping[str, Any]],
    *,
    as_of: str,
) -> None:
    """Replace one region's rows without modifying other regions' rows."""

    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS street_pricing_s3 (
                id INTEGER PRIMARY KEY,
                region_code TEXT NOT NULL,
                region_name TEXT NOT NULL,
                canonical_key TEXT NOT NULL,
                price_usd REAL,
                unit TEXT NOT NULL,
                tier TEXT,
                sku TEXT,
                as_of TEXT NOT NULL,
                attributes_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'resolved',
                UNIQUE (region_code, canonical_key)
            )
            """
        )
        connection.execute("DELETE FROM street_pricing_s3 WHERE region_code = ?", (region,))
        connection.executemany(
            """
            INSERT INTO street_pricing_s3
                (region_code, region_name, canonical_key, price_usd, unit,
                 tier, sku, as_of, attributes_json, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    region,
                    REGION_NAMES.get(region, region),
                    key,
                    float(value["price"]) if value.get("price") is not None else None,
                    value["unit"],
                    value.get("tier"),
                    value.get("sku"),
                    as_of,
                    value.get("attributes_json", "{}"),
                    value.get("status", "resolved"),
                )
                for key, value in prices.items()
            ],
        )
        connection.commit()
