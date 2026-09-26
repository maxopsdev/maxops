"""Deterministic RDS class and storage recommendation evaluation."""

from __future__ import annotations

import math
from dataclasses import asdict
from decimal import Decimal
from typing import Any, Callable

from .catalogs import RdsClassEntry, storage_capability
from .models import (
    AVAILABILITY_NOTE,
    CLASS_OUTAGE_NOTE,
    MULTI_AZ_NOTE,
    STORAGE_NOTE,
    EvaluationStatus,
    RdsRightsizerPolicy,
)
from .inventory import metadata_value as _metadata_value, scope_reason as _scope_reason
from rightsizers.common.tiers import pick_tier_candidates
from .risk.assessment import RISK_ORDER, assess_risk, ratio_risk
from .warnings import warning_details


PriceLookup = Callable[[str, str, str, bool, str], float | None]


def _metric(metrics: dict[str, Any], name: str, window: str = "60d") -> dict[str, Any]:
    windows = metrics.get("windows") if isinstance(metrics.get("windows"), dict) else {}
    value = windows.get(window, {}).get(name, {})
    return value if isinstance(value, dict) else {}


def _decision(metrics: dict[str, Any], name: str) -> float | None:
    selected = metrics.get("selected_decision_values")
    value = selected.get(name) if isinstance(selected, dict) else None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _all_status(metrics: dict[str, Any], name: str, status: str) -> bool:
    return all(_metric(metrics, name, window).get("status") == status for window in ("14d", "30d", "60d"))


def _thin_codes(metrics: dict[str, Any], policy: RdsRightsizerPolicy) -> list[str]:
    codes: list[str] = []
    for name in ("cpu_percent", "freeable_memory_bytes"):
        summaries = [_metric(metrics, name, window) for window in ("14d", "30d", "60d")]
        present = [summary for summary in summaries if summary.get("status") == "PRESENT"]
        if present and max(float(item.get("observed_days") or 0) for item in present) < policy.min_observed_days:
            codes.append("OBSERVATION_WINDOW_TOO_SHORT")
        if present and max(float(item.get("coverage_ratio") or 0) for item in present) < policy.min_required_coverage_ratio:
            codes.append("OBSERVATION_COVERAGE_TOO_LOW")
    return list(dict.fromkeys(codes))


def _orderable_classes(metadata: dict[str, Any]) -> set[str] | None:
    context = metadata.get("rightsizing_context") if isinstance(metadata.get("rightsizing_context"), dict) else {}
    if context.get("orderable_status") not in {None, "SUCCESS"}:
        return None
    options = context.get("orderable_options") or metadata.get("orderable_target_classes") or []
    result: set[str] = set()
    for option in options:
        if isinstance(option, str):
            result.add(option)
        elif isinstance(option, dict) and option.get("DBInstanceClass"):
            result.add(str(option["DBInstanceClass"]))
    return result


def _price_decimal(value: float | None) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _class_price(
    metadata: dict[str, Any],
    price_lookup: PriceLookup,
    region: str,
    db_instance_class: str,
    engine: str,
    multi_az: bool,
    license_model: str,
) -> Decimal | None:
    """Prefer the exact price persisted by the scan, then use the packaged catalog."""
    context = metadata.get("rightsizing_context") if isinstance(metadata.get("rightsizing_context"), dict) else {}
    prices = context.get("class_prices") if isinstance(context.get("class_prices"), dict) else {}
    persisted = prices.get(db_instance_class)
    if isinstance(persisted, dict):
        persisted = persisted.get("monthly")
    try:
        if persisted is not None:
            return Decimal(str(persisted))
    except (TypeError, ValueError):
        pass
    return _price_decimal(price_lookup(region, db_instance_class, engine, multi_az, license_model))


