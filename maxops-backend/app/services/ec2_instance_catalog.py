"""EC2 candidate catalog backed exclusively by the packaged pricing SQLite DB."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.street_pricing import _resolve_pricing_db_path
from app.utils.ec2_gpu_info import gpu_info_from_spec
# Re-exported: ec2_rightsizer and asg_rightsizer import instance_family from
# here rather than reaching into the rightsizers package directly.
from rightsizers.common.instance_types import instance_family  # noqa: F401


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _attributes(text: str | None) -> dict[str, Any]:
    try:
        value = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def qualitative_network_class(description: str | None) -> str:
    text = (description or "").upper()
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:GIGABIT|GBPS)", text)
    if not match:
        return "UNKNOWN"
    gbps = float(match.group(1))
    if gbps <= 5:
        return "LOW"
    if gbps <= 10:
        return "MODERATE"
    if gbps <= 25:
        return "HIGH"
    if gbps <= 50:
        return "VERY_HIGH"
    return "EXTREME"


@dataclass(frozen=True)
class EC2CatalogEntry:
    instance_type: str
    region: str
    platform: str
    monthly_usd: float
    vcpus: float | None
    memory_mib: float | None
    coremark: float | None
    architectures: tuple[str, ...]
    network_performance: str | None
    network_class: str
    network_baseline_mbps: float | None
    network_reliable_max_mbps: float | None
    network_capacity_kind: str
    eni_limit: int | None
    efa_supported: bool | None
    ebs_supported: bool | None
    ebs_attachment_limit: int | None
    ebs_attachment_limit_type: str | None
    instance_store_device_count: int | None
    ebs_baseline_iops: float | None
    ebs_max_iops: float | None
    ebs_baseline_throughput_mibps: float | None
    ebs_max_throughput_mibps: float | None
    capability_source: str
    attributes: dict[str, Any]
    gpu_device_count: int | None = None
    gpu_fractional: bool = False
    gpu_model: str | None = None
    gpu_memory_mib_per_device: int | None = None


class EC2InstanceCatalog:
    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = _resolve_pricing_db_path(
            database_path or settings.pricing_database_path
        )

    def list_region(
        self, region: str, platform: str = "linux"
    ) -> dict[str, EC2CatalogEntry]:
        if not self.database_path.exists():
            return {}
        entries: dict[str, EC2CatalogEntry] = {}
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            specs = self._load_specs(connection)
            rows = connection.execute(
                "SELECT * FROM street_pricing_ec2 WHERE region_code = ? AND platform_normalized = ?",
                (region, platform),
            )
            for row in rows:
                entry = self._entry(row, specs.get(row["instance_type"], {}))
                entries[entry.instance_type] = entry
        return entries

    def get(
        self, region: str, instance_type: str, platform: str = "linux"
    ) -> EC2CatalogEntry | None:
        return self.list_region(region, platform).get(instance_type)

    def specification_metadata(self) -> dict[str, Any]:
        """Return release identity for the global specification catalog."""
        if not self.database_path.exists():
            return {}
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'ec2_instance_specs'"
            ).fetchone()
            if table is None:
                return {}
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(ec2_instance_specs)")
            }
            if not {"schema_version", "generated_at"}.issubset(columns):
                return {}
            source_column = "source_region" if "source_region" in columns else None
            selected = "schema_version, generated_at"
            if source_column:
                selected += f", {source_column}"
            row = connection.execute(
                f"SELECT {selected} FROM ec2_instance_specs "
                "ORDER BY generated_at DESC, schema_version DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return {}
            return {
                "schema_version": row["schema_version"],
                "generated_at": row["generated_at"],
                "source_region": row[source_column] if source_column else None,
            }

    @staticmethod
    def _load_specs(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ec2_instance_specs'"
        ).fetchone()
        if table is None:
            return {}
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(ec2_instance_specs)")
        }
        if "source_region" in columns:
            rows = connection.execute(
                "SELECT instance_type, spec_json FROM ec2_instance_specs"
            )
            return {row["instance_type"]: _attributes(row["spec_json"]) for row in rows}
        if "region_code" not in columns:
            return {}

        # Backward compatibility for pre-global derived catalogs.  EC2 product
        # specifications are region-invariant; prefer us-east-1 when duplicate
        # regional rows exist and otherwise retain a deterministic fallback.
        rows = connection.execute(
            """
            SELECT instance_type, spec_json, region_code
            FROM ec2_instance_specs
            ORDER BY CASE WHEN region_code = 'us-east-1' THEN 1 ELSE 0 END,
                     region_code
            """
        )
        return {row["instance_type"]: _attributes(row["spec_json"]) for row in rows}

    @staticmethod
    def _entry(row: sqlite3.Row, spec: dict[str, Any]) -> EC2CatalogEntry:
        attrs = _attributes(row["attributes_json"])
        memory_gib = _number(attrs.get("memory"))
        vpc = attrs.get("vpc") if isinstance(attrs.get("vpc"), dict) else {}
        architectures = attrs.get("arch") if isinstance(attrs.get("arch"), list) else []
        network_info = (
            spec.get("NetworkInfo") if isinstance(spec.get("NetworkInfo"), dict) else {}
        )
        network = (
            network_info.get("NetworkPerformance")
            or spec.get("network_performance")
            or attrs.get("network_performance")
        )
        network_cards = (
            network_info.get("NetworkCards")
            if isinstance(network_info.get("NetworkCards"), list)
            else []
        )
        network_baseline = _number(spec.get("baseline_network_mbps_total"))
        network_peak = _number(spec.get("peak_network_mbps_total"))
        if network_baseline is None and network_cards:
            card_values = [
                _number(card.get("BaselineBandwidthInGbps"))
                for card in network_cards
                if isinstance(card, dict)
            ]
            if card_values and all(value is not None for value in card_values):
                network_baseline = (
                    sum(value for value in card_values if value is not None) * 1000.0
                )
        if network_peak is None and network_cards:
            card_values = [
                _number(card.get("PeakBandwidthInGbps"))
                for card in network_cards
                if isinstance(card, dict)
            ]
            if card_values and all(value is not None for value in card_values):
                network_peak = (
                    sum(value for value in card_values if value is not None) * 1000.0
                )
        ebs_info = spec.get("EbsInfo") if isinstance(spec.get("EbsInfo"), dict) else {}
        ebs_optimized_info = (
            ebs_info.get("EbsOptimizedInfo")
            if isinstance(ebs_info.get("EbsOptimizedInfo"), dict)
            else {}
        )
        supported_root_devices = spec.get("SupportedRootDeviceTypes")
        ebs_supported = (
            "ebs" in {str(item).lower() for item in supported_root_devices}
            if isinstance(supported_root_devices, list)
            else True
            if ebs_info
            else None
        )
        instance_storage = spec.get("InstanceStorageInfo")
        if isinstance(instance_storage, dict):
            disks = (
                instance_storage.get("Disks")
                if isinstance(instance_storage.get("Disks"), list)
                else []
            )
            instance_store_device_count = sum(
                int(_number(disk.get("Count")) or 0)
                for disk in disks
                if isinstance(disk, dict)
            )
        elif spec.get("InstanceStorageSupported") is False:
            instance_store_device_count = 0
        else:
            instance_store_device_count = None
        gpu_info = gpu_info_from_spec(row["instance_type"], spec)
        gpu_memory_per_device = (
            round(gpu_info.total_memory_mib / gpu_info.device_count)
            if gpu_info is not None
            and gpu_info.total_memory_mib is not None
            and gpu_info.device_count > 0
            else None
        )
        return EC2CatalogEntry(
            instance_type=row["instance_type"],
            region=row["region_code"],
            platform=row["platform_normalized"],
            monthly_usd=float(row["monthly_usd"]),
            vcpus=_number(attrs.get("vCPU")),
            memory_mib=memory_gib * 1024.0 if memory_gib is not None else None,
            coremark=_number(attrs.get("coremark_iterations_second")),
            architectures=tuple(str(item).lower() for item in architectures),
            network_performance=str(network) if network else None,
            network_class=qualitative_network_class(str(network) if network else None),
            network_baseline_mbps=network_baseline,
            network_reliable_max_mbps=network_peak,
            network_capacity_kind="BASELINE"
            if network_baseline is not None
            else "BURST_OR_UP_TO"
            if "up to" in str(network or "").lower()
            else "UNKNOWN",
            eni_limit=int(vpc["max_enis"])
            if _number(vpc.get("max_enis")) is not None
            else int(network_info["MaximumNetworkInterfaces"])
            if _number(network_info.get("MaximumNetworkInterfaces")) is not None
            else None,
            efa_supported=network_info.get("EfaSupported")
            if "EfaSupported" in network_info
            else None,
            ebs_supported=ebs_supported,
            ebs_attachment_limit=int(ebs_info["MaximumEbsAttachments"])
            if _number(ebs_info.get("MaximumEbsAttachments")) is not None
            else int(spec["maximum_ebs_attachments"])
            if _number(spec.get("maximum_ebs_attachments")) is not None
            else None,
            ebs_attachment_limit_type=str(
                ebs_info.get("AttachmentLimitType")
                or spec.get("ebs_attachment_limit_type")
                or ""
            ).lower()
            or None,
            instance_store_device_count=instance_store_device_count,
            ebs_baseline_iops=_number(ebs_optimized_info.get("BaselineIops"))
            or _number(attrs.get("ebs_baseline_iops")),
            ebs_max_iops=_number(ebs_optimized_info.get("MaximumIops"))
            or _number(attrs.get("ebs_iops")),
            ebs_baseline_throughput_mibps=_number(
                ebs_optimized_info.get("BaselineThroughputInMBps")
            )
            or _number(attrs.get("ebs_baseline_throughput")),
            ebs_max_throughput_mibps=_number(
                ebs_optimized_info.get("MaximumThroughputInMBps")
            )
            or _number(attrs.get("ebs_throughput")),
            capability_source="describe_instance_types"
            if spec
            else "street_pricing_attributes",
            attributes=attrs,
            gpu_device_count=gpu_info.device_count if gpu_info else None,
            gpu_fractional=gpu_info.fractional if gpu_info else False,
            gpu_model=gpu_info.model if gpu_info else None,
            gpu_memory_mib_per_device=gpu_memory_per_device,
        )
