"""Pure intersection of orderable RDS options with current configuration."""
from __future__ import annotations

from typing import Any


def compatible_orderable_options(
    metadata: dict[str, Any], options: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    storage_type = str(metadata.get("StorageType") or metadata.get("storage_type") or "")
    network_type = str(metadata.get("NetworkType") or "")
    availability_zone = str(metadata.get("AvailabilityZone") or metadata.get("availability_zone") or "")
    compatible: list[dict[str, Any]] = []
    for option in options:
        if storage_type and str(option.get("StorageType") or "") != storage_type:
            continue
        if metadata.get("MultiAZ") and not option.get("MultiAZCapable"):
            continue
        if metadata.get("StorageEncrypted") and not option.get("SupportsStorageEncryption"):
            continue
        if metadata.get("Iops") and not option.get("SupportsIops"):
            continue
        if metadata.get("StorageThroughput") and not option.get("SupportsStorageThroughput"):
            continue
        if metadata.get("PerformanceInsightsEnabled") and option.get("SupportsPerformanceInsights") is False:
            continue
        supported_network = option.get("SupportedNetworkTypes") or []
        if network_type and supported_network and network_type not in supported_network:
            continue
        zones = {
            str(item.get("Name"))
            for item in (option.get("AvailabilityZones") or [])
            if isinstance(item, dict) and item.get("Name")
        }
        if availability_zone and zones and availability_zone not in zones:
            continue
        compatible.append(option)
    return compatible
