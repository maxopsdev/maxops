"""SQLite-backed street pricing lookup service."""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from app.config import settings

logger = logging.getLogger("uvicorn.error")


def _resolve_pricing_db_path(database_path: str | Path) -> Path:
    path = Path(database_path)
    if path.is_absolute():
        return path
    backend_root = Path(__file__).resolve().parents[2]
    return (backend_root / path).resolve()


def _load_json(text: Optional[str]) -> Dict[str, Any]:
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


class StreetPricingService:
    """Read curated street pricing from the local pricing SQLite database."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        configured_path = database_path or settings.pricing_database_path
        self.database_path = _resolve_pricing_db_path(configured_path)

    def get_price_for_resource(self, resource: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.database_path.exists():
            return None

        resource_type = str(resource.get("resource_type") or "").strip().lower()
        region = str(resource.get("region") or settings.aws_region or "").strip()
        metadata = resource.get("metadata") if isinstance(resource.get("metadata"), dict) else {}
        if not resource_type or not region:
            return None

        try:
            with sqlite3.connect(self.database_path) as connection:
                connection.row_factory = sqlite3.Row
                if resource_type in {"ec2", "ec2_instance"}:
                    return self._lookup_ec2(connection, region, metadata)
                if resource_type in {"rds", "rds_instance"}:
                    return self._lookup_rds(connection, region, metadata)
                if resource_type in {"elasticache", "elasticache_cluster", "elasticache_replication_group"}:
                    return self._lookup_elasticache(connection, region, metadata)
                if resource_type in {"redshift", "redshift_cluster"}:
                    return self._lookup_simple_instance(connection, "street_pricing_redshift", region, metadata)
                if resource_type in {"opensearch", "opensearch_domain", "elasticsearch", "elasticsearch_domain"}:
                    return self._lookup_simple_instance(connection, "street_pricing_opensearch", region, metadata)
        except sqlite3.Error as exc:
            logger.warning("Street pricing lookup failed against %s: %s", self.database_path, exc)
            return None

        return None

    def get_s3_price_map(
        self,
        region: str,
        canonical_keys: Iterable[str],
    ) -> Dict[str, Dict[str, Any]]:
        """Read S3 canonical prices, returning empty data for absent DB/table.

        The S3 table is release-time data and may not exist in older packaged
        databases.  Treating both a missing database and a missing table as an
        empty map lets the caller continue to the bootstrap JSON seed.
        """

        if not self.database_path.exists():
            return {}
        keys = list(canonical_keys)
        if not keys:
            return {}
        placeholders = ",".join("?" for _ in keys)
        try:
            with sqlite3.connect(self.database_path) as connection:
                connection.row_factory = sqlite3.Row
                table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'street_pricing_s3'"
                ).fetchone()
                if table is None:
                    return {}
                rows = connection.execute(
                    f"""
                    SELECT canonical_key, price_usd, unit, tier, sku, as_of
                    FROM street_pricing_s3
                    WHERE region_code = ? AND status = 'resolved'
                      AND price_usd IS NOT NULL
                      AND canonical_key IN ({placeholders})
                    """,
                    [region, *keys],
                ).fetchall()
        except (OSError, sqlite3.Error) as exc:
            logger.warning("S3 street pricing lookup failed against %s: %s", self.database_path, exc)
            return {}
        return {
            row["canonical_key"]: {
                "price": float(row["price_usd"]),
                "unit": row["unit"],
                **({"tier": row["tier"]} if row["tier"] is not None else {}),
                **({"sku": row["sku"]} if row["sku"] is not None else {}),
                **({"as_of": row["as_of"]} if row["as_of"] is not None else {}),
            }
            for row in rows
        }

    def _lookup_ec2(
        self,
        connection: sqlite3.Connection,
        region: str,
        metadata: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        instance_type = _first_text(metadata.get("instance_type"), metadata.get("InstanceType"))
        if not instance_type:
            return None
        for platform in ("linux",):
            row = connection.execute(
                """
                SELECT *
                FROM street_pricing_ec2
                WHERE region_code = ? AND instance_type = ? AND platform_normalized = ?
                LIMIT 1
                """,
                (region, instance_type, platform),
            ).fetchone()
            if row is not None:
                return self._build_response("street_pricing_ec2", row, {"platform": platform})
        return None

    def _lookup_rds(
        self,
        connection: sqlite3.Connection,
        region: str,
        metadata: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        instance_type = _first_text(metadata.get("instance_class"), metadata.get("DBInstanceClass"))
        engine = _first_text(metadata.get("engine"), metadata.get("Engine"))
        if not instance_type or not engine:
            return None
        for candidate in self._rds_engine_candidates(engine):
            row = connection.execute(
                """
                SELECT *
                FROM street_pricing_rds
                WHERE region_code = ? AND instance_type = ? AND engine_normalized = ?
                LIMIT 1
                """,
                (region, instance_type, candidate),
            ).fetchone()
            if row is not None:
                return self._build_response("street_pricing_rds", row, {"engine": candidate})
        return None

    def _lookup_elasticache(
        self,
        connection: sqlite3.Connection,
        region: str,
        metadata: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        instance_type = _first_text(metadata.get("CacheNodeType"), metadata.get("cache_node_type"))
        engine = _first_text(metadata.get("Engine"), metadata.get("engine"))
        if not instance_type or not engine:
            return None
        for candidate in self._elasticache_engine_candidates(engine):
            row = connection.execute(
                """
                SELECT *
                FROM street_pricing_elasticache
                WHERE region_code = ? AND instance_type = ? AND engine_normalized = ?
                LIMIT 1
                """,
                (region, instance_type, candidate),
            ).fetchone()
            if row is not None:
                return self._build_response("street_pricing_elasticache", row, {"engine": candidate})
        return None

    def _lookup_simple_instance(
        self,
        connection: sqlite3.Connection,
        table_name: str,
        region: str,
        metadata: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        instance_type = _first_text(
            metadata.get("instance_type"),
            metadata.get("InstanceType"),
            metadata.get("NodeType"),
            metadata.get("node_type"),
        )
        if not instance_type:
            return None
        row = connection.execute(
            f"""
            SELECT *
            FROM {table_name}
            WHERE region_code = ? AND instance_type = ?
            LIMIT 1
            """,
            (region, instance_type),
        ).fetchone()
        if row is None:
            return None
        return self._build_response(table_name, row, {})

    def _build_response(
        self,
        table_name: str,
        row: sqlite3.Row,
        lookup: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        monthly = row["monthly_usd"]
        if monthly is None:
            return None
        return {
            "price_per_unit": float(monthly),
            "unit": "month",
            "currency": row["currency"] or "USD",
            "source": "street_pricing",
            "parameters": {
                "table": table_name,
                "lookup": lookup,
                "region": row["region_code"],
                "instance_type": row["instance_type"],
                "hourly_usd": row["hourly_usd"],
                "attributes": _load_json(row["attributes_json"]),
                "pricing": _load_json(row["pricing_json"]),
            },
        }

    @staticmethod
    def _rds_engine_candidates(engine: str) -> Iterable[str]:
        normalized = engine.strip().lower()
        aliases = {
            "postgres": ("postgresql", "postgres", "postgresql community"),
            "postgresql": ("postgresql", "postgres", "postgresql community"),
            "mysql": ("mysql",),
            "mariadb": ("mariadb", "mysql"),
            "aurora-postgresql": ("aurora postgresql",),
            "aurora_postgresql": ("aurora postgresql",),
            "aurora-mysql": ("aurora mysql",),
            "aurora_mysql": ("aurora mysql",),
            "sql server": ("sql server",),
            "oracle": ("oracle",),
        }
        return aliases.get(normalized, (normalized,))

    @staticmethod
    def _elasticache_engine_candidates(engine: str) -> Iterable[str]:
        normalized = engine.strip().lower()
        aliases = {
            "redis": ("redis", "redis oss"),
            "redis oss": ("redis oss", "redis"),
            "redisos": ("redis", "redis oss"),
            "valkey": ("valkey",),
            "memcached": ("memcached",),
        }
        return aliases.get(normalized, (normalized,))
