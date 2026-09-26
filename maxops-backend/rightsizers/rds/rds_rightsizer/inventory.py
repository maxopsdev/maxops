"""RDS V1 inventory scope and feature support rules."""

from __future__ import annotations

from typing import Any


def metadata_value(metadata: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in metadata and metadata[name] is not None:
            return metadata[name]
    return default


def _storage_optimization_active(metadata: dict[str, Any], state: str) -> bool:
    if state.lower() == "storage-optimization":
        return True
    explicit = metadata_value(
        metadata,
        "StorageOptimizationStatus",
        "storage_optimization_status",
        default="",
    )
    if str(explicit).lower() in {"in-progress", "in_progress", "running", "storage-optimization"}:
        return True
    for status in metadata_value(metadata, "StatusInfos", "status_infos", default=[]) or []:
        if not isinstance(status, dict):
            continue
        text = " ".join(str(status.get(key) or "") for key in ("StatusType", "Normal", "Status", "Message"))
        if "storage" in text.lower() and "optim" in text.lower():
            return True
    return False


def scope_reason(metadata: dict[str, Any], state: str) -> str | None:
    """Return the first stable V1 scope reason, or ``None`` when in scope."""
    engine = str(metadata_value(metadata, "Engine", "engine", default="")).lower()
    if engine.startswith("aurora"):
        return "AURORA_REQUIRES_CLUSTER_RIGHTSIZER"
    if metadata_value(metadata, "DBClusterIdentifier", "db_cluster_identifier"):
        return "MULTI_AZ_CLUSTER_REQUIRES_CLUSTER_RIGHTSIZER"
    if engine.startswith("custom-") or metadata_value(metadata, "CustomIamInstanceProfile"):
        return "RDS_CUSTOM_UNSUPPORTED"
    if metadata_value(metadata, "ProcessorFeatures", "processor_features"):
        return "CUSTOM_PROCESSOR_CONFIGURATION_UNSUPPORTED"
    if _storage_optimization_active(metadata, state):
        return "STORAGE_OPTIMIZATION_IN_PROGRESS"
    pending = metadata_value(metadata, "PendingModifiedValues", "pending_modified_values", default={})
    if isinstance(pending, dict) and pending:
        return "PENDING_MODIFICATION"
    parameter_groups = metadata_value(metadata, "DBParameterGroups", "db_parameter_groups", default=[])
    if any(
        isinstance(group, dict)
        and str(group.get("ParameterApplyStatus") or "in-sync") not in {"in-sync", "applied"}
        for group in (parameter_groups or [])
    ):
        return "PENDING_MODIFICATION"
    if state.lower() != "available":
        return "RESOURCE_NOT_AVAILABLE"
    required = (
        engine,
        metadata_value(metadata, "EngineVersion", "engine_version"),
        metadata_value(metadata, "LicenseModel", "license_model"),
        metadata_value(metadata, "DBInstanceClass", "db_instance_class"),
    )
    if any(value in (None, "") for value in required):
        return "INVENTORY_CONFIGURATION_INCOMPLETE"
    return None


def performance_insights_supported(
    metadata: dict[str, Any],
    orderable_options: list[dict[str, Any]] | None = None,
) -> bool:
    """Return false only when the persisted engine/class contract proves PI unsupported."""
    engine = str(metadata_value(metadata, "Engine", "engine", default="")).lower()
    if engine.startswith("db2"):
        return False
    current_class = str(metadata_value(metadata, "DBInstanceClass", "db_instance_class", default=""))
    matching = [
        option
        for option in (orderable_options or [])
        if isinstance(option, dict) and str(option.get("DBInstanceClass") or "") == current_class
    ]
    return not matching or any(option.get("SupportsPerformanceInsights") is not False for option in matching)