def _evaluate_class(
    inventory: dict[str, Any],
    metadata: dict[str, Any],
    metrics: dict[str, Any],
    catalog: dict[str, RdsClassEntry],
    price_lookup: PriceLookup,
    policy: RdsRightsizerPolicy,
    min_savings: Decimal,
    candidate_limit: int,
) -> tuple[dict[str, Any] | None, str, list[str], dict[str, int]]:
    context = metadata.get("rightsizing_context") if isinstance(metadata.get("rightsizing_context"), dict) else {}
    if metrics.get("collection", {}).get("status") != "SUCCESS":
        return None, EvaluationStatus.INSUFFICIENT_DATA.value, ["RDS_CLOUDWATCH_COLLECTION_FAILED"], {}
    if _all_status(metrics, "cpu_percent", "INVALID") or _all_status(metrics, "freeable_memory_bytes", "INVALID"):
        return None, EvaluationStatus.INSUFFICIENT_DATA.value, ["RDS_CLOUDWATCH_COLLECTION_FAILED"], {}
    orderable = _orderable_classes(metadata)
    if orderable is None:
        return None, EvaluationStatus.INSUFFICIENT_DATA.value, ["RDS_ORDERABLE_OPTIONS_COLLECTION_FAILED"], {}
    current_type = str(_metadata_value(metadata, "DBInstanceClass", "db_instance_class", default=inventory.get("db_instance_class") or ""))
    current = catalog.get(current_type)
    if current is None or current.vcpus is None or current.memory_gib is None:
        return None, EvaluationStatus.INSUFFICIENT_DATA.value, ["CURRENT_COMPUTE_CAPACITY_UNKNOWN"], {}
    engine = str(_metadata_value(metadata, "Engine", "engine", default=inventory.get("engine") or ""))
    license_model = str(_metadata_value(metadata, "LicenseModel", "license_model", default=""))
    region = str(inventory.get("region") or "")
    multi_az = bool(_metadata_value(metadata, "MultiAZ", "multi_az", default=False))
    current_price = _class_price(metadata, price_lookup, region, current_type, engine, multi_az, license_model)
    if current_price is None:
        return None, EvaluationStatus.NO_RECOMMENDATION.value, ["CURRENT_PRICING_UNAVAILABLE"], {}

    cpu_empty = _all_status(metrics, "cpu_percent", "EMPTY")
    memory_empty = _all_status(metrics, "freeable_memory_bytes", "EMPTY")
    cpu_p99 = _decision(metrics, "cpu_percent")
    freeable_p01 = _decision(metrics, "freeable_memory_bytes")
    if not cpu_empty and cpu_p99 is None:
        return None, EvaluationStatus.INSUFFICIENT_DATA.value, ["RDS_CLOUDWATCH_COLLECTION_FAILED"], {}
    if not memory_empty and freeable_p01 is None:
        return None, EvaluationStatus.INSUFFICIENT_DATA.value, ["RDS_CLOUDWATCH_COLLECTION_FAILED"], {}
    cpu_used = None if cpu_empty else current.vcpus * float(cpu_p99 or 0) / 100
    memory_demand = None if memory_empty else min(max(current.memory_gib - float(freeable_p01 or 0) / 1024**3, 0), current.memory_gib)
    thin = _thin_codes(metrics, policy)
    pi = metrics.get("performance_insights") if isinstance(metrics.get("performance_insights"), dict) else {}
    pi_status = str(pi.get("status") or "NOT_NEEDED")
    pi_required = bool(pi.get("required"))
    pi_cpu_p99 = None
    summaries = pi.get("summaries") if isinstance(pi.get("summaries"), dict) else {}
    if summaries:
        try:
            pi_cpu_p99 = float((summaries.get("cpu_load") or {}).get("p99"))
        except (TypeError, ValueError):
            pi_cpu_p99 = None
    candidates: list[dict[str, Any]] = []
    rejected: dict[str, int] = {}
    max_target = policy.aggressive_target_util
    network_directions = (
        _decision(metrics, "network_rx_bps"),
        _decision(metrics, "network_tx_bps"),
    )
    known_network = [value for value in network_directions if value is not None]
    network_demand = max(known_network) if known_network else None
    iops = _decision(metrics, "total_iops")
    throughput_bps = _decision(metrics, "total_throughput_bps")
    throughput_mibps = throughput_bps / 1024**2 if throughput_bps is not None else None
    swap = _decision(metrics, "swap_bytes") or 0
    base_warnings = list(thin)
    if cpu_empty:
        base_warnings.append("RDS_CPU_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED")
    if memory_empty:
        base_warnings.append("RDS_MEMORY_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED")
    if len(known_network) < 2:
        base_warnings.append("NETWORK_DIRECTIONAL_METRICS_INCOMPLETE")
    if iops is None or throughput_mibps is None:
        base_warnings.append("STORAGE_DIRECTIONAL_METRICS_INCOMPLETE")
    allocated_storage = float(
        _metadata_value(metadata, "AllocatedStorage", "allocated_storage", default=0) or 0
    )
    free_storage = _decision(metrics, "free_storage_bytes")
    if (
        allocated_storage
        and free_storage is not None
        and free_storage / (allocated_storage * 1024**3)
        < policy.free_storage_warning_ratio
    ):
        base_warnings.append("LOW_FREE_STORAGE_REQUIRES_REVIEW")
    replica_lag = _decision(metrics, "replica_lag_seconds")
    if replica_lag is not None and replica_lag >= policy.replica_lag_warning_seconds:
        base_warnings.append("REPLICA_LAG_REQUIRES_REVIEW")
    if context.get("pending_maintenance_actions"):
        base_warnings.append("PENDING_MAINTENANCE_REQUIRES_REVIEW")
    event_categories = {
        str(item).lower() for item in (context.get("recent_event_categories") or [])
    }
    if event_categories & {"failure", "failover", "maintenance"}:
        base_warnings.append("RECENT_RDS_EVENT_REQUIRES_REVIEW")
    connection_context = (
        context.get("connection_limit")
        if isinstance(context.get("connection_limit"), dict)
        else {}
    )
    max_connections = connection_context.get("resolved_value") or _metadata_value(
        metadata, "max_connections", "MaxConnections"
    )
    target_connection_limits = (
        context.get("target_connection_limits")
        if isinstance(context.get("target_connection_limits"), dict)
        else {}
    )
    connections = _decision(metrics, "connections")
    try:
        if (
            connections is not None
            and max_connections
            and connections / float(max_connections) >= policy.connection_warning_ratio
        ):
            base_warnings.append("CONNECTION_HEADROOM_REQUIRES_REVIEW")
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    for balance_name, code in (
        ("cpu_credit_balance", "CPU_CREDIT_DEPLETION_REQUIRES_REVIEW"),
        ("ebs_io_balance_percent", "STORAGE_CREDIT_DEPLETION_REQUIRES_REVIEW"),
        ("ebs_byte_balance_percent", "STORAGE_CREDIT_DEPLETION_REQUIRES_REVIEW"),
    ):
        balance = _decision(metrics, balance_name)
        if balance is not None and balance <= 0:
            base_warnings.append(code)
    if pi_required and pi_status == "DISABLED":
        base_warnings.append("PERFORMANCE_INSIGHTS_REQUIRED_FOR_ATTRIBUTION")
    elif pi_required and pi_status in {"ACCESS_DENIED", "ERROR"}:
        base_warnings.append("PERFORMANCE_INSIGHTS_TELEMETRY_UNAVAILABLE")
    elif pi_required and pi_status == "UNSUPPORTED":
        base_warnings.append("PERFORMANCE_INSIGHTS_UNSUPPORTED")
    if pi_status == "AVAILABLE":
        unattributed_share = (pi.get("truncation") or {}).get("unattributed_share")
        try:
            if (
                unattributed_share is not None
                and float(unattributed_share)
                > policy.db_load_unattributed_warning_ratio
            ):
                base_warnings.append("DB_LOAD_ATTRIBUTION_INCOMPLETE")
        except (TypeError, ValueError):
            pass
    base_warnings = list(dict.fromkeys(base_warnings))

    for target_type in sorted(orderable):
        if target_type == current_type:
            continue
        target = catalog.get(target_type)
        if target is None or target.vcpus is None or target.memory_gib is None:
            rejected["TARGET_COMPUTE_CAPACITY_UNKNOWN"] = rejected.get("TARGET_COMPUTE_CAPACITY_UNKNOWN", 0) + 1
            continue
        if target.burstable != current.burstable or target.local_nvme != current.local_nvme:
            rejected["FAMILY_NOT_ELIGIBLE"] = rejected.get("FAMILY_NOT_ELIGIBLE", 0) + 1
            continue
        gated = set(policy.gated_previous_generation_families)
        if _family(target_type) in gated and _family(current_type) not in gated:
            rejected["FAMILY_NOT_ELIGIBLE"] = rejected.get("FAMILY_NOT_ELIGIBLE", 0) + 1
            continue
        projected_cpu = None if cpu_used is None else cpu_used / target.vcpus
        projected_memory = None if memory_demand is None else memory_demand / target.memory_gib
        projected_free = None if memory_demand is None else target.memory_gib - memory_demand
        if cpu_used is None and target.vcpus < current.vcpus:
            rejected["CPU_CAPACITY_EXCEEDED"] = rejected.get("CPU_CAPACITY_EXCEEDED", 0) + 1
            continue
        if memory_demand is None and target.memory_gib < current.memory_gib:
            rejected["MEMORY_CAPACITY_EXCEEDED"] = rejected.get("MEMORY_CAPACITY_EXCEEDED", 0) + 1
            continue
        components = [value for value in (projected_cpu, projected_memory, pi_cpu_p99 / target.vcpus if pi_cpu_p99 is not None else None) if value is not None]
        projected_util = max(components) if components else None
        projected_pi_cpu = pi_cpu_p99 / target.vcpus if pi_cpu_p99 is not None else None
        if projected_cpu is not None and projected_cpu > max_target:
            rejected["CPU_CAPACITY_EXCEEDED"] = rejected.get("CPU_CAPACITY_EXCEEDED", 0) + 1
            continue
        if projected_memory is not None and projected_memory > max_target:
            rejected["MEMORY_CAPACITY_EXCEEDED"] = rejected.get("MEMORY_CAPACITY_EXCEEDED", 0) + 1
            continue
        if projected_pi_cpu is not None and projected_pi_cpu > max_target:
            rejected["DB_LOAD_CPU_EXCEEDS_TARGET"] = rejected.get("DB_LOAD_CPU_EXCEEDS_TARGET", 0) + 1
            continue
        floor = policy.sqlserver_memory_absolute_free_floor_gib if "sqlserver" in engine.lower() else policy.memory_absolute_free_floor_gib
        if projected_free is not None and projected_free < floor:
            rejected["MEMORY_ABSOLUTE_FLOOR_VIOLATED"] = rejected.get("MEMORY_ABSOLUTE_FLOOR_VIOLATED", 0) + 1
            continue
        if target.burstable and target.cpu_baseline_ratio is not None and projected_cpu is not None and projected_cpu >= target.cpu_baseline_ratio * 0.9:
            rejected["BURSTABLE_CPU_BASELINE_EXCEEDED"] = rejected.get("BURSTABLE_CPU_BASELINE_EXCEEDED", 0) + 1
            continue
        target_price = _class_price(metadata, price_lookup, region, target_type, engine, multi_az, license_model)
        if target_price is None:
            rejected["TARGET_PRICING_UNAVAILABLE"] = rejected.get("TARGET_PRICING_UNAVAILABLE", 0) + 1
            continue
        savings = current_price - target_price
        if savings < max(policy.min_monthly_savings, min_savings):
            continue
        warnings = list(base_warnings)
        if swap >= policy.swap_warning_bytes and target.memory_gib < current.memory_gib:
            warnings.append("SWAP_USAGE_REQUIRES_REVIEW")
        target_max_connections = target_connection_limits.get(target_type)
        if target.memory_gib < current.memory_gib:
            try:
                target_headroom_known = (
                    connections is not None
                    and target_max_connections is not None
                    and float(target_max_connections) > 0
                )
                if not target_headroom_known or connections / float(target_max_connections) >= policy.connection_warning_ratio:
                    warnings.append("CONNECTION_HEADROOM_REQUIRES_REVIEW")
            except (TypeError, ValueError, ZeroDivisionError):
                warnings.append("CONNECTION_HEADROOM_REQUIRES_REVIEW")
        if "sqlserver" in engine.lower() and target.memory_gib < current.memory_gib:
            warnings.append("SQLSERVER_MEMORY_PROJECTION_REQUIRES_REVIEW")
        network_ratio = network_demand / target.network_baseline_mbps if network_demand is not None and target.network_baseline_mbps else None
        if target.network_peak_mbps is not None and network_demand is not None and network_demand > target.network_peak_mbps:
            rejected["NETWORK_RELIABLE_MAX_EXCEEDED"] = rejected.get("NETWORK_RELIABLE_MAX_EXCEEDED", 0) + 1
            continue
        if target.network_baseline_mbps is None:
            warnings.append("TARGET_NETWORK_CAPACITY_UNKNOWN")
        ebs_iops_ratio = iops / target.ebs_baseline_iops if iops is not None and target.ebs_baseline_iops else None
        ebs_throughput_ratio = throughput_mibps / target.ebs_baseline_mbps if throughput_mibps is not None and target.ebs_baseline_mbps else None
        if target.ebs_peak_iops is not None and iops is not None and iops > target.ebs_peak_iops:
            rejected["EBS_RELIABLE_MAX_EXCEEDED"] = rejected.get("EBS_RELIABLE_MAX_EXCEEDED", 0) + 1
            continue
        if target.ebs_peak_mbps is not None and throughput_mibps is not None and throughput_mibps > target.ebs_peak_mbps:
            rejected["EBS_RELIABLE_MAX_EXCEEDED"] = rejected.get("EBS_RELIABLE_MAX_EXCEEDED", 0) + 1
            continue
        if target.ebs_baseline_iops is None or target.ebs_baseline_mbps is None:
            warnings.append("TARGET_EBS_CAPACITY_UNKNOWN")
        if pi_status == "AVAILABLE":
            total_p99 = (summaries.get("total_load") or {}).get("p99")
            non_cpu_p99 = (summaries.get("non_cpu_load") or {}).get("p99")
            try:
                if total_p99 is not None and non_cpu_p99 is not None and float(total_p99) > target.vcpus and float(non_cpu_p99) >= float(total_p99) / 2:
                    warnings.append("NON_CPU_DB_LOAD_REQUIRES_REVIEW")
            except (TypeError, ValueError):
                pass
        comparable = {
            "cpu": projected_cpu,
            "memory": projected_memory,
            "db_load_cpu": pi_cpu_p99 / target.vcpus if pi_cpu_p99 is not None else None,
            "network": network_ratio,
            "storage_iops": ebs_iops_ratio,
            "storage_throughput": ebs_throughput_ratio,
        }
        known = {key: value for key, value in comparable.items() if value is not None}
        binding = max(known, key=known.get) if known else None
        satisfied = [name for name, ratio in policy.tier_ratios if projected_util is not None and projected_util <= ratio]
        risk = assess_risk(
            warnings,
            compute=ratio_risk(
                projected_cpu,
                policy.balanced_target_util,
                policy.aggressive_target_util,
            ) if projected_cpu is not None else "HIGH",
            memory=ratio_risk(
                projected_memory,
                policy.balanced_target_util,
                policy.aggressive_target_util,
            ) if projected_memory is not None else "HIGH",
            network=ratio_risk(network_ratio, policy.network_medium_ratio, policy.network_high_ratio),
            storage=max(
                (ratio_risk(value, policy.ebs_medium_ratio, policy.ebs_high_ratio) for value in (ebs_iops_ratio, ebs_throughput_ratio) if value is not None),
                default="HIGH",
                key=lambda value: RISK_ORDER[value],
            ),
            db_load="LOW" if pi_status == "AVAILABLE" else "NOT_NEEDED" if not pi_required else "HIGH",
        )
        classification = "CONDITIONAL" if warnings or risk["overall"] != "LOW" else "ACTIONABLE"
        candidates.append(
            {
                "kind": "DB_INSTANCE_CLASS_CHANGE",
                "rank": 0,
                "target_db_instance_class": target_type,
                "target_vcpus": target.vcpus,
                "target_memory_gib": target.memory_gib,
                "projected_cpu_percent": projected_cpu * 100 if projected_cpu is not None else None,
                "projected_memory_util": projected_memory,
                "projected_freeable_gib": projected_free,
                "projected_util": projected_util,
                "binding_dimension": binding,
                "target_monthly_cost": float(target_price),
                "monthly_savings": float(savings),
                "yearly_savings": float(savings * 12),
                "classification": classification,
                "satisfied_tiers": satisfied,
                "risk_assessment": risk,
                "reason_codes": list(dict.fromkeys(warnings)),
                "warning_details": warning_details(warnings),
                "evidence": {
                    "cpu_p99_percent": cpu_p99,
                    "freeable_memory_p01_bytes": freeable_p01,
                    "network_p99_mbps": network_demand,
                    "storage_iops_p99": iops,
                    "storage_throughput_p99_mibps": throughput_mibps,
                    "target_network_baseline_mbps": target.network_baseline_mbps,
                    "target_network_peak_mbps": target.network_peak_mbps,
                    "target_ebs_baseline_iops": target.ebs_baseline_iops,
                    "target_ebs_peak_iops": target.ebs_peak_iops,
                    "target_ebs_baseline_mibps": target.ebs_baseline_mbps,
                    "target_ebs_peak_mibps": target.ebs_peak_mbps,
                },
            }
        )

    candidates.sort(key=lambda item: (-item["monthly_savings"], item["target_db_instance_class"]))
    for index, candidate in enumerate(candidates, 1):
        candidate["rank"] = index
    tiers: dict[str, Any] = {"default": None, "conservative": None, "balanced": None, "aggressive": None}
    picks = pick_tier_candidates(
        candidates,
        policy.tier_ratios,
        selection_key=lambda item: (item["target_monthly_cost"], item["target_db_instance_class"]),
    )
    for tier, candidate in picks.items():
        tiers[tier] = candidate
    tiers["default"] = "balanced" if tiers["balanced"] else "conservative" if tiers["conservative"] else "aggressive" if tiers["aggressive"] else None
    reserved = {item["target_db_instance_class"] for item in tiers.values() if isinstance(item, dict)}
    selected = [item for item in candidates if item["target_db_instance_class"] in reserved]
    selected.extend(item for item in candidates if item["target_db_instance_class"] not in reserved)
    selected = selected[:candidate_limit]
    if not selected:
        return None, EvaluationStatus.NO_RECOMMENDATION.value, sorted(rejected) or ["NO_SAVING_CLASS_TARGET"], rejected
    classification = "ACTIONABLE" if any(item["classification"] == "ACTIONABLE" for item in selected) else "CONDITIONAL"
    return {
        "kind": "DB_INSTANCE_CLASS_CHANGE",
        "classification": classification,
        "tiers": tiers,
        "candidates": selected,
    }, EvaluationStatus.RECOMMENDED.value, [], rejected


