"""Import curated AWS street pricing from Vantage website JSON assets."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
from urllib.request import Request, urlopen


TARGET_REGIONS = [
    "us-east-1",
    "us-east-2",
    "us-west-1",
    "us-west-2",
    "ca-central-1",
    "eu-west-1",
    "eu-west-2",
    "eu-west-3",
    "eu-central-1",
    "eu-north-1",
    "ap-south-1",
    "ap-southeast-1",
    "ap-southeast-2",
    "ap-northeast-1",
    "sa-east-1",
]

REGION_NAMES = {
    "us-east-1": "US East (N. Virginia)",
    "us-east-2": "US East (Ohio)",
    "us-west-1": "US West (N. California)",
    "us-west-2": "US West (Oregon)",
    "ca-central-1": "Canada (Central)",
    "eu-west-1": "EU (Ireland)",
    "eu-west-2": "EU (London)",
    "eu-west-3": "EU (Paris)",
    "eu-central-1": "EU (Frankfurt)",
    "eu-north-1": "EU (Stockholm)",
    "ap-south-1": "Asia Pacific (Mumbai)",
    "ap-southeast-1": "Asia Pacific (Singapore)",
    "ap-southeast-2": "Asia Pacific (Sydney)",
    "ap-northeast-1": "Asia Pacific (Tokyo)",
    "sa-east-1": "South America (Sao Paulo)",
}

DATASET_URLS = {
    "ec2": "https://instances.vantage.sh/instances.json",
    "rds": "https://instances.vantage.sh/rds/instances.json",
    "elasticache": "https://instances.vantage.sh/cache/instances.json",
    "redshift": "https://instances.vantage.sh/redshift/instances.json",
    "opensearch": "https://instances.vantage.sh/opensearch/instances.json",
}


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_database_path() -> Path:
    return _backend_root() / "maxops_pricing.db"


def _fetch_json(url: str) -> List[Dict[str, Any]]:
    request = Request(
        url,
        headers={
            "User-Agent": "maxops-staging-pricing-importer/1.0",
            "Accept": "application/json",
        },
    )
    with urlopen(request) as response:
        return json.load(response)


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def _first_text(*values: Any) -> str:
    for value in values:
        text = _as_text(value).strip()
        if text:
            return text
    return ""


def _normalize_text(value: Any) -> str:
    return _as_text(value).strip().lower()


def _json_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _extract_ondemand_price(price_info: Any) -> float | None:
    if isinstance(price_info, (int, float)):
        return float(price_info)
    if isinstance(price_info, dict):
        value = price_info.get("ondemand")
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return None
    return None


def _instance_type(instance: Dict[str, Any]) -> str:
    return _first_text(
        instance.get("instance_type"),
        instance.get("instanceType"),
        instance.get("node_type"),
        instance.get("NodeType"),
    )


def _metadata_without_pricing(instance: Dict[str, Any]) -> Dict[str, Any]:
    metadata = dict(instance)
    metadata.pop("pricing", None)
    metadata.pop("regions", None)
    return metadata


def _prepare_rows_ec2(instances: List[Dict[str, Any]]) -> List[Tuple[Any, ...]]:
    rows: List[Tuple[Any, ...]] = []
    for instance in instances:
        instance_type = _instance_type(instance)
        if not instance_type:
            continue
        metadata = _metadata_without_pricing(instance)
        for region_code, platforms in (instance.get("pricing") or {}).items():
            if region_code not in TARGET_REGIONS or not isinstance(platforms, dict):
                continue
            for platform, price_info in platforms.items():
                hourly = _extract_ondemand_price(price_info)
                if hourly is None:
                    continue
                rows.append(
                    (
                        region_code,
                        REGION_NAMES.get(region_code, region_code),
                        instance_type,
                        _as_text(platform),
                        _normalize_text(platform),
                        float(hourly),
                        float(hourly) * 730.0,
                        "USD",
                        _first_text(instance.get("pretty_name")),
                        _first_text(instance.get("family"), instance.get("instanceFamily")),
                        _json_text(metadata),
                        _json_text(price_info),
                    )
                )
    return rows


def _prepare_rows_rds(instances: List[Dict[str, Any]]) -> List[Tuple[Any, ...]]:
    rows: List[Tuple[Any, ...]] = []
    for instance in instances:
        instance_type = _instance_type(instance)
        if not instance_type:
            continue
        metadata = _metadata_without_pricing(instance)
        for region_code, engines in (instance.get("pricing") or {}).items():
            if region_code not in TARGET_REGIONS or not isinstance(engines, dict):
                continue
            for engine, price_info in engines.items():
                hourly = _extract_ondemand_price(price_info)
                if hourly is None:
                    continue
                rows.append(
                    (
                        region_code,
                        REGION_NAMES.get(region_code, region_code),
                        instance_type,
                        _as_text(engine),
                        _normalize_text(engine),
                        float(hourly),
                        float(hourly) * 730.0,
                        "USD",
                        _first_text(instance.get("pretty_name")),
                        _first_text(instance.get("family"), instance.get("instanceFamily")),
                        _json_text(metadata),
                        _json_text(price_info),
                    )
                )
    return rows


def _prepare_rows_elasticache(instances: List[Dict[str, Any]]) -> List[Tuple[Any, ...]]:
    rows: List[Tuple[Any, ...]] = []
    for instance in instances:
        instance_type = _instance_type(instance)
        if not instance_type:
            continue
        metadata = _metadata_without_pricing(instance)
        for region_code, engines in (instance.get("pricing") or {}).items():
            if region_code not in TARGET_REGIONS or not isinstance(engines, dict):
                continue
            for engine, price_info in engines.items():
                hourly = _extract_ondemand_price(price_info)
                if hourly is None:
                    continue
                rows.append(
                    (
                        region_code,
                        REGION_NAMES.get(region_code, region_code),
                        instance_type,
                        _as_text(engine),
                        _normalize_text(engine),
                        float(hourly),
                        float(hourly) * 730.0,
                        "USD",
                        _first_text(instance.get("pretty_name")),
                        _first_text(instance.get("family"), instance.get("instanceFamily")),
                        _json_text(metadata),
                        _json_text(price_info),
                    )
                )
    return rows


def _prepare_rows_simple(instances: List[Dict[str, Any]]) -> List[Tuple[Any, ...]]:
    rows: List[Tuple[Any, ...]] = []
    for instance in instances:
        instance_type = _instance_type(instance)
        if not instance_type:
            continue
        metadata = _metadata_without_pricing(instance)
        for region_code, price_info in (instance.get("pricing") or {}).items():
            if region_code not in TARGET_REGIONS:
                continue
            hourly = _extract_ondemand_price(price_info)
            if hourly is None:
                continue
            rows.append(
                (
                    region_code,
                    REGION_NAMES.get(region_code, region_code),
                    instance_type,
                    float(hourly),
                    float(hourly) * 730.0,
                    "USD",
                    _first_text(instance.get("pretty_name")),
                    _first_text(instance.get("family"), instance.get("instanceFamily"), instance.get("usageFamily")),
                    _json_text(metadata),
                    _json_text(price_info),
                )
            )
    return rows


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS street_pricing_ec2 (
            id INTEGER PRIMARY KEY,
            region_code TEXT NOT NULL,
            region_name TEXT NOT NULL,
            instance_type TEXT NOT NULL,
            platform TEXT NOT NULL,
            platform_normalized TEXT NOT NULL,
            hourly_usd REAL NOT NULL,
            monthly_usd REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'USD',
            pretty_name TEXT,
            family TEXT,
            attributes_json TEXT NOT NULL,
            pricing_json TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_street_pricing_ec2
            ON street_pricing_ec2 (region_code, instance_type, platform_normalized);

        CREATE TABLE IF NOT EXISTS street_pricing_rds (
            id INTEGER PRIMARY KEY,
            region_code TEXT NOT NULL,
            region_name TEXT NOT NULL,
            instance_type TEXT NOT NULL,
            database_engine TEXT NOT NULL,
            engine_normalized TEXT NOT NULL,
            hourly_usd REAL NOT NULL,
            monthly_usd REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'USD',
            pretty_name TEXT,
            family TEXT,
            attributes_json TEXT NOT NULL,
            pricing_json TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_street_pricing_rds
            ON street_pricing_rds (region_code, instance_type, engine_normalized);

        CREATE TABLE IF NOT EXISTS street_pricing_elasticache (
            id INTEGER PRIMARY KEY,
            region_code TEXT NOT NULL,
            region_name TEXT NOT NULL,
            instance_type TEXT NOT NULL,
            cache_engine TEXT NOT NULL,
            engine_normalized TEXT NOT NULL,
            hourly_usd REAL NOT NULL,
            monthly_usd REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'USD',
            pretty_name TEXT,
            family TEXT,
            attributes_json TEXT NOT NULL,
            pricing_json TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_street_pricing_elasticache
            ON street_pricing_elasticache (region_code, instance_type, engine_normalized);

        CREATE TABLE IF NOT EXISTS street_pricing_redshift (
            id INTEGER PRIMARY KEY,
            region_code TEXT NOT NULL,
            region_name TEXT NOT NULL,
            instance_type TEXT NOT NULL,
            hourly_usd REAL NOT NULL,
            monthly_usd REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'USD',
            pretty_name TEXT,
            family TEXT,
            attributes_json TEXT NOT NULL,
            pricing_json TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_street_pricing_redshift
            ON street_pricing_redshift (region_code, instance_type);

        CREATE TABLE IF NOT EXISTS street_pricing_opensearch (
            id INTEGER PRIMARY KEY,
            region_code TEXT NOT NULL,
            region_name TEXT NOT NULL,
            instance_type TEXT NOT NULL,
            hourly_usd REAL NOT NULL,
            monthly_usd REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'USD',
            pretty_name TEXT,
            family TEXT,
            attributes_json TEXT NOT NULL,
            pricing_json TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_street_pricing_opensearch
            ON street_pricing_opensearch (region_code, instance_type);
        """
    )


