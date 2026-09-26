"""GPU capabilities read from the global EC2 instance specification catalog."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.street_pricing import _resolve_pricing_db_path


@dataclass(frozen=True)
class EC2GpuInfo:
    """GPU identity and addressable-device count for an EC2 instance type."""

    instance_type: str
    device_count: int
    fractional: bool
    total_memory_mib: int | None
    manufacturer: str | None
    model: str | None


def _parse_spec(text: Any) -> dict[str, Any]:
    """Parse one catalog specification, returning an empty object on bad JSON."""
    try:
        value = json.loads(text or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


@lru_cache(maxsize=None)
def _load_gpu_specs(database_path: str) -> dict[str, dict[str, Any]]:
    """Load GPU-bearing specifications once per resolved database path."""
    path = Path(database_path)
    if not path.exists():
        return {}
    try:
        with sqlite3.connect(path) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'ec2_instance_specs'"
            ).fetchone()
            if table is None:
                return {}
            rows = connection.execute(
                "SELECT instance_type, spec_json FROM ec2_instance_specs"
            )
            return {
                str(row[0]): _parse_spec(row[1])
                for row in rows
                if row[0]
            }
    except sqlite3.Error:
        return {}


def _resolved_gpu_specs() -> dict[str, dict[str, Any]]:
    """Return the cached global specification table independent of regional pricing."""
    path = _resolve_pricing_db_path(settings.pricing_database_path)
    return _load_gpu_specs(str(path))


def _integer(value: Any) -> int | None:
    """Convert a catalog number to an integer when it is finite and non-negative."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def gpu_info_from_spec(instance_type: str, spec: dict[str, Any]) -> EC2GpuInfo | None:
    """Parse GPU capability from one specification, or ``None`` when absent.

    A specification whose summed GPU ``Count`` is zero is a fractional slice,
    not a non-GPU instance: one slice is one addressable device and nvidia-smi
    reports its utilization on the same 0–100 scale. Thus fractional catalog
    rows return ``device_count=1`` and ``fractional=True``.
    """
    normalized_type = str(instance_type or "").strip()
    if not normalized_type or not isinstance(spec, dict):
        return None
    if not isinstance(spec, dict) or not isinstance(spec.get("GpuInfo"), dict):
        return None
    gpu_info = spec["GpuInfo"]
    gpus = gpu_info.get("Gpus")
    if not isinstance(gpus, list) or not gpus:
        return None

    count = sum(
        value
        for value in (_integer(gpu.get("Count")) for gpu in gpus if isinstance(gpu, dict))
        if value is not None
    )
    fractional = count == 0
    device_count = count if count > 0 else 1
    first_gpu = next((gpu for gpu in gpus if isinstance(gpu, dict)), {})
    total_memory = _integer(gpu_info.get("TotalGpuMemoryInMiB"))
    first_memory = _integer(
        first_gpu.get("MemoryInfo", {}).get("SizeInMiB")
        if isinstance(first_gpu.get("MemoryInfo"), dict)
        else None
    )
    if total_memory in (None, 0):
        if count and first_memory is not None:
            total_memory = count * first_memory
        elif fractional:
            total_memory = first_memory
    manufacturer = str(first_gpu.get("Manufacturer") or "").strip() or None
    model = str(first_gpu.get("Name") or "").strip() or None
    return EC2GpuInfo(
        instance_type=normalized_type,
        device_count=device_count,
        fractional=fractional,
        total_memory_mib=total_memory,
        manufacturer=manufacturer,
        model=model,
    )


def gpu_info_for_instance_type(instance_type: str) -> EC2GpuInfo | None:
    """Return catalog GPU info, or ``None`` for unknown and non-GPU types."""
    normalized_type = str(instance_type or "").strip()
    if not normalized_type:
        return None
    return gpu_info_from_spec(
        normalized_type, _resolved_gpu_specs().get(normalized_type, {})
    )


def is_gpu_instance_type(instance_type: str) -> bool:
    """Return whether the catalog identifies the type as GPU-backed."""
    return gpu_info_for_instance_type(instance_type) is not None
