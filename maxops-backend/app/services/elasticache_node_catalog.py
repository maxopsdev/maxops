"""ElastiCache node catalog backed by the packaged street-pricing database."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.ec2_instance_catalog import EC2InstanceCatalog
from app.services.street_pricing import _resolve_pricing_db_path


MIN_ENGINE_VERSION_BY_NODE: dict[str, tuple[int, int, int]] = {
    "m7g": (6, 2, 0),
    "r7g": (6, 2, 0),
    "c7gn": (6, 2, 0),
    "m6g": (5, 0, 6),
    "r6g": (5, 0, 6),
    "r6gd": (6, 2, 0),
    "t4g.micro": (3, 2, 4),
    "t4g.small": (5, 0, 6),
    "t4g.medium": (5, 0, 6),
    "m5": (3, 2, 4),
    "m4": (3, 2, 4),
    "r5": (3, 2, 4),
    "r4": (3, 2, 4),
    "t3": (3, 2, 4),
    "t2": (3, 2, 4),
}

MAX_ACCRUED_CPU_CREDITS: dict[str, int] = {
    "t4g.micro": 288,
    "t4g.small": 576,
    "t4g.medium": 576,
    "t3.micro": 288,
    "t3.small": 576,
    "t3.medium": 576,
    "t2.micro": 144,
    "t2.small": 288,
    "t2.medium": 576,
}


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _family(node_type: str) -> str:
    parts = str(node_type).split(".")
    return parts[1].lower() if len(parts) >= 3 else ""


def _size(node_type: str) -> str:
    parts = str(node_type).split(".")
    return ".".join(parts[2:]).lower() if len(parts) >= 3 else ""


def _family_class(family: str) -> str:
    match = re.match(r"[a-z]+", family)
    return (match.group(0) if match else family).rstrip("g")[:1]


def _semantic_version(value: str) -> tuple[int, int, int] | None:
    match = re.match(r"^\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(value or ""))
    if not match:
        return None
    return tuple(int(item or 0) for item in match.groups())  # type: ignore[return-value]


def engine_supports_node_type(
    engine: str, engine_version: str, node_type: str
) -> bool | None:
    family = _family(node_type)
    key = f"{family}.{_size(node_type)}"
    floor = MIN_ENGINE_VERSION_BY_NODE.get(key) or MIN_ENGINE_VERSION_BY_NODE.get(
        family
    )
    version = _semantic_version(engine_version)
    if floor is None or version is None:
        return None
    if str(engine).strip().lower() == "valkey":
        return True
    return version >= floor


@dataclass(frozen=True)
class ElastiCacheCatalogEntry:
    node_type: str
    engine: str
    region: str
    hourly_usd: float
    monthly_usd: float
    vcpus: int | None
    memory_gib: float | None
    maxmemory_bytes: int | None
    maxmemory_source: str
    max_clients: int | None
    family: str
    family_class: str
    architecture: str
    data_tiering: bool
    burstable: bool
    network_baseline_mbps: float | None
    network_reliable_max_mbps: float | None
    network_capacity_kind: str
    network_performance_label: str | None
    capability_source: str = "street_pricing_attributes"


class ElastiCacheNodeCatalog:
    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = _resolve_pricing_db_path(
            database_path or settings.pricing_database_path
        )

    def list_region(
        self, region: str, engine: str
    ) -> dict[str, ElastiCacheCatalogEntry]:
        if not self.database_path.exists():
            return {}
        normalized = str(engine or "").lower()
        candidates = ("redis", "redis oss") if normalized == "redis" else (normalized,)
        result: dict[str, ElastiCacheCatalogEntry] = {}
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            specs = EC2InstanceCatalog._load_specs(connection)
            placeholders = ",".join("?" for _ in candidates)
            rows = connection.execute(
                f"SELECT * FROM street_pricing_elasticache "
                f"WHERE region_code = ? AND engine_normalized IN ({placeholders})",
                (region, *candidates),
            )
            for row in rows:
                entry = self._entry(row, specs.get(str(row["instance_type"])[6:], {}))
                result[entry.node_type] = entry
        return result

    def get(
        self, region: str, engine: str, node_type: str
    ) -> ElastiCacheCatalogEntry | None:
        return self.list_region(region, engine).get(node_type)

    @staticmethod
    def _entry(row: sqlite3.Row, spec: dict[str, Any]) -> ElastiCacheCatalogEntry:
        attrs = _json(row["attributes_json"])
        family = _family(row["instance_type"])
        memory_gib = _number(attrs.get("memory"))
        maxmemory = [
            _number(value)
            for key, value in attrs.items()
            if re.match(r"redis.*-maxmemory$", str(key), re.IGNORECASE)
        ]
        known_maxmemory = [value for value in maxmemory if value is not None]
        network_info = (
            spec.get("NetworkInfo") if isinstance(spec.get("NetworkInfo"), dict) else {}
        )
        cards = (
            network_info.get("NetworkCards")
            if isinstance(network_info.get("NetworkCards"), list)
            else []
        )

        def bandwidth(key: str) -> float | None:
            values = [
                _number(card.get(key)) for card in cards if isinstance(card, dict)
            ]
            return (
                sum(value for value in values if value is not None) * 1000.0
                if values and all(value is not None for value in values)
                else None
            )

        baseline = _number(spec.get("baseline_network_mbps_total")) or bandwidth(
            "BaselineBandwidthInGbps"
        )
        peak = _number(spec.get("peak_network_mbps_total")) or bandwidth(
            "PeakBandwidthInGbps"
        )
        label = attrs.get("networkPerformance") or network_info.get(
            "NetworkPerformance"
        )
        fallback = int(memory_gib * 1024**3) if memory_gib is not None else None
        return ElastiCacheCatalogEntry(
            node_type=str(row["instance_type"]),
            engine=str(row["engine_normalized"]),
            region=str(row["region_code"]),
            hourly_usd=float(row["hourly_usd"]),
            monthly_usd=float(row["monthly_usd"]),
            vcpus=int(value)
            if (value := _number(attrs.get("vcpu") or attrs.get("vCPU"))) is not None
            else None,
            memory_gib=memory_gib,
            maxmemory_bytes=int(max(known_maxmemory)) if known_maxmemory else fallback,
            maxmemory_source="pricing_attribute"
            if known_maxmemory
            else "memory_attribute",
            max_clients=int(value)
            if (value := _number(attrs.get("max_clients"))) is not None
            else None,
            family=family,
            family_class=_family_class(family),
            architecture="arm64" if "g" in family else "x86_64",
            data_tiering=family.endswith("gd"),
            burstable=family.startswith("t"),
            network_baseline_mbps=baseline,
            network_reliable_max_mbps=peak,
            network_capacity_kind="BASELINE"
            if baseline is not None
            else "BURST_OR_UP_TO"
            if "up to" in str(label or "").lower()
            else "UNKNOWN",
            network_performance_label=str(label) if label else None,
        )