def _range_contains(
    ranges: list[dict[str, Any]], value: float, *, required: bool = False
) -> bool:
    if not ranges:
        return not required
    return any(float(item.get("From") or 0) <= value <= float(item.get("To") or math.inf) for item in ranges)


def _family(db_instance_class: str) -> str:
    parts = db_instance_class.split(".")
    return parts[1] if len(parts) > 2 else ""


def _storage_prices(metadata: dict[str, Any], storage_type: str) -> dict[str, float] | None:
    context = metadata.get("rightsizing_context") if isinstance(metadata.get("rightsizing_context"), dict) else {}
    prices = context.get("storage_prices") if isinstance(context.get("storage_prices"), dict) else {}
    value = prices.get(storage_type)
    if not isinstance(value, dict):
        return None
    numeric_keys = {
        "storage_gib_month",
        "iops_month",
        "throughput_mibps_month",
        "included_iops",
        "included_throughput_mibps",
    }
    result: dict[str, float] = {}
    for key in numeric_keys:
        number = value.get(key)
        if number is None:
            continue
        try:
            result[key] = float(number)
        except (TypeError, ValueError):
            return None
    return result if "storage_gib_month" in result else None


def _evaluate_storage(
    inventory: dict[str, Any],
    metadata: dict[str, Any],
    metrics: dict[str, Any],
    policy: RdsRightsizerPolicy,
    min_savings: Decimal,
) -> tuple[dict[str, Any] | None, str, list[str]]:
    storage_type = str(_metadata_value(metadata, "StorageType", "storage_type", default=""))
    if _metadata_value(metadata, "DedicatedLogVolume", "dedicated_log_volume", default=False) or _metadata_value(metadata, "AdditionalStorageVolumes", "additional_storage_volumes", default=[]):
        return None, EvaluationStatus.NOT_APPLICABLE.value, ["MULTI_VOLUME_STORAGE_OPTIMIZATION_UNSUPPORTED"]
    if storage_type not in {"gp2", "gp3", "io1"}:
        return None, EvaluationStatus.NOT_APPLICABLE.value, ["STORAGE_CONFIGURATION_KIND_NOT_APPLICABLE"]
    if metrics.get("collection", {}).get("status") != "SUCCESS":
        return None, EvaluationStatus.INSUFFICIENT_DATA.value, ["RDS_CLOUDWATCH_COLLECTION_FAILED"]
    total_iops = _metric(metrics, "total_iops")
    total_throughput = _metric(metrics, "total_throughput_bps")
    if total_iops.get("status") != "PRESENT" or total_throughput.get("status") != "PRESENT":
        return None, EvaluationStatus.NO_RECOMMENDATION.value, ["STORAGE_DIRECTIONAL_METRICS_INCOMPLETE"]
    if float(total_iops.get("observed_days") or 0) < policy.storage_min_observed_days:
        return None, EvaluationStatus.NO_RECOMMENDATION.value, ["STORAGE_OBSERVATION_WINDOW_TOO_SHORT"]
    if float(total_iops.get("coverage_ratio") or 0) < policy.storage_min_coverage_ratio:
        return None, EvaluationStatus.NO_RECOMMENDATION.value, ["STORAGE_OBSERVATION_COVERAGE_TOO_LOW"]
    if (
        float(total_iops.get("pairing_ratio") or 0) < policy.storage_min_coverage_ratio
        or float(total_throughput.get("pairing_ratio") or 0) < policy.storage_min_coverage_ratio
    ):
        return None, EvaluationStatus.NO_RECOMMENDATION.value, ["STORAGE_DIRECTIONAL_METRICS_INCOMPLETE"]
    context = metadata.get("rightsizing_context") if isinstance(metadata.get("rightsizing_context"), dict) else {}
    if context.get("valid_storage_status") not in {None, "SUCCESS"}:
        return None, EvaluationStatus.INSUFFICIENT_DATA.value, ["RDS_VALID_STORAGE_OPTIONS_COLLECTION_FAILED"]
    valid_options = context.get("valid_storage_options") or metadata.get("valid_storage_options") or []
    allocated = int(_metadata_value(metadata, "AllocatedStorage", "allocated_storage", default=0) or 0)
    capability = storage_capability(str(inventory.get("engine") or ""), storage_type, allocated)
    if capability is None:
        return None, EvaluationStatus.NO_RECOMMENDATION.value, ["STORAGE_STATIC_POLICY_GAP"]
    target_type = capability.target_storage_type
    option = next((item for item in valid_options if item.get("StorageType") == target_type), None)
    if not isinstance(option, dict):
        return None, EvaluationStatus.NO_RECOMMENDATION.value, ["STORAGE_RUNTIME_CONSTRAINT_VIOLATED"]
    iops_p99 = _decision(metrics, "total_iops") or 0
    throughput_p99 = (_decision(metrics, "total_throughput_bps") or 0) / 1024**2
    target_iops = int(math.ceil(max(iops_p99 / policy.balanced_target_util, capability.min_iops) / 100) * 100)
    target_throughput = int(math.ceil(max(
        throughput_p99 / policy.balanced_target_util,
        capability.min_throughput_mibps or 0,
    )))
    if not capability.min_iops <= target_iops <= capability.max_iops:
        return None, EvaluationStatus.NO_RECOMMENDATION.value, ["STORAGE_STATIC_POLICY_CONSTRAINT_VIOLATED"]
    if capability.min_throughput_mibps is not None:
        target_throughput = max(target_throughput, capability.min_throughput_mibps)
    if capability.max_throughput_mibps is not None and target_throughput > capability.max_throughput_mibps:
        return None, EvaluationStatus.NO_RECOMMENDATION.value, ["STORAGE_STATIC_POLICY_CONSTRAINT_VIOLATED"]
    throughput_ranges = option.get("StorageThroughput") or []
    if capability.min_throughput_mibps is None and not throughput_ranges:
        target_throughput = 0
    iops_per_gib = target_iops / allocated if allocated else math.inf
    throughput_per_iops = target_throughput / target_iops if target_iops else math.inf
    if (
        not _range_contains(
            option.get("ProvisionedIops") or [], target_iops, required=True
        )
        or not _range_contains(
            throughput_ranges,
            target_throughput,
            required=target_throughput > 0,
        )
        or not _range_contains(option.get("IopsToStorageRatio") or [], iops_per_gib)
        or not _range_contains(option.get("StorageThroughputToIopsRatio") or [], throughput_per_iops)
    ):
        return None, EvaluationStatus.NO_RECOMMENDATION.value, ["STORAGE_RUNTIME_CONSTRAINT_VIOLATED"]
    current_iops = int(_metadata_value(metadata, "Iops", "iops", default=0) or 0)
    current_throughput = int(_metadata_value(metadata, "StorageThroughput", "storage_throughput", default=0) or 0)
    current_prices = _storage_prices(metadata, storage_type)
    target_prices = _storage_prices(metadata, target_type)
    if current_prices is None or target_prices is None:
        return None, EvaluationStatus.NO_RECOMMENDATION.value, ["TARGET_PRICING_UNAVAILABLE"]

    def cost(prices: dict[str, float], iops: int, throughput: int) -> Decimal | None:
        included_iops = int(prices.get("included_iops", 0))
        included_throughput = int(prices.get("included_throughput_mibps", 0))
        if iops > included_iops and "iops_month" not in prices:
            return None
        if throughput > included_throughput and "throughput_mibps_month" not in prices:
            return None
        return (
            Decimal(str(prices.get("storage_gib_month", 0))) * allocated
            + Decimal(str(prices.get("iops_month", 0))) * max(iops - included_iops, 0)
            + Decimal(str(prices.get("throughput_mibps_month", 0))) * max(throughput - included_throughput, 0)
        )

    current_cost = cost(current_prices, current_iops, current_throughput)
    target_cost = cost(target_prices, target_iops, target_throughput)
    if current_cost is None or target_cost is None:
        return None, EvaluationStatus.NO_RECOMMENDATION.value, ["TARGET_PRICING_UNAVAILABLE"]
    savings = current_cost - target_cost
    if savings < max(policy.min_monthly_savings, min_savings):
        return None, EvaluationStatus.NO_RECOMMENDATION.value, ["NO_SAVING_STORAGE_TARGET"]
    warnings: list[str] = []
    latency = max(_decision(metrics, "read_latency_seconds") or 0, _decision(metrics, "write_latency_seconds") or 0)
    latency_threshold = policy.latency_warning_seconds_by_engine.get(
        str(inventory.get("engine") or "").lower(),
        policy.latency_warning_seconds_by_engine.get("*"),
    )
    if latency_threshold is not None and latency >= latency_threshold:
        return None, EvaluationStatus.NO_RECOMMENDATION.value, ["STORAGE_LATENCY_REQUIRES_REVIEW"]
    queue_threshold = policy.queue_depth_warning_by_storage.get(storage_type)
    queue_depth = _decision(metrics, "disk_queue_depth")
    if queue_threshold is not None and queue_depth is not None and queue_depth >= queue_threshold:
        return None, EvaluationStatus.NO_RECOMMENDATION.value, ["STORAGE_QUEUE_REQUIRES_REVIEW"]
    for metric_name in ("burst_balance_percent", "ebs_io_balance_percent", "ebs_byte_balance_percent"):
        balance = _decision(metrics, metric_name)
        if balance is not None and balance <= 0:
            return None, EvaluationStatus.NO_RECOMMENDATION.value, ["STORAGE_CREDIT_DEPLETION_REQUIRES_REVIEW"]
    risk = assess_risk(warnings, storage="LOW")
    return {
        "kind": "STORAGE_CONFIGURATION_CHANGE",
        "classification": "ACTIONABLE",
        "current_storage": {
            "storage_type": storage_type,
            "allocated_storage_gib": allocated,
            "iops": current_iops or None,
            "throughput_mibps": current_throughput or None,
        },
        "target_storage": {
            "storage_type": target_type,
            "allocated_storage_gib": allocated,
            "iops": target_iops,
            "throughput_mibps": target_throughput or None,
        },
        "monthly_savings": float(savings),
        "yearly_savings": float(savings * 12),
        "evidence": {"iops_p99": iops_p99, "throughput_p99_mibps": throughput_p99, "target_utilization": policy.balanced_target_util},
        "risk_assessment": risk,
        "reason_codes": warnings,
        "warning_details": warning_details(warnings),
    }, EvaluationStatus.RECOMMENDED.value, []


