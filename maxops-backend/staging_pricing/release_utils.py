"""Shared helpers for pricing release packaging and bootstrap."""
from __future__ import annotations

import hashlib
import json
import lzma
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Dict, Iterable
from urllib.request import Request, urlopen


USER_AGENT = "maxops-pricing-release/1.0"
DEFAULT_ASSET_NAME = "maxops_pricing.db.xz"
DEFAULT_SHA256_NAME = f"{DEFAULT_ASSET_NAME}.sha256"
DEFAULT_METADATA_NAME = "maxops_pricing.metadata.json"


def _json_object(value: str | None) -> Dict[str, object]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_to_path(url: str, destination: Path) -> None:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def compress_xz(source_path: Path, destination_path: Path, preset: int = 6) -> None:
    with source_path.open("rb") as source, lzma.open(
        destination_path, "wb", preset=preset
    ) as target:
        shutil.copyfileobj(source, target)


def decompress_xz(source_path: Path, destination_path: Path) -> None:
    with lzma.open(source_path, "rb") as source, destination_path.open("wb") as target:
        shutil.copyfileobj(source, target)


def atomic_replace(source_path: Path, destination_path: Path) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source_path, destination_path)


def parse_sha256_text(text: str) -> str:
    line = text.strip().splitlines()[0].strip()
    return line.split()[0]


def read_sha256_file(path: Path) -> str:
    return parse_sha256_text(path.read_text())


def write_sha256_file(target_path: Path, digest: str, file_name: str) -> None:
    target_path.write_text(f"{digest}  {file_name}\n")


def build_release_url(repo: str, asset_name: str, release: str = "latest") -> str:
    if release == "latest":
        return f"https://github.com/{repo}/releases/latest/download/{asset_name}"
    return f"https://github.com/{repo}/releases/download/{release}/{asset_name}"


def build_db_metadata(database_path: Path, regions: Iterable[str]) -> Dict[str, object]:
    tables = [
        "street_pricing_ec2",
        "street_pricing_rds",
        "street_pricing_elasticache",
        "street_pricing_redshift",
        "street_pricing_opensearch",
        "street_pricing_s3",
        "ec2_instance_specs",
        "rds_instance_specs",
        "rds_rightsizer_class_prices",
        "rds_rightsizer_storage_prices",
    ]
    region_list = list(regions)
    metadata: Dict[str, object] = {
        "database_name": database_path.name,
        "database_size_bytes": database_path.stat().st_size,
        "regions": region_list,
        "tables": {},
    }
    with sqlite3.connect(database_path) as connection:
        existing_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        for table_name in tables:
            count = (
                connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
                if table_name in existing_tables
                else 0
            )
            metadata["tables"][table_name] = {"row_count": count}
        if "ec2_instance_specs" in existing_tables:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(ec2_instance_specs)")
            }
            source_column = (
                "source_region"
                if "source_region" in columns
                else "region_code"
                if "region_code" in columns
                else None
            )
            source_regions = (
                [
                    row[0]
                    for row in connection.execute(
                        f"SELECT DISTINCT {source_column} FROM ec2_instance_specs "
                        f"WHERE {source_column} IS NOT NULL ORDER BY {source_column}"
                    )
                ]
                if source_column
                else []
            )
            with_baseline = 0
            with_peak = 0
            for (spec_json,) in connection.execute(
                "SELECT spec_json FROM ec2_instance_specs"
            ):
                spec = _json_object(spec_json)
                network_info = spec.get("NetworkInfo")
                cards = (
                    network_info.get("NetworkCards")
                    if isinstance(network_info, dict)
                    and isinstance(network_info.get("NetworkCards"), list)
                    else []
                )
                if cards and all(
                    isinstance(card, dict)
                    and card.get("BaselineBandwidthInGbps") is not None
                    for card in cards
                ):
                    with_baseline += 1
                if cards and all(
                    isinstance(card, dict)
                    and card.get("PeakBandwidthInGbps") is not None
                    for card in cards
                ):
                    with_peak += 1
            spec_metadata = metadata["tables"]["ec2_instance_specs"]
            priced_type_count = (
                connection.execute(
                    "SELECT COUNT(DISTINCT instance_type) FROM street_pricing_ec2"
                ).fetchone()[0]
                if "street_pricing_ec2" in existing_tables
                else 0
            )
            spec_count = spec_metadata["row_count"]
            spec_metadata.update(
                {
                    "source_region": source_regions[0]
                    if len(source_regions) == 1
                    else None,
                    "source_regions": source_regions,
                    "priced_instance_type_count": priced_type_count,
                    "missing_priced_instance_type_count": max(
                        priced_type_count - spec_count, 0
                    ),
                    "coverage_ratio": (
                        spec_count / priced_type_count if priced_type_count else 0.0
                    ),
                    "with_network_baseline": with_baseline,
                    "with_network_peak": with_peak,
                }
            )
        if "rds_instance_specs" in existing_tables:
            specs = metadata["tables"]["rds_instance_specs"]
            specs["source_versions"] = [
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT source_version FROM rds_instance_specs ORDER BY source_version"
                )
            ]
            priced_type_count = (
                connection.execute(
                    "SELECT COUNT(DISTINCT instance_type) FROM rds_rightsizer_class_prices"
                ).fetchone()[0]
                if "rds_rightsizer_class_prices" in existing_tables
                else 0
            )
            spec_count = specs["row_count"]
            missing_priced_types = (
                [
                    row[0]
                    for row in connection.execute(
                        "SELECT DISTINCT prices.instance_type "
                        "FROM rds_rightsizer_class_prices AS prices "
                        "LEFT JOIN rds_instance_specs AS specs "
                        "ON specs.instance_type = prices.instance_type "
                        "WHERE specs.instance_type IS NULL "
                        "ORDER BY prices.instance_type"
                    )
                ]
                if "rds_rightsizer_class_prices" in existing_tables
                else []
            )
            covered_priced_type_count = max(
                priced_type_count - len(missing_priced_types), 0
            )
            specs["priced_instance_type_count"] = priced_type_count
            specs["covered_priced_instance_type_count"] = covered_priced_type_count
            specs["missing_priced_instance_type_count"] = len(missing_priced_types)
            specs["missing_priced_instance_types"] = missing_priced_types
            specs["coverage_ratio"] = (
                covered_priced_type_count / priced_type_count
                if priced_type_count
                else 0.0
            )
    return metadata


def write_metadata(path: Path, metadata: Dict[str, object]) -> None:
    path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")


def temporary_file_path(parent: Path, suffix: str) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(delete=False, dir=parent, suffix=suffix)
    handle.close()
    return Path(handle.name)