def _replace_table(
    connection: sqlite3.Connection,
    table_name: str,
    columns: str,
    rows: List[Tuple[Any, ...]],
) -> None:
    connection.execute(f"DELETE FROM {table_name}")
    placeholders = ",".join("?" for _ in columns.split(","))
    connection.executemany(
        f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})",
        rows,
    )


def import_pricing(database_path: Path) -> Dict[str, int]:
    datasets = {name: _fetch_json(url) for name, url in DATASET_URLS.items()}
    row_sets = {
        "street_pricing_ec2": _prepare_rows_ec2(datasets["ec2"]),
        "street_pricing_rds": _prepare_rows_rds(datasets["rds"]),
        "street_pricing_elasticache": _prepare_rows_elasticache(datasets["elasticache"]),
        "street_pricing_redshift": _prepare_rows_simple(datasets["redshift"]),
        "street_pricing_opensearch": _prepare_rows_simple(datasets["opensearch"]),
    }

    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as connection:
        _create_schema(connection)
        _replace_table(
            connection,
            "street_pricing_ec2",
            "region_code,region_name,instance_type,platform,platform_normalized,hourly_usd,monthly_usd,currency,pretty_name,family,attributes_json,pricing_json",
            row_sets["street_pricing_ec2"],
        )
        _replace_table(
            connection,
            "street_pricing_rds",
            "region_code,region_name,instance_type,database_engine,engine_normalized,hourly_usd,monthly_usd,currency,pretty_name,family,attributes_json,pricing_json",
            row_sets["street_pricing_rds"],
        )
        _replace_table(
            connection,
            "street_pricing_elasticache",
            "region_code,region_name,instance_type,cache_engine,engine_normalized,hourly_usd,monthly_usd,currency,pretty_name,family,attributes_json,pricing_json",
            row_sets["street_pricing_elasticache"],
        )
        _replace_table(
            connection,
            "street_pricing_redshift",
            "region_code,region_name,instance_type,hourly_usd,monthly_usd,currency,pretty_name,family,attributes_json,pricing_json",
            row_sets["street_pricing_redshift"],
        )
        _replace_table(
            connection,
            "street_pricing_opensearch",
            "region_code,region_name,instance_type,hourly_usd,monthly_usd,currency,pretty_name,family,attributes_json,pricing_json",
            row_sets["street_pricing_opensearch"],
        )
        connection.commit()

    return {table: len(rows) for table, rows in row_sets.items()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import curated Vantage pricing into maxops_pricing.db")
    parser.add_argument(
        "--db-path",
        default=str(_default_database_path()),
        help="Path to the pricing SQLite database.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    counts = import_pricing(Path(args.db_path).resolve())
    print("Imported pricing rows:")
    for table_name, count in counts.items():
        print(f"  {table_name}: {count}")


if __name__ == "__main__":
    main()
