"""Versioned conservative storage policy intersected with live AWS ranges."""
from __future__ import annotations

from dataclasses import dataclass


VERSION = "rds-storage-v1"


@dataclass(frozen=True)
class StorageCapabilityBand:
    source_storage_type: str
    target_storage_type: str
    min_allocated_gib: int
    max_allocated_gib: int
    min_iops: int
    max_iops: int
    min_throughput_mibps: int | None
    max_throughput_mibps: int | None
    policy_version: str = VERSION


_COMMON = {
    "gp2": StorageCapabilityBand("gp2", "gp3", 20, 65_536, 3_000, 64_000, 125, 4_000),
    "gp3": StorageCapabilityBand("gp3", "gp3", 20, 65_536, 3_000, 64_000, 125, 4_000),
    "io1": StorageCapabilityBand("io1", "io2", 100, 65_536, 1_000, 256_000, None, None),
}


def storage_capability(engine: str, storage_type: str, allocated_gib: int) -> StorageCapabilityBand | None:
    """Return the matching conservative band; live AWS data remains authoritative."""
    band = _COMMON.get(storage_type)
    if band is None or not band.min_allocated_gib <= allocated_gib <= band.max_allocated_gib:
        return None
    # RDS SQL Server gp3/io storage has narrower combinations. Until the
    # reviewed table carries those bands, runtime ranges alone are not enough.
    if "sqlserver" in engine.lower():
        return None
    return band
