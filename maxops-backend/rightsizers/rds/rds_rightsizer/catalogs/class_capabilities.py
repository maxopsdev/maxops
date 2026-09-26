"""RDS class capabilities and exact-enough local price lookup."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.street_pricing import _resolve_pricing_db_path


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _json(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


@dataclass(frozen=True)
class RdsClassEntry:
    db_instance_class: str
    vcpus: float | None
    memory_gib: float | None
    network_baseline_mbps: float | None
    network_peak_mbps: float | None
    ebs_baseline_iops: float | None
    ebs_peak_iops: float | None
    ebs_baseline_mbps: float | None
    ebs_peak_mbps: float | None
    architecture: str | None
    burstable: bool
    cpu_baseline_ratio: float | None
    local_nvme: bool
    current_generation: bool | None
    capability_source: str


class RdsClassCatalog:
    """Load RDS-specific specs, with Price List attributes as enrichment only."""

    VERSION = "rds-class-v1"

    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = _resolve_pricing_db_path(database_path or settings.pricing_database_path)

    def list(self) -> dict[str, RdsClassEntry]:
        if not self.database_path.exists():
            return {}
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            explicit: dict[str, dict[str, Any]] = {}
            if "rds_instance_specs" in tables:
                for row in connection.execute("SELECT instance_type, spec_json FROM rds_instance_specs"):
                    explicit[row["instance_type"]] = _json(row["spec_json"])
            attrs: dict[str, dict[str, Any]] = {}
            if "street_pricing_rds" in tables:
                for row in connection.execute(
                    "SELECT instance_type, attributes_json FROM street_pricing_rds GROUP BY instance_type"
                ):
                    attrs[row["instance_type"]] = _json(row["attributes_json"])
        return {
            instance_type: self._entry(instance_type, explicit.get(instance_type, {}), attrs.get(instance_type, {}))
            for instance_type in sorted(set(explicit) | set(attrs))
        }

    def get(self, instance_type: str) -> RdsClassEntry | None:
        return self.list().get(instance_type)

    def monthly_price(
        self,
        region: str,
        instance_type: str,
        engine: str,
        *,
        multi_az: bool,
        license_model: str,
    ) -> float | None:
        if not self.database_path.exists():
            return None
        deployment = "Multi-AZ" if multi_az else "Single-AZ"
        engine_key = engine.lower()
        legacy_engine_name = {
            "postgres": "PostgreSQL",
            "postgresql": "PostgreSQL",
            "mysql": "MySQL",
            "mariadb": "MariaDB",
            "oracle-ee": "Oracle",
            "oracle-se2": "Oracle",
            "oracle-se1": "Oracle",
            "oracle-se": "Oracle",
            "sqlserver-ee": "SQL Server",
            "sqlserver-se": "SQL Server",
            "sqlserver-ex": "SQL Server",
            "sqlserver-web": "SQL Server",
            "db2-ae": "Db2",
            "db2-se": "Db2",
        }.get(engine_key)
        license_name = {
            "general-public-license": "No license required",
            "postgresql-license": "No license required",
            "license-included": "License included",
            "bring-your-own-license": "Bring your own license",
            "": "No license required",
        }.get(license_model.lower())
        with sqlite3.connect(self.database_path) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "rds_rightsizer_class_prices" in tables and license_name:
                # New release catalogs use the exact RDS Engine value. Retain a
                # compatibility lookup for the first generic-engine artifact
                # schema, but never use that fallback for editioned engines.
                row = connection.execute(
                    "SELECT monthly_usd FROM rds_rightsizer_class_prices "
                    "WHERE region_code=? AND database_engine=? AND license_model=? "
                    "AND deployment=? AND instance_type=? LIMIT 1",
                    (region, engine_key, license_name, deployment, instance_type),
                ).fetchone()
                if row:
                    return float(row[0])
                if engine_key in {"postgres", "postgresql", "mysql", "mariadb"} and legacy_engine_name:
                    row = connection.execute(
                        "SELECT monthly_usd FROM rds_rightsizer_class_prices "
                        "WHERE region_code=? AND database_engine=? AND license_model=? "
                        "AND deployment=? AND instance_type=? LIMIT 1",
                        (region, legacy_engine_name, license_name, deployment, instance_type),
                    ).fetchone()
                    if row:
                        return float(row[0])
            has_legacy_rds = "street_pricing_rds" in tables
        if multi_az:
            return None
        if not has_legacy_rds:
            return None
        normalized = engine.lower()
        aliases = {
            "postgres": ("postgresql",),
            "postgresql": ("postgresql",),
            "mysql": ("mysql",),
            "mariadb": ("mariadb",),
        }.get(normalized)
        if not aliases or license_model.lower() not in {
            "general-public-license",
            "postgresql-license",
            "",
        }:
            return None
        with sqlite3.connect(self.database_path) as connection:
            for alias in aliases:
                row = connection.execute(
                    "SELECT monthly_usd FROM street_pricing_rds WHERE region_code=? AND instance_type=? AND engine_normalized=? LIMIT 1",
                    (region, instance_type, alias),
                ).fetchone()
                if row:
                    return float(row[0])
        return None

    @staticmethod
    def _entry(instance_type: str, spec: dict[str, Any], attrs: dict[str, Any]) -> RdsClassEntry:
        source = spec or attrs
        processor = str(source.get("processor") or source.get("physicalProcessor") or "").lower()
        architecture = str(source.get("architecture") or "") or (
            "arm64" if "graviton" in processor else "x86_64" if processor else None
        )
        network = str(source.get("network_bandwidth") or source.get("networkPerformance") or "")
        match = re.search(r"([0-9]+(?:\.[0-9]+)?)", network.replace(",", ""))
        network_peak = float(match.group(1)) * 1000 if match else None
        ebs_mbps = _number(source.get("ebs_peak_mbps") or source.get("ebs_max_bandwidth"))
        storage = str(source.get("instance_storage") or source.get("storage") or "").lower()
        credits = _number(source.get("cpu_credits_per_hour"))
        vcpus = _number(source.get("vcpus") or source.get("vcpu"))
        baseline = credits / (vcpus * 60) if credits is not None and vcpus else None
        return RdsClassEntry(
            db_instance_class=instance_type,
            vcpus=vcpus,
            memory_gib=_number(source.get("memory_gib") or source.get("memory")),
            network_baseline_mbps=_number(source.get("network_baseline_mbps")),
            network_peak_mbps=_number(source.get("network_peak_mbps")) or network_peak,
            ebs_baseline_iops=_number(source.get("ebs_baseline_iops")),
            ebs_peak_iops=_number(source.get("ebs_peak_iops") or source.get("ebs_iops")),
            ebs_baseline_mbps=_number(source.get("ebs_baseline_mbps") or source.get("ebs_baseline_throughput")),
            ebs_peak_mbps=_number(source.get("ebs_peak_mbps") or source.get("ebs_throughput")) or (ebs_mbps / 8 if ebs_mbps else None),
            architecture=architecture,
            burstable=bool(source["burstable"]) if source.get("burstable") is not None else instance_type.startswith("db.t"),
            cpu_baseline_ratio=_number(source.get("cpu_baseline_ratio")) or baseline,
            local_nvme=bool(source["local_nvme"]) if source.get("local_nvme") is not None else "nvme" in storage or (" x " in storage and "ebs" not in storage),
            current_generation=(
                bool(source["current_generation"])
                if source.get("current_generation") is not None
                else str(source.get("currentGeneration") or "").lower() == "yes"
                if source.get("currentGeneration") is not None
                else None
            ),
            capability_source="rds_instance_specs" if spec else "rds_price_list_attributes",
        )