def evaluate_rds(
    inventory: dict[str, Any],
    catalog: dict[str, RdsClassEntry],
    price_lookup: PriceLookup,
    *,
    policy: RdsRightsizerPolicy | None = None,
    min_monthly_savings: Decimal = Decimal("0"),
    candidate_limit: int = 10,
) -> dict[str, Any]:
    """Evaluate one persisted RDS inventory document without calling AWS."""
    policy = policy or RdsRightsizerPolicy()
    metadata = inventory.get("metadata") if isinstance(inventory.get("metadata"), dict) else {}
    rightsizing_context = metadata.get("rightsizing_context") if isinstance(metadata.get("rightsizing_context"), dict) else {}
    state = str(inventory.get("state") or "")
    engine = str(_metadata_value(metadata, "Engine", "engine", default=inventory.get("engine") or ""))
    engine_version = str(_metadata_value(metadata, "EngineVersion", "engine_version", default=""))
    license_model = str(_metadata_value(metadata, "LicenseModel", "license_model", default=""))
    multi_az = bool(_metadata_value(metadata, "MultiAZ", "multi_az", default=False))
    scope_reason = _scope_reason(metadata, state)
    base = {
        "inventory_id": inventory.get("inventory_id"),
        "resource_id": inventory.get("resource_id"),
        "resource_name": inventory.get("resource_name"),
        "account_id": inventory.get("account_id"),
        "region": inventory.get("region") or "",
        "state": state,
        "engine": engine,
        "engine_version": engine_version,
        "license_model": license_model,
        "multi_az": multi_az,
        "read_replica": bool(_metadata_value(metadata, "ReadReplicaSourceDBInstanceIdentifier", "read_replica_source")),
        "current": {
            "db_instance_class": _metadata_value(metadata, "DBInstanceClass", "db_instance_class", default=inventory.get("db_instance_class")),
            "storage_type": _metadata_value(metadata, "StorageType", "storage_type"),
            "allocated_storage_gib": _metadata_value(metadata, "AllocatedStorage", "allocated_storage"),
            "iops": _metadata_value(metadata, "Iops", "iops"),
            "storage_throughput_mibps": _metadata_value(metadata, "StorageThroughput", "storage_throughput"),
        },
        "policy": {**asdict(policy), "monthly_hours": str(policy.monthly_hours), "min_monthly_savings": str(policy.min_monthly_savings)},
        "pricing_scope": {
            "currency": "USD",
            "monthly_hours": str(policy.monthly_hours),
            "modeled": ["on_demand_instance", "allocated_storage", "provisioned_iops", "provisioned_throughput"],
            "excluded": ["reserved_instances", "enterprise_discounts", "backup", "snapshot", "data_transfer", "performance_insights", "byol"],
            "catalog_version": rightsizing_context.get("pricing_catalog_version") or "rds-pricing-v1",
            "source": rightsizing_context.get("pricing_source") or "PACKAGED_CATALOG",
            "source_versions": rightsizing_context.get("pricing_source_versions") or [],
            "collection_status": rightsizing_context.get("pricing_status"),
        },
        "availability_note": AVAILABILITY_NOTE,
        "operational_note": " ".join([CLASS_OUTAGE_NOTE, MULTI_AZ_NOTE if multi_az else "", STORAGE_NOTE]).strip(),
    }
    if scope_reason:
        return {
            **base,
            "classification": "DEFERRED",
            "evaluation_status": {"instance_class": "DEFERRED", "storage_configuration": "DEFERRED"},
            "evaluation_reason_codes": {"instance_class": [scope_reason], "storage_configuration": [scope_reason]},
            "deferred_reason_codes": [scope_reason],
            "recommendations": [],
            "telemetry_summary": {},
            "database_load_attribution": {"status": "NOT_NEEDED", "required": False, "observed_days": None, "total_load": None, "cpu_load": None, "non_cpu_load": None, "unattributed_load": None, "wait_type_shares": [], "enablement_prompt": None},
            "rejection_summary": {},
        }
    rightsizing = metadata.get("rightsizing_metrics") if isinstance(metadata.get("rightsizing_metrics"), dict) else {}
    metrics = rightsizing.get("rds_v1") if isinstance(rightsizing.get("rds_v1"), dict) else None
    if metrics is None:
        return {
            **base,
            "classification": "INSUFFICIENT_DATA",
            "evaluation_status": {"instance_class": "INSUFFICIENT_DATA", "storage_configuration": "INSUFFICIENT_DATA"},
            "evaluation_reason_codes": {"instance_class": ["RDS_RIGHTSIZING_SCAN_REQUIRED"], "storage_configuration": ["RDS_RIGHTSIZING_SCAN_REQUIRED"]},
            "recommendations": [],
            "telemetry_summary": {},
            "database_load_attribution": {"status": "NOT_NEEDED", "required": False, "observed_days": None, "total_load": None, "cpu_load": None, "non_cpu_load": None, "unattributed_load": None, "wait_type_shares": [], "enablement_prompt": None},
            "rejection_summary": {},
        }
    class_rec, class_status, class_reasons, rejected = _evaluate_class(
        inventory, metadata, metrics, catalog, price_lookup, policy, min_monthly_savings, candidate_limit
    )
    storage_rec, storage_status, storage_reasons = _evaluate_storage(
        inventory, metadata, metrics, policy, min_monthly_savings
    )
    recommendations = [item for item in (class_rec, storage_rec) if item]

    def headline() -> dict[str, Any] | None:
        if class_rec:
            default = class_rec["tiers"].get(class_rec["tiers"].get("default")) if class_rec["tiers"].get("default") else None
            if default:
                return default
            if class_rec["candidates"]:
                return class_rec["candidates"][0]
        return storage_rec

    selected = headline()
    classification = selected.get("classification") if selected else "INSUFFICIENT_DATA" if "INSUFFICIENT_DATA" in {class_status, storage_status} else None
    pi = metrics.get("performance_insights") if isinstance(metrics.get("performance_insights"), dict) else {}
    required = bool(pi.get("required"))
    enablement_prompt = None
    if required and pi.get("status") == "DISABLED":
        enablement_prompt = {
            "title": "Enable Database Insights for stronger confidence",
            "message": "The standard metrics are close or contradictory, so database-load attribution would improve this recommendation. Enable Database Insights Standard with Performance Insights, keep seven-day retention, and rescan. Enabling it does not require a reboot, failover, or database outage. MaxOps will not enable it automatically.",
            "documentation_url": "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_PerfInsights.Enabling.html",
            "causes_downtime": False,
            "recommended_mode": "standard",
            "sufficient_retention_days": 7,
        }
    return {
        **base,
        "classification": classification,
        "evaluation_status": {"instance_class": class_status, "storage_configuration": storage_status},
        "evaluation_reason_codes": {"instance_class": class_reasons, "storage_configuration": storage_reasons},
        "recommendations": recommendations,
        "telemetry_summary": {
            "generated_at": metrics.get("generated_at"),
            "period_seconds": metrics.get("period_seconds"),
            "cpu": _metric(metrics, "cpu_percent"),
            "freeable_memory": _metric(metrics, "freeable_memory_bytes"),
            "storage_iops": _metric(metrics, "total_iops"),
            "storage_throughput": _metric(metrics, "total_throughput_bps"),
        },
        "database_load_attribution": {
            "status": pi.get("status") or "NOT_NEEDED",
            "required": required,
            "observed_days": pi.get("observed_days"),
            "total_load": (pi.get("summaries") or {}).get("total_load") if isinstance(pi.get("summaries"), dict) else None,
            "cpu_load": (pi.get("summaries") or {}).get("cpu_load") if isinstance(pi.get("summaries"), dict) else None,
            "non_cpu_load": (pi.get("summaries") or {}).get("non_cpu_load") if isinstance(pi.get("summaries"), dict) else None,
            "unattributed_load": (pi.get("summaries") or {}).get("unattributed_load") if isinstance(pi.get("summaries"), dict) else None,
            "wait_type_shares": pi.get("wait_type_summary") or [],
            "enablement_prompt": enablement_prompt,
        },
        "rejection_summary": rejected,
    }
