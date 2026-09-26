"""Read-only ElastiCache rightsizing recommendations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.adapters.aws.adapter import AWSAdapter
from app.models.inventory import ElasticacheInventory
from app.services.ec2_trend_cache import ec2_trend_cache
from app.services.elasticache_node_catalog import (
    ElastiCacheCatalogEntry,
    ElastiCacheNodeCatalog,
    engine_supports_node_type,
)
from app.utils.elasticache import is_elasticache_member_cluster
from rightsizers.ec2.ec2_rightsizer.constraints import NetworkConstraintInput
from rightsizers.ec2.ec2_rightsizer.evaluation import evaluate_network
from rightsizers.ec2.ec2_rightsizer.models import PerformanceWarningPolicy
from rightsizers.ec2.ec2_rightsizer.network_baseline import (
    effective_network_baseline,
)
from rightsizers.ec2.ec2_rightsizer.warnings import NetworkWarningInput
from rightsizers.elasticache.elasticache_rightsizer.models import (
    ElastiCachePerformanceWarningPolicy,
)
from rightsizers.elasticache.elasticache_rightsizer.requirements import (
    projected_engine_cpu,
    projected_host_cpu,
    projected_memory_util,
    usable_memory_bytes,
)


AVAILABILITY_NOTE = (
    "Recommendations are advisory. Validate availability, maintenance windows, "
    "and failover posture before changing a replication group."
)
OPERATIONAL_NOTE = (
    "ElastiCache vertical scaling is performed online where AWS supports it, "
    "but can involve node replacement, connection interruption, and failover."
)
REDUNDANCY_DISCLOSURE = (
    "Reducing replicas reduces read redundancy and failover headroom. The "
    "recommendation preserves the minimum safety floor, not the current posture."
)


@dataclass(frozen=True)
class ElastiCacheComputePolicy:
    conservative_ratio: float = 0.55
    balanced_ratio: float = 0.70
    aggressive_ratio: float = 0.85
    engine_cpu_target_ratio: float = 0.70
    host_cpu_target_ratio: float = 0.70
    memory_target_ratio: float = 0.70
    small_target_cpu_multiplier: float = 0.85
    reserved_memory_percent_default: float = 0.25


@dataclass(frozen=True)
class ElastiCacheScopePolicy:
    defer_global_datastore: bool = True
    defer_auto_scaling: bool = True
    require_available: bool = True


@dataclass(frozen=True)
class ElastiCacheCandidatePolicy:
    gated_family_classes: tuple[str, ...] = ("t",)
    gated_data_tiering: bool = True


def _float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() in {"true", "enabled", "available", "1"}:
            return True
        if value.lower() in {"false", "disabled", "0"}:
            return False
    return None


def _first(metadata: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in metadata and metadata[name] is not None:
            return metadata[name]
    return None


def _metric(metadata: dict[str, Any], name: str, field: str = "p99") -> float | None:
    root = metadata.get("rightsizing_metrics")
    if not isinstance(root, dict):
        root = metadata
    value = root.get(name)
    if isinstance(value, dict):
        return _float(
            value.get(field)
            if field in value
            else value.get("maximum")
            if field == "max"
            else None
        )
    return _float(value)


def _risk(overall: str, reasons: Iterable[str], **overrides: str) -> dict[str, Any]:
    result = {
        "telemetry": "LOW",
        "compute": "LOW",
        "memory": "LOW",
        "network": "LOW",
        "cache_health": "LOW",
        "compatibility": "LOW",
        "migration": "MEDIUM",
        "overall": overall,
        "reason_codes": list(dict.fromkeys(reasons)),
    }
    result.update(overrides)
    return result


def _node_count(metadata: dict[str, Any]) -> int:
    members = _first(metadata, "member_cluster_ids", "MemberClusters")
    if isinstance(members, list) and members:
        return len(members)
    return int(_float(_first(metadata, "num_cache_nodes", "NumCacheNodes")) or 1)


def _replica_layout(metadata: dict[str, Any]) -> list[int]:
    value = _first(metadata, "replicas_per_node_group", "ReplicasPerNodeGroup")
    if isinstance(value, list):
        return [int(_float(item) or 0) for item in value]
    groups = int(_float(_first(metadata, "num_node_groups", "NumNodeGroups")) or 1)
    return [int(_float(value) or 0)] * groups


def _tier_option(candidate: dict[str, Any], tier: str) -> dict[str, Any]:
    return {
        "tier": tier,
        "target_node_type": candidate["target_node_type"],
        "monthly_savings": candidate["monthly_savings"],
        "yearly_savings": candidate["yearly_savings"],
        "projected_util": candidate["projected_util"],
        "binding_dimension": candidate["binding_dimension"],
        "classification": candidate["classification"],
        "risk_assessment": candidate["risk_assessment"],
        "recommendation_rank": candidate.get("rank"),
    }


def _tiers(
    candidates: list[dict[str, Any]], policy: ElastiCacheComputePolicy
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "default": None,
        "conservative": None,
        "balanced": None,
        "aggressive": None,
    }
    for name, ratio in (
        ("conservative", policy.conservative_ratio),
        ("balanced", policy.balanced_ratio),
        ("aggressive", policy.aggressive_ratio),
    ):
        eligible = [item for item in candidates if item["projected_util"] <= ratio]
        if eligible:
            chosen = min(
                eligible,
                key=lambda item: (
                    item["target_monthly_cost"],
                    item["target_node_type"],
                ),
            )
            result[name] = _tier_option(chosen, name)
    result["default"] = (
        "balanced"
        if result["balanced"]
        else "conservative"
        if result["conservative"]
        else "aggressive"
        if result["aggressive"]
        else None
    )
    return result


class ElastiCacheRightsizer:
    TREND_SCHEMA_VERSION = "elasticache-trend-v1"

    def __init__(
        self,
        db: Session,
        catalog: ElastiCacheNodeCatalog | None = None,
        warning_policy: ElastiCachePerformanceWarningPolicy | None = None,
        compute_policy: ElastiCacheComputePolicy | None = None,
        scope_policy: ElastiCacheScopePolicy | None = None,
        candidate_policy: ElastiCacheCandidatePolicy | None = None,
    ) -> None:
        self.db = db
        self.catalog = catalog or ElastiCacheNodeCatalog()
        self.warning_policy = warning_policy or ElastiCachePerformanceWarningPolicy()
        self.compute_policy = compute_policy or ElastiCacheComputePolicy()
        self.scope_policy = scope_policy or ElastiCacheScopePolicy()
        self.candidate_policy = candidate_policy or ElastiCacheCandidatePolicy()

    def list_recommendations(
        self,
        *,
        account_id: str | None = None,
        region: str | None = None,
        state: str = "available",
        min_monthly_savings: float = 0.0,
        candidate_limit: int = 10,
    ) -> list[dict[str, Any]]:
        query = self.db.query(ElasticacheInventory)
        if account_id:
            query = query.filter(ElasticacheInventory.account_id == account_id)
        if region:
            query = query.filter(ElasticacheInventory.region == region)
        if state:
            query = query.filter(ElasticacheInventory.state == state)
        return [
            self._recommend(row, min_monthly_savings, candidate_limit)
            for row in query.order_by(ElasticacheInventory.inventory_id).all()
        ]

    def get_recommendation(
        self,
        inventory_id: int,
        *,
        min_monthly_savings: float = 0.0,
        candidate_limit: int = 10,
    ) -> dict[str, Any] | None:
        row = (
            self.db.query(ElasticacheInventory)
            .filter(ElasticacheInventory.inventory_id == inventory_id)
            .first()
        )
        return (
            self._recommend(row, min_monthly_savings, candidate_limit) if row else None
        )

    def _scope_reason(
        self, row: ElasticacheInventory, metadata: dict[str, Any]
    ) -> str | None:
        engine = str(row.engine or _first(metadata, "engine", "Engine") or "").lower()
        if engine not in {"redis", "valkey"}:
            return "UNSUPPORTED_ENGINE"
        if not (
            row.cache_node_type or _first(metadata, "cache_node_type", "CacheNodeType")
        ):
            return "SERVERLESS_CACHE"
        if is_elasticache_member_cluster(
            {"resource_type": row.resource_type, "metadata": metadata}
        ):
            return "MEMBER_OF_REPLICATION_GROUP"
        if (
            _bool(_first(metadata, "global_datastore_member", "GlobalDatastoreMember"))
            is True
        ):
            return "MANAGED_BY_GLOBAL_DATASTORE"
        if (
            _bool(_first(metadata, "auto_scaling_attached", "AutoScalingAttached"))
            is True
        ):
            return "MANAGED_BY_AUTO_SCALING"
        if (
            self.scope_policy.require_available
            and str(row.state or _first(metadata, "status", "Status") or "").lower()
            != "available"
        ):
            return "RESOURCE_NOT_AVAILABLE"
        return None

    def _base(
        self, row: ElasticacheInventory, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        layout = _replica_layout(metadata)
        groups = int(
            _float(_first(metadata, "num_node_groups", "NumNodeGroups"))
            or len(layout)
            or 1
        )
        return {
            "inventory_id": row.inventory_id,
            "resource_id": row.resource_id,
            "resource_name": row.resource_name,
            "account_id": row.account_id,
            "region": row.region,
            "state": row.state,
            "replication_group_id": row.resource_id
            if "group" in str(row.resource_type).lower()
            or _first(metadata, "MemberClusters", "member_cluster_ids")
            else None,
            "engine": str(
                row.engine or _first(metadata, "engine", "Engine") or ""
            ).lower(),
            "engine_version": row.engine_version
            or _first(metadata, "engine_version", "EngineVersion"),
            "current_node_type": row.cache_node_type
            or _first(metadata, "cache_node_type", "CacheNodeType"),
            "node_count": _node_count(metadata),
            "cluster_mode_enabled": groups > 1,
            "num_node_groups": groups,
            "policy": asdict(self.warning_policy),
            "compute_policy": asdict(self.compute_policy),
            "candidate_policy": asdict(self.candidate_policy),
            "scope_policy": asdict(self.scope_policy),
            "availability_note": AVAILABILITY_NOTE,
            "availability_validated": False,
            "operational_note": OPERATIONAL_NOTE,
            "telemetry_summary": metadata.get("telemetry_summary")
            or {"windows_days": [14, 30, 60]},
        }

    def _deferred(
        self, row: ElasticacheInventory, metadata: dict[str, Any], reason: str
    ) -> dict[str, Any]:
        return {
            **self._base(row, metadata),
            "classification": "DEFERRED",
            "deferred_reason_codes": [reason],
            "current_monthly_cost": round(float(row.monthly_cost_estimate or 0), 2),
            "pricing_source": "street_pricing_sqlite",
            "current_capacity_evidence": {},
            "recommendations": [],
            "tiers": _tiers([], self.compute_policy),
            "rejection_summary": {},
            "capability_catalog": {},
            "replica_recommendation": None,
            "replica_deferred_reason_codes": [],
        }

    def _recommend(
        self, row: ElasticacheInventory, min_savings: float, limit: int
    ) -> dict[str, Any]:
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        if reason := self._scope_reason(row, metadata):
            return self._deferred(row, metadata, reason)
        base = self._base(row, metadata)
        engine = base["engine"]
        entries = self.catalog.list_region(row.region or "", engine)
        current = entries.get(base["current_node_type"])
        if current is None:
            return self._deferred(row, metadata, "CURRENT_NODE_TYPE_NOT_IN_CATALOG")
        node_count = base["node_count"]
        current_monthly = current.monthly_usd * node_count
        rejected: dict[str, int] = {}
        candidates: list[dict[str, Any]] = []
        allowed_up = _first(metadata, "allowed_scale_up_types", "AllowedScaleUpTypes")
        allowed_down = _first(
            metadata, "allowed_scale_down_types", "AllowedScaleDownTypes"
        )
        allowed = (
            set(allowed_up or []) | set(allowed_down or [])
            if isinstance(allowed_up, list) or isinstance(allowed_down, list)
            else None
        )
        auto_scaling_unknown = (
            _first(metadata, "auto_scaling_attached", "AutoScalingAttached") is None
        )
        family_max_vcpus: dict[str, float] = {}
        for entry in entries.values():
            if entry.vcpus is not None and not entry.node_type.lower().endswith(
                ".metal"
            ):
                family_max_vcpus[entry.family] = max(
                    family_max_vcpus.get(entry.family, 0.0), float(entry.vcpus)
                )
        for target in entries.values():
            if (
                target.node_type == current.node_type
                or target.monthly_usd >= current.monthly_usd
            ):
                continue
            savings = (current.monthly_usd - target.monthly_usd) * node_count
            if round(savings, 2) < round(min_savings, 2):
                continue
            if allowed is not None and target.node_type not in allowed:
                rejected["ENGINE_VERSION_INCOMPATIBLE"] = (
                    rejected.get("ENGINE_VERSION_INCOMPATIBLE", 0) + 1
                )
                continue
            if allowed is None:
                support = engine_supports_node_type(
                    engine, str(base["engine_version"] or ""), target.node_type
                )
                if support is False:
                    rejected["ENGINE_VERSION_INCOMPATIBLE"] = (
                        rejected.get("ENGINE_VERSION_INCOMPATIBLE", 0) + 1
                    )
                    continue
                if support is None:
                    rejected["ENGINE_VERSION_SUPPORT_UNKNOWN"] = (
                        rejected.get("ENGINE_VERSION_SUPPORT_UNKNOWN", 0) + 1
                    )
                    continue
            if (
                target.family_class in self.candidate_policy.gated_family_classes
                and target.family_class != current.family_class
            ):
                rejected["BURSTABLE_FAMILY_REQUIRES_REVIEW"] = (
                    rejected.get("BURSTABLE_FAMILY_REQUIRES_REVIEW", 0) + 1
                )
                continue
            if (
                self.candidate_policy.gated_data_tiering
                and target.data_tiering != current.data_tiering
            ):
                rejected["DATA_TIERING_CHANGE_REQUIRES_REVIEW"] = (
                    rejected.get("DATA_TIERING_CHANGE_REQUIRES_REVIEW", 0) + 1
                )
                continue
            candidate = self._evaluate(
                row,
                metadata,
                current,
                target,
                node_count,
                savings,
                auto_scaling_unknown,
                family_max_vcpus,
            )
            if candidate["classification"] == "REJECTED":
                for code in candidate["reason_codes"]:
                    rejected[code] = rejected.get(code, 0) + 1
            else:
                candidates.append(candidate)
        candidates.sort(
            key=lambda item: (-item["monthly_savings"], item["target_node_type"])
        )
        preliminary_tiers = _tiers(candidates, self.compute_policy)
        selected = candidates[:limit]
        balanced = preliminary_tiers.get("balanced")
        if balanced and not any(
            item["target_node_type"] == balanced["target_node_type"]
            for item in selected
        ):
            balanced_candidate = next(
                item
                for item in candidates
                if item["target_node_type"] == balanced["target_node_type"]
            )
            selected = (
                (selected[: max(limit - 1, 0)] + [balanced_candidate]) if limit else []
            )
        for rank, item in enumerate(selected, 1):
            item["rank"] = rank
        tiers = _tiers(selected, self.compute_policy)
        replica, replica_deferred_reasons = self._replica_recommendation(
            metadata, current
        )
        classifications = [item["classification"] for item in selected]
        if replica:
            classifications.append(replica["classification"])
        response_classification = (
            "ACTIONABLE"
            if "ACTIONABLE" in classifications
            else "CONDITIONAL"
            if "CONDITIONAL" in classifications
            else "NO_CANDIDATE"
        )
        return {
            **base,
            "classification": response_classification,
            "deferred_reason_codes": [],
            "current_monthly_cost": round(current_monthly, 2),
            "pricing_source": "street_pricing_sqlite",
            "current_capacity_evidence": {
                "node_type": current.node_type,
                "vcpus": current.vcpus,
                "maxmemory_bytes": current.maxmemory_bytes,
                "maxmemory_source": current.maxmemory_source,
                "network_baseline_mbps": current.network_baseline_mbps,
            },
            "recommendations": selected,
            "tiers": tiers,
            "rejection_summary": rejected,
            "capability_catalog": {
                "priced_node_types": len(entries),
                "current_maxmemory_source": current.maxmemory_source,
                "current_network_capacity_kind": current.network_capacity_kind,
            },
            "replica_recommendation": replica,
            "replica_deferred_reason_codes": replica_deferred_reasons,
        }

    def _evaluate(
        self,
        row: ElasticacheInventory,
        metadata: dict[str, Any],
        current: ElastiCacheCatalogEntry,
        target: ElastiCacheCatalogEntry,
        node_count: int,
        savings: float,
        auto_scaling_unknown: bool,
        family_max_vcpus: dict[str, float],
    ) -> dict[str, Any]:
        engine_cpu = _metric(metadata, "engine_cpu_percent")
        if engine_cpu is None:
            engine_cpu = _metric(metadata, "engine_cpu")
        host_cpu = _metric(metadata, "host_cpu_percent")
        if host_cpu is None:
            host_cpu = _metric(metadata, "cpu_percent")
        memory_percent = _metric(metadata, "memory_percent")
        if memory_percent is None:
            memory_percent = _metric(metadata, "database_memory_usage_percent")
        bytes_used = _metric(metadata, "bytes_used_for_cache")
        reserved_percent = _float(
            _first(metadata, "reserved_memory_percent", "ReservedMemoryPercent")
        )
        reserved_bytes = _float(
            _first(metadata, "reserved_memory_bytes", "ReservedMemoryBytes")
        )
        reservation_unknown = reserved_percent is None and reserved_bytes is None
        effective_reserved = (
            reserved_percent
            if reserved_percent is not None
            else self.compute_policy.reserved_memory_percent_default
        )
        current_usable = usable_memory_bytes(
            current, effective_reserved, reserved_bytes
        )
        target_usable = usable_memory_bytes(target, effective_reserved, reserved_bytes)
        if (
            bytes_used is None
            and memory_percent is not None
            and current_usable is not None
        ):
            bytes_used = current_usable * memory_percent / 100.0
        projected_memory = projected_memory_util(bytes_used, target_usable)
        projected_engine = projected_engine_cpu(engine_cpu)
        projected_host = projected_host_cpu(host_cpu, current.vcpus, target.vcpus)
        network_policy = PerformanceWarningPolicy(
            network_medium_ratio=self.warning_policy.network_medium_ratio,
            network_high_ratio=self.warning_policy.network_high_ratio,
            network_assumed_baseline_enabled=self.warning_policy.network_assumed_baseline_enabled,
            network_assumed_baseline_multiplier=self.warning_policy.network_assumed_baseline_multiplier,
            network_assumed_baseline_floor_mbps=self.warning_policy.network_assumed_baseline_floor_mbps,
            policy_version=self.warning_policy.policy_version,
        )
        current_network_baseline, _, _ = effective_network_baseline(
            published_baseline_mbps=current.network_baseline_mbps,
            reliable_max_mbps=current.network_reliable_max_mbps,
            vcpus=current.vcpus,
            family_max_vcpus=family_max_vcpus.get(current.family),
            capacity_kind=current.network_capacity_kind,
            policy=network_policy,
        )
        (
            target_network_baseline,
            target_kind,
            baseline_is_assumed,
        ) = effective_network_baseline(
            published_baseline_mbps=target.network_baseline_mbps,
            reliable_max_mbps=target.network_reliable_max_mbps,
            vcpus=target.vcpus,
            family_max_vcpus=family_max_vcpus.get(target.family),
            capacity_kind=target.network_capacity_kind,
            policy=network_policy,
        )
        ratios = {
            "cpu": max(
                value
                for value in (
                    (projected_engine or 0) / 100.0,
                    (projected_host or 0) / 100.0,
                )
            ),
            "memory": (projected_memory or 0) / 100.0,
        }
        network_in = _metric(metadata, "network_in_mbps")
        network_out = _metric(metadata, "network_out_mbps")
        network_peak = (
            max(value for value in (network_in, network_out) if value is not None)
            if network_in is not None or network_out is not None
            else None
        )
        ratios["network"] = (
            network_peak / target_network_baseline
            if network_peak is not None and target_network_baseline
            else 0.0
        )
        binding = max(ratios, key=ratios.get)
        projected_util = max(ratios.values())
        reasons: list[str] = []
        warnings: list[str] = []
        hard_fail = False
        memory_reducing = (target_usable or 0) < (current_usable or 0)
        cpu_reducing = (target.vcpus or 0) < (current.vcpus or 0)
        network_capacity_retained = (
            current_network_baseline is None
            or (
                target_network_baseline is not None
                and target_network_baseline >= current_network_baseline
            )
        ) and (
            current.network_reliable_max_mbps is None
            or (
                target.network_reliable_max_mbps is not None
                and target.network_reliable_max_mbps
                >= current.network_reliable_max_mbps
            )
        )
        if engine_cpu is None and host_cpu is None and cpu_reducing:
            reasons.append("CPU_TELEMETRY_MISSING_CAPACITY_RETAINED")
            hard_fail = True
        if bytes_used is None and memory_percent is None and memory_reducing:
            reasons.append("MEMORY_TELEMETRY_MISSING_CAPACITY_RETAINED")
            hard_fail = True
        if (
            network_in is None or network_out is None
        ) and not network_capacity_retained:
            reasons.append("NETWORK_TELEMETRY_MISSING_CAPACITY_RETAINED")
            hard_fail = True
        evictions = _metric(metadata, "evictions", "max") or 0
        traffic = _metric(metadata, "traffic_management_active", "max") or 0
        aggressive_cpu_limit = self.compute_policy.aggressive_ratio * (
            self.compute_policy.small_target_cpu_multiplier
            if target.vcpus is not None and target.vcpus <= 2
            else 1.0
        )
        if max(ratios["cpu"], 0) > aggressive_cpu_limit:
            reasons.append("CPU_REQUIREMENT_NOT_MET")
            hard_fail = True
        if ratios["memory"] > self.compute_policy.aggressive_ratio:
            reasons.append("MEMORY_REQUIREMENT_NOT_MET")
            hard_fail = True
        if projected_memory is not None and projected_memory >= 100:
            reasons.append("MEMORY_REQUIREMENT_NOT_MET")
            hard_fail = True
        if evictions > 0 and memory_reducing:
            reasons.append("MEMORY_PRESSURE_EVICTIONS_PRESENT")
            hard_fail = True
        elif evictions > 0:
            warnings.append("EVICTIONS_PRESENT")
        if traffic > 0 and (memory_reducing or cpu_reducing):
            reasons.append("TRAFFIC_MANAGEMENT_ACTIVE")
            hard_fail = True
        elif traffic > 0:
            warnings.append("TRAFFIC_MANAGEMENT_DETECTED")
        allowance_names = (
            "network_bw_in_allowance_exceeded",
            "network_bw_out_allowance_exceeded",
            "network_packets_per_second_allowance_exceeded",
            "network_conntrack_allowance_exceeded",
        )
        allowance_values = {
            name: _metric(metadata, name, "max") for name in allowance_names
        }
        allowance_collected = all(
            value is not None for value in allowance_values.values()
        )
        network = evaluate_network(
            NetworkConstraintInput(
                observed_in_p99_mbps=network_in,
                observed_out_p99_mbps=network_out,
                target_reliable_max_mbps=target.network_reliable_max_mbps,
                allowance_exceeded=any(
                    (value or 0) > 0 for value in allowance_values.values()
                ),
                target_capacity_relation=(
                    "LOWER"
                    if current_network_baseline is not None
                    and target_network_baseline is not None
                    and target_network_baseline < current_network_baseline
                    else "GREATER"
                    if current_network_baseline is not None
                    and target_network_baseline is not None
                    and target_network_baseline > current_network_baseline
                    else "EQUAL"
                    if current_network_baseline == target_network_baseline
                    and current_network_baseline is not None
                    else "UNKNOWN"
                ),
            ),
            NetworkWarningInput(
                observed_in_p99_mbps=network_in,
                observed_out_p99_mbps=network_out,
                target_baseline_mbps=target_network_baseline,
                capacity_kind=target_kind,
                bandwidth_allowance_events=any(
                    (allowance_values[name] or 0) > 0 for name in allowance_names[:2]
                ),
                pps_allowance_events=(allowance_values[allowance_names[2]] or 0) > 0,
                conntrack_allowance_events=(allowance_values[allowance_names[3]] or 0)
                > 0,
                allowance_metrics_collected=allowance_collected,
                target_peak_mbps=target.network_reliable_max_mbps,
                baseline_is_assumed=baseline_is_assumed,
            ),
            network_policy,
        )
        if network.hard_constraint_status.value == "FAIL":
            hard_fail = True
        reasons.extend(
            code
            for code in network.warnings
            if code
            in {
                "NETWORK_RELIABLE_MAX_EXCEEDED",
                "NETWORK_ALLOWANCE_EVENTS_WITHOUT_CAPACITY_INCREASE",
            }
        )
        warnings.extend(
            code
            for code in network.warnings
            if code not in reasons and code != "NETWORK_ALLOWANCE_METRICS_MISSING"
        )
        conditional = auto_scaling_unknown
        if auto_scaling_unknown:
            warnings.append("AUTO_SCALING_STATE_UNKNOWN")
        if target_network_baseline is None:
            conditional = True
        if reservation_unknown:
            if memory_reducing:
                reasons.append("RESERVED_MEMORY_UNKNOWN")
                hard_fail = True
            else:
                warnings.append("RESERVED_MEMORY_UNKNOWN")
                conditional = True
        if engine_cpu is None:
            warnings.append("ENGINE_CPU_METRIC_UNAVAILABLE")
            conditional = True
        if projected_memory is not None:
            memory_ratio = projected_memory / 100.0
            if memory_ratio > self.warning_policy.memory_high_ratio:
                warnings.append("HIGH_MEMORY_USAGE_REVIEW_REQUIRED")
                conditional = True
            elif memory_ratio >= self.warning_policy.memory_medium_ratio:
                warnings.append("MEMORY_USAGE_REVIEW_REQUIRED")
                conditional = True
        connections = _metric(metadata, "curr_connections")
        if (
            connections is not None
            and target.max_clients
            and connections / target.max_clients
            >= self.warning_policy.connection_warning_ratio
        ):
            warnings.append("HIGH_CONNECTION_COUNT_REVIEW_REQUIRED")
            conditional = True
        if current.burstable:
            credit_balance = _metric(metadata, "cpu_credit_balance")
            if credit_balance is None:
                warnings.append("CPU_CREDIT_TELEMETRY_UNAVAILABLE")
                if target.burstable:
                    conditional = True
        health_thresholds = (
            (
                "swap_usage_bytes",
                self.warning_policy.swap_warning_bytes,
                "SWAP_USAGE_REVIEW_REQUIRED",
            ),
            (
                "replication_lag_seconds",
                self.warning_policy.replication_lag_warning_seconds,
                "REPLICATION_LAG_REVIEW_REQUIRED",
            ),
        )
        for metric, threshold, code in health_thresholds:
            if (_metric(metadata, metric, "max") or 0) > threshold:
                warnings.append(code)
                conditional = True
        classification = (
            "REJECTED"
            if hard_fail
            else "CONDITIONAL"
            if conditional or warnings
            else "ACTIONABLE"
        )
        all_reasons = list(dict.fromkeys(reasons + warnings))
        overall = "HIGH" if hard_fail or conditional or warnings else "LOW"
        return {
            "kind": "NODE_TYPE_CHANGE",
            "target_node_type": target.node_type,
            "target_monthly_cost": round(target.monthly_usd * node_count, 2),
            "monthly_savings": round(savings, 2),
            "yearly_savings": round(savings * 12, 2),
            "projected_engine_cpu": projected_engine,
            "projected_host_cpu": projected_host,
            "projected_memory_util": projected_memory,
            "projected_util": projected_util,
            "binding_dimension": binding,
            "classification": classification,
            "risk_assessment": _risk(
                overall,
                all_reasons,
                memory="HIGH"
                if any("MEMORY" in code or "EVICTION" in code for code in all_reasons)
                else "LOW",
                network="HIGH"
                if any("NETWORK" in code for code in all_reasons)
                else "LOW",
                cache_health="HIGH" if warnings else "LOW",
            ),
            "reason_codes": all_reasons,
            "warning_details": [
                {"code": code, "message": code.replace("_", " ").title()}
                for code in warnings
            ],
            "compute_evidence": {
                "engine_cpu_p99": engine_cpu,
                "host_cpu_p99": host_cpu,
                "current_vcpus": current.vcpus,
                "target_vcpus": target.vcpus,
            },
            "network_evaluation": asdict(network),
            "memory_evaluation": {
                "bytes_used_p99": bytes_used,
                "target_usable_bytes": target_usable,
                "reserved_memory_percent": reserved_percent,
                "reserved_memory_bytes": reserved_bytes,
            },
            "cache_health": {
                "evictions": evictions,
                "traffic_management_active": traffic,
            },
            "constraint_coverage": {
                "numeric_network_baseline": target_network_baseline is not None,
                "network_baseline_assumed": baseline_is_assumed,
                "assumed_network_baseline_mbps": (
                    target_network_baseline if baseline_is_assumed else None
                ),
                "reliable_network_maximum": target.network_reliable_max_mbps
                is not None,
                "maxmemory_source": target.maxmemory_source,
            },
            "required_review": classification == "CONDITIONAL",
        }

    def _replica_recommendation(
        self, metadata: dict[str, Any], current: ElastiCacheCatalogEntry
    ) -> tuple[dict[str, Any] | None, list[str]]:
        layout = _replica_layout(metadata)
        if not layout or any(value != layout[0] for value in layout):
            return None, []
        current_replicas = layout[0]
        if current_replicas <= 1:
            return None, []
        per_node = (metadata.get("rightsizing_metrics") or {}).get("per_node")
        if not isinstance(per_node, dict) or not per_node:
            return None, ["REPLICA_EVIDENCE_INCOMPLETE"]
        members = []
        for cache_cluster_id, raw in per_node.items():
            if not isinstance(raw, dict):
                continue
            members.append({"cache_cluster_id": cache_cluster_id, **raw})
        roles = {str(item.get("role") or "unknown") for item in members}
        if "unknown" in roles or not {"primary", "replica"}.issubset(roles):
            return None, ["REPLICA_ROLES_UNRESOLVED"]
        required_evidence = (
            "engine_cpu_p99",
            "read_ops_p99",
            "read_ops_max",
            "read_ops_sum",
            "read_coverage_ratio",
            "read_observed_days",
            "read_latest_sample_age_seconds",
        )
        if (
            any(
                any(item.get(field) is None for field in required_evidence)
                for item in members
            )
            or _metric(metadata, "replication_lag_seconds", "max") is None
        ):
            return None, ["REPLICA_EVIDENCE_INCOMPLETE"]
        shards: dict[str, list[dict[str, Any]]] = {}
        for item in members:
            shards.setdefault(str(item.get("node_group_id") or "0001"), []).append(item)
        removed: list[dict[str, Any]] = []
        target = current_replicas
        projected_survivor = max(
            _float(item.get("engine_cpu_p99")) or 0 for item in members
        )
        modeled_members = [
            item
            for item in members
            if (_float(item.get("read_ops_p99")) or 0)
            >= self.warning_policy.min_read_ops_for_cpu_rate
        ]
        cpu_per_read_op = (
            max(
                (_float(item.get("engine_cpu_p99")) or 0)
                / (_float(item.get("read_ops_p99")) or 1)
                for item in modeled_members
            )
            if modeled_members
            else None
        )
        projection_used = False
        for next_target in range(current_replicas - 1, 0, -1):
            proposed: list[dict[str, Any]] = []
            valid = True
            for shard_members in shards.values():
                replicas = sorted(
                    (item for item in shard_members if item.get("role") == "replica"),
                    key=lambda item: (
                        _float(item.get("read_ops_p99")) or 0,
                        str(item.get("cache_cluster_id")),
                    ),
                )
                count = current_replicas - next_target
                if len(replicas) < count:
                    valid = False
                    break
                proposed.extend(replicas[:count])
            if not valid:
                break
            idle = all(
                (_float(item.get("read_ops_p99")) or 0)
                <= self.warning_policy.idle_replica_read_ops_threshold
                for item in proposed
            )
            busiest_survivor = max(
                (
                    _float(item.get("engine_cpu_p99")) or 0
                    for item in members
                    if item not in proposed
                ),
                default=0,
            )
            projected = (
                busiest_survivor
                + sum(_float(item.get("read_ops_p99")) or 0 for item in proposed)
                * cpu_per_read_op
                if cpu_per_read_op is not None
                else busiest_survivor
            )
            projection_qualifies = cpu_per_read_op is not None and projected <= 70.0
            if idle or projection_qualifies:
                removed, target, projected_survivor = proposed, next_target, projected
                projection_used = not idle
        if not removed:
            return None, []
        positive_reads = any(
            (_float(item.get("read_ops_max")) or 0) > 0
            or (_float(item.get("read_ops_sum")) or 0) > 0
            for item in removed
        )
        coverage_failed = any(
            item.get("member_created_at") is None
            or (_float(item.get("read_coverage_ratio")) or 0)
            < self.warning_policy.replica_actionable_min_coverage_ratio
            or (_float(item.get("read_observed_days")) or 0)
            < self.warning_policy.replica_actionable_min_observed_days
            or (
                (age := _float(item.get("read_latest_sample_age_seconds"))) is None
                or age > self.warning_policy.replica_actionable_max_sample_age_seconds
            )
            for item in removed
        )
        reasons: list[str] = []
        if positive_reads:
            reasons.append("REPLICA_READ_TRAFFIC_OBSERVED")
        if coverage_failed:
            reasons.append("REPLICA_READ_COVERAGE_INSUFFICIENT")
        if projection_used:
            reasons.append("REPLICA_LOAD_PROJECTION_REQUIRES_REVIEW")
        health_codes = []
        if (
            _metric(metadata, "replication_lag_seconds", "max") or 0
        ) > self.warning_policy.replication_lag_warning_seconds:
            health_codes.append("REPLICATION_LAG_REVIEW_REQUIRED")
        if (_metric(metadata, "evictions", "max") or 0) > 0:
            health_codes.append("EVICTIONS_PRESENT")
        reasons.extend(health_codes)
        classification = "CONDITIONAL" if reasons else "ACTIONABLE"
        removed_ids = {item["cache_cluster_id"] for item in removed}
        evidence = []
        for item in members:
            evidence.append(
                {
                    "cache_cluster_id": item["cache_cluster_id"],
                    "node_group_id": item.get("node_group_id"),
                    "role": item.get("role"),
                    "removed": item["cache_cluster_id"] in removed_ids,
                    "member_created_at": item.get("member_created_at"),
                    "read_ops_p99": item.get("read_ops_p99"),
                    "read_ops_max": item.get("read_ops_max"),
                    "read_ops_sum": item.get("read_ops_sum"),
                    "read_coverage_ratio": item.get("read_coverage_ratio"),
                    "read_observed_days": item.get("read_observed_days"),
                    "read_latest_sample_age_seconds": item.get(
                        "read_latest_sample_age_seconds"
                    ),
                }
            )
        removed_count = len(removed)
        return {
            "kind": "REPLICA_COUNT_REDUCTION",
            "classification": classification,
            "current_replicas_per_node_group": current_replicas,
            "target_replicas_per_node_group": target,
            "affected_node_group_count": len(shards),
            "removed_node_count": removed_count,
            "projected_survivor_engine_cpu": projected_survivor,
            "monthly_savings": round(removed_count * current.monthly_usd, 2),
            "yearly_savings": round(removed_count * current.monthly_usd * 12, 2),
            "risk_assessment": _risk(
                "MEDIUM" if classification == "ACTIONABLE" else "HIGH",
                reasons,
                migration="MEDIUM",
            ),
            "reason_codes": reasons,
            "redundancy_disclosure": REDUNDANCY_DISCLOSURE,
            "member_evidence": evidence,
        }, []

    def get_confidence_trend(
        self,
        inventory_id: int,
        adapter: AWSAdapter | None = None,
        end_date: datetime | None = None,
    ) -> dict[str, Any] | None:
        row = (
            self.db.query(ElasticacheInventory)
            .filter(ElasticacheInventory.inventory_id == inventory_id)
            .first()
        )
        if row is None:
            return None
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        metrics = (
            metadata.get("rightsizing_metrics")
            if isinstance(metadata.get("rightsizing_metrics"), dict)
            else {}
        )
        per_node = (
            metrics.get("per_node") if isinstance(metrics.get("per_node"), dict) else {}
        )
        key = (
            "elasticache",
            row.account_id or "",
            row.region or "",
            row.resource_id,
            self.TREND_SCHEMA_VERSION,
            self.warning_policy.trend_primary_context_limit,
        )
        if cached := ec2_trend_cache.get(key):
            cached["cached"] = True
            return cached
        end = end_date or datetime.now(timezone.utc)
        series_by_metric: dict[str, list[dict[str, Any]]] = {}
        summaries: dict[str, dict[str, int]] = {}
        for response_name, score_name, reason in (
            ("engine_cpu", "engine_cpu_p99", "hottest_engine_cpu"),
            ("memory", "memory_p99", "hottest_memory"),
        ):
            nodes = [
                {"cache_cluster_id": node_id, **value}
                for node_id, value in per_node.items()
                if isinstance(value, dict)
            ]
            nodes.sort(
                key=lambda item: (
                    -(_float(item.get(score_name)) or -1),
                    str(item["cache_cluster_id"]),
                )
            )
            hottest = (
                nodes[0]
                if nodes
                else {
                    "cache_cluster_id": row.resource_id,
                    "node_group_id": "0001",
                    "role": "primary",
                }
            )
            primaries = sorted(
                (item for item in nodes if item.get("role") == "primary"),
                key=lambda item: (
                    -(_float(item.get(score_name)) or -1),
                    str(item["cache_cluster_id"]),
                ),
            )
            selected_primaries = primaries[
                : self.warning_policy.trend_primary_context_limit
            ]
            selected: list[dict[str, Any]] = [{**hottest, "selection_reason": reason}]
            seen = {hottest["cache_cluster_id"]}
            for item in selected_primaries:
                if item["cache_cluster_id"] not in seen:
                    selected.append({**item, "selection_reason": "primary"})
                    seen.add(item["cache_cluster_id"])
            primary_ids = {item["cache_cluster_id"] for item in primaries}
            returned_primary_ids = {
                item["cache_cluster_id"]
                for item in selected
                if item["cache_cluster_id"] in primary_ids
            }
            series_by_metric[response_name] = selected
            summaries[response_name] = {
                "primary_total": len(primaries),
                "primary_returned": len(returned_primary_ids),
                "primary_omitted": max(len(primaries) - len(returned_primary_ids), 0),
                "primary_context_limit": self.warning_policy.trend_primary_context_limit,
            }
        aws_adapter = adapter or AWSAdapter(default_region=row.region or None)
        trend = aws_adapter.get_elasticache_confidence_trend(
            series_by_metric, end - timedelta(days=455), end, region=row.region or None
        )
        generated_at = datetime.now(timezone.utc)
        response = {
            "inventory_id": row.inventory_id,
            "resource_id": row.resource_id,
            "metrics": trend,
            "selection_summary": summaries,
            "bucket_semantics": {
                "maximum": "trailing_window_from_daily_maximum",
                "percentiles": "latest_complete_epoch_aligned_window",
            },
            "generated_at": generated_at.isoformat(),
            "expires_at": (generated_at + timedelta(seconds=3600)).isoformat(),
            "cached": False,
        }
        ec2_trend_cache.set(key, response)
        return response
