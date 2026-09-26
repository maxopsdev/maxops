"""Read-only, inventory-backed Auto Scaling Group rightsizer."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
import math
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.aws.adapter import AWSAdapter
from app.config import settings
from app.models.inventory import AsgInventory
from app.services.asg_trend_cache import asg_trend_cache
from app.services.ec2_instance_catalog import (
    EC2CatalogEntry,
    EC2InstanceCatalog,
    instance_family,
)
from rightsizers.common.ec2_candidates import (
    CPUCapacityKind,
    EC2CandidatePolicy,
    architecture_overlaps,
    candidate_policy_evidence,
    comparable_cpu_capacity,
    coremark_catalog_coverage,
    exact_savings,
    family_target_allowed,
    limit_preserving_balanced,
    performance_evidence,
    raw_capacity_retention,
    savings_is_eligible,
)
from rightsizers.common.constants import TARGET_INDEPENDENT_DEMAND_SCHEMA
from rightsizers.common.statistics import (
    exact_ceil_division,
    nearest_rank_percentile_sorted,
)
from rightsizers.common.tiers import pick_tier_candidates
from rightsizers.ec2.ec2_rightsizer import AVAILABILITY_NOTE
from rightsizers.asg.asg_rightsizer.models import (
    ASGCapacityPolicy,
    ASGScopePolicy,
    asg_scope_reason,
)
from rightsizers.ec2.ec2_rightsizer.models import RecommendationClassification


logger = logging.getLogger("uvicorn.error")


class OptimizationKind:
    CAPACITY_ONLY = "CAPACITY_ONLY"
    INSTANCE_ONLY = "INSTANCE_ONLY"
    COMBINED = "COMBINED"


_EMPTY_TELEMETRY = {
    "cpu_percent": {
        "present": False,
        "observed_days": 0.0,
        "thin_data": True,
        "pairing_ratio": None,
    },
    "memory_percent": {
        "present": False,
        "observed_days": 0.0,
        "thin_data": True,
        "pairing_ratio": None,
        "source": None,
        "status": "unavailable",
    },
    "desired_capacity": {
        "present": False,
        "observed_days": 0.0,
        "thin_data": True,
    },
    "in_service_instances": {
        "present": False,
        "observed_days": 0.0,
        "thin_data": True,
    },
}


def _telemetry(metadata: dict[str, Any]) -> dict[str, Any]:
    persisted = metadata.get("telemetry_summary")
    result = json.loads(json.dumps(_EMPTY_TELEMETRY))
    if isinstance(persisted, dict):
        for name, defaults in result.items():
            if isinstance(persisted.get(name), dict):
                defaults.update(persisted[name])
    return result


def _tiers_empty() -> dict[str, Any]:
    return {
        "conservative": None,
        "balanced": None,
        "aggressive": None,
        "default": None,
    }


def _normalized_availability_zones(metadata: dict[str, Any]) -> list[str]:
    return sorted(
        {
            normalized
            for value in metadata.get("availability_zones") or []
            if (normalized := str(value).strip())
        }
    )


def _capacity_policy_view(
    policy: ASGCapacityPolicy, *, instance_optimization_enabled: bool
) -> dict[str, Any]:
    """Serialize one policy object through its effective V1 or V2 contract view."""
    evidence = asdict(policy)
    if not instance_optimization_enabled:
        evidence["policy_version"] = "asg-v1-balanced"
        evidence.pop("candidate_limit", None)
        evidence.pop("instance_optimization_enabled", None)
    return evidence


def _telemetry_schema_is_v2(metadata: dict[str, Any]) -> bool:
    demand = metadata.get("rightsizing_demand")
    return bool(
        isinstance(demand, dict)
        and demand.get("normalization_version") == TARGET_INDEPENDENT_DEMAND_SCHEMA
    )


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _selected_requirement(
    windows: dict[str, Any], tier: str, statistic: str
) -> tuple[int | None, dict[str, Any] | None, int | None]:
    candidates: list[tuple[int, int, dict[str, Any] | None]] = []
    for window in windows.values():
        if not isinstance(window, dict):
            continue
        days = int(window.get("lookback_days") or 0)
        summary = ((window.get("normalized") or {}).get("required_capacity") or {}).get(
            tier
        ) or {}
        value = summary.get(statistic)
        if value is not None:
            candidates.append((int(value), days, summary.get(f"{statistic}_record")))
    if not candidates:
        return None, None, None
    value, days, record = max(candidates, key=lambda item: (item[0], item[1]))
    return value, record if isinstance(record, dict) else None, days


def _decimal_eligible(savings: Decimal, minimum: float) -> bool:
    return savings >= Decimal("0.01") and savings >= Decimal(str(minimum))


def _policy_reason_codes(
    metadata: dict[str, Any], telemetry: dict[str, Any]
) -> list[str]:
    reasons: list[str] = []
    signals = metadata.get("operational_signals") or {}
    if signals.get("scaling_failure_seen"):
        reasons.append("RECENT_SCALING_FAILURE_REQUIRES_REVIEW")
    if signals.get("capacity_shortage_seen"):
        reasons.append("RECENT_CAPACITY_SHORTAGE_REQUIRES_REVIEW")
    if signals.get("desired_in_service_mismatch_seen"):
        reasons.append("DESIRED_IN_SERVICE_MISMATCH_REQUIRES_REVIEW")
    metrics = [
        str(value).casefold() for value in metadata.get("dynamic_policy_metrics") or []
    ]
    kinds = metadata.get("dynamic_policy_kinds") or []
    if not kinds:
        reasons.append("NO_DYNAMIC_SCALING_POLICY_REQUIRES_REVIEW")
    elif not any("cpu" in value for value in metrics):
        reasons.append("NON_CPU_SCALING_SIGNAL_REQUIRES_REVIEW")
    if metadata.get("capacity_rebalance"):
        reasons.append("CAPACITY_REBALANCE_REQUIRES_REVIEW")
    if not telemetry["desired_capacity"]["present"]:
        reasons.append("ASG_DESIRED_CAPACITY_HISTORY_UNAVAILABLE")
    if not _normalized_availability_zones(metadata):
        reasons.append("ASG_AVAILABILITY_ZONES_UNAVAILABLE_FLOOR_ONE")
    return list(dict.fromkeys(reasons))


def _common_risk_levels(
    reason_codes: list[str], projected: float | None
) -> tuple[str, str]:
    telemetry = (
        "MEDIUM" if any(code.startswith("ASG_") for code in reason_codes) else "LOW"
    )
    compute = "HIGH" if projected is not None and projected >= 0.85 else "LOW"
    return telemetry, compute


def _risk(reason_codes: list[str], projected: float | None) -> dict[str, Any]:
    telemetry, compute = _common_risk_levels(reason_codes, projected)
    operations = "MEDIUM" if reason_codes else "LOW"
    non_null = [telemetry, operations, compute, "LOW", "LOW"]
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    overall = max(non_null, key=order.__getitem__)
    return {
        "telemetry": telemetry,
        "compute": compute,
        "memory": "LOW",
        "network": None,
        "storage": None,
        "compatibility": "LOW",
        "migration": None,
        "operations": operations,
        "overall": overall,
        "reason_codes": reason_codes,
    }


def _risk_v2(
    reason_codes: list[str],
    projected: float | None,
    *,
    type_changed: bool,
    retained_memory: bool,
) -> dict[str, Any]:
    operational_codes = {
        "RECENT_SCALING_FAILURE_REQUIRES_REVIEW",
        "RECENT_CAPACITY_SHORTAGE_REQUIRES_REVIEW",
        "DESIRED_IN_SERVICE_MISMATCH_REQUIRES_REVIEW",
        "NON_CPU_SCALING_SIGNAL_REQUIRES_REVIEW",
        "NO_DYNAMIC_SCALING_POLICY_REQUIRES_REVIEW",
        "CAPACITY_REBALANCE_REQUIRES_REVIEW",
    }
    telemetry, compute = _common_risk_levels(reason_codes, projected)
    operations = (
        "MEDIUM" if any(code in operational_codes for code in reason_codes) else "LOW"
    )
    memory = "MEDIUM" if retained_memory else "LOW"
    compatibility = "MEDIUM" if type_changed else "LOW"
    values = [telemetry, operations, compute, memory, compatibility]
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    return {
        "telemetry": telemetry,
        "compute": compute,
        "memory": memory,
        "network": None,
        "storage": None,
        "compatibility": compatibility,
        "migration": None,
        "operations": operations,
        "overall": max(values, key=order.__getitem__),
        "reason_codes": reason_codes,
    }


def _parse_timestamp(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _nonnegative_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


class ASGRightsizer:
    TREND_SCHEMA_VERSION = "asg-trend-v1"

    def __init__(
        self,
        db: Session,
        catalog: EC2InstanceCatalog | None = None,
        capacity_policy: ASGCapacityPolicy | None = None,
        scope_policy: ASGScopePolicy | None = None,
        candidate_policy: EC2CandidatePolicy | None = None,
        deployment_enabled: bool | None = None,
        coremark_coverage_override: float | None = None,
    ) -> None:
        self.db = db
        self.catalog = catalog or EC2InstanceCatalog()
        self.capacity_policy = capacity_policy or ASGCapacityPolicy()
        self.scope_policy = scope_policy or ASGScopePolicy()
        self.candidate_policy = candidate_policy or EC2CandidatePolicy()
        self.deployment_enabled = (
            settings.asg_instance_optimization_enabled
            if deployment_enabled is None
            else deployment_enabled
        )
        self.coremark_min_coverage = (
            settings.asg_instance_optimization_min_coremark_coverage
        )
        self.coremark_coverage_override = coremark_coverage_override
        self._catalog_cache: dict[tuple[str, str], dict[str, EC2CatalogEntry]] = {}
        self._coverage_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._coverage_error_logged: set[tuple[str, str]] = set()
        self._telemetry_upgrade_logged: set[int] = set()

    def list_recommendations(
        self,
        *,
        account_id: str | None = None,
        region: str | None = None,
        state: str = "active",
        classification: str | None = None,
        min_monthly_savings: float = 0.0,
        candidate_limit: int | None = None,
    ) -> list[dict[str, Any]]:
        query = self.db.query(AsgInventory)
        if account_id:
            query = query.filter(AsgInventory.account_id == account_id)
        if region:
            query = query.filter(AsgInventory.region == region)
        if state:
            query = query.filter(AsgInventory.state == state)
        results = [
            self._recommend(row, min_monthly_savings, candidate_limit)
            for row in query.order_by(AsgInventory.inventory_id).all()
        ]
        if classification:
            expected = classification.upper()
            results = [
                item for item in results if item.get("classification") == expected
            ]
        return results

    def get_recommendation(
        self,
        inventory_id: int,
        *,
        min_monthly_savings: float = 0.0,
        candidate_limit: int | None = None,
    ) -> dict[str, Any] | None:
        row = (
            self.db.query(AsgInventory)
            .filter(AsgInventory.inventory_id == inventory_id)
            .first()
        )
        return (
            self._recommend(row, min_monthly_savings, candidate_limit) if row else None
        )

    def _base(
        self,
        row: AsgInventory,
        metadata: dict[str, Any],
        current: EC2CatalogEntry | None = None,
        *,
        instance_optimization_enabled: bool = False,
    ) -> dict[str, Any]:
        telemetry = _telemetry(metadata)
        zones = _normalized_availability_zones(metadata)
        in_service = len(metadata.get("in_service_instance_ids") or [])
        unit = Decimal(str(current.monthly_usd)) if current else None
        current_cost = unit * row.desired_capacity if unit is not None else None
        return {
            "inventory_id": row.inventory_id,
            "resource_id": row.resource_id,
            "resource_name": row.resource_name,
            "account_id": row.account_id,
            "region": row.region,
            "state": row.state,
            "classification": None,
            "current_monthly_cost": round(float(current_cost), 2)
            if current_cost is not None
            else None,
            "pricing_source": "street_pricing_sqlite" if current else None,
            "current_capacity_evidence": {
                "min_size": row.min_size,
                "desired_capacity": row.desired_capacity,
                "max_size": row.max_size,
                "in_service_instances": in_service,
                "availability_zone_count": len(zones),
                "inventory_generated_at": _utc_iso(row.generated_at),
            },
            "current_configuration": {
                "instance_type": row.instance_type,
                "min_size": row.min_size,
                "desired_capacity": row.desired_capacity,
                "max_size": row.max_size,
                "availability_zones": zones,
            },
            "recommendations": [],
            "tiers": _tiers_empty(),
            "savings_previews": [],
            "blocking_reasons": [],
            "deferred_reason_codes": [],
            "messages": [],
            "telemetry_summary": telemetry,
            "capacity_policy": _capacity_policy_view(
                self.capacity_policy,
                instance_optimization_enabled=instance_optimization_enabled,
            ),
            "scope_policy": asdict(self.scope_policy),
            "pricing_evidence": {
                "unit_monthly_price": float(unit) if unit is not None else None,
                "source": "street_pricing_sqlite" if current else None,
                "specification_source": current.capability_source if current else None,
            },
        }

    def _recommend(
        self,
        row: AsgInventory,
        minimum: float,
        candidate_limit: int | None,
    ) -> dict[str, Any]:
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        schema_is_v2 = _telemetry_schema_is_v2(metadata)
        if (
            self.deployment_enabled
            and self.capacity_policy.instance_optimization_enabled
            and not schema_is_v2
            and row.inventory_id not in self._telemetry_upgrade_logged
        ):
            logger.info(
                "ASG_INSTANCE_OPTIMIZATION_TELEMETRY_UPGRADE_REQUIRED "
                "inventory_id=%s account_id=%s region=%s resource_id=%s",
                row.inventory_id,
                row.account_id,
                row.region,
                row.resource_id,
            )
            self._telemetry_upgrade_logged.add(row.inventory_id)
        effective, entries, coverage = self._effective_instance_optimization(
            row, metadata
        )
        if not effective:
            return self._recommend_v1(row, minimum)
        assert coverage is not None
        limit = (
            self.capacity_policy.candidate_limit
            if candidate_limit is None
            else candidate_limit
        )
        if limit < 1:
            raise ValueError("candidate_limit must be positive")
        return self._recommend_v2(
            row,
            minimum,
            limit,
            entries=entries,
            coverage=coverage,
        )

    def _telemetry_gate(
        self,
        row: AsgInventory,
        metadata: dict[str, Any],
        response: dict[str, Any],
    ) -> int | None:
        """Apply the shared V1/V2 telemetry ladder and return the AZ floor."""
        telemetry = response["telemetry_summary"]
        if not telemetry["cpu_percent"]["present"]:
            response["classification"] = (
                RecommendationClassification.INSUFFICIENT_DATA.value
            )
            response["blocking_reasons"] = ["ASG_CPU_METRIC_UNAVAILABLE"]
            return None
        if not telemetry["in_service_instances"]["present"]:
            response["classification"] = (
                RecommendationClassification.INSUFFICIENT_DATA.value
            )
            response["blocking_reasons"] = [
                "ASG_IN_SERVICE_CAPACITY_METRIC_UNAVAILABLE"
            ]
            response["messages"].append(
                "Historical ASG capacity is unavailable. Enable Auto Scaling group "
                "metrics collection to provide GroupInServiceInstances for capacity assessment."
            )
            return None
        if not metadata.get("in_service_instance_ids"):
            response["classification"] = (
                RecommendationClassification.INSUFFICIENT_DATA.value
            )
            response["blocking_reasons"] = ["ASG_CURRENT_IN_SERVICE_CAPACITY_ZERO"]
            response["messages"].append(
                "The group currently has no in-service instances, so aggregate "
                "capacity demand cannot support a safe downsize recommendation."
            )
            return None
        if (
            telemetry["cpu_percent"].get("pairing_ratio") or 0
        ) < self.capacity_policy.minimum_pairing_ratio:
            response["classification"] = (
                RecommendationClassification.INSUFFICIENT_DATA.value
            )
            response["blocking_reasons"] = [
                "ASG_CPU_CAPACITY_PAIRING_INSUFFICIENT"
            ]
            return None
        observed = min(
            float(telemetry["cpu_percent"].get("observed_days") or 0),
            float(telemetry["in_service_instances"].get("observed_days") or 0),
        )
        if observed < self.capacity_policy.minimum_observed_days:
            response["classification"] = (
                RecommendationClassification.INSUFFICIENT_DATA.value
            )
            response["blocking_reasons"] = ["ASG_TELEMETRY_WINDOW_TOO_SHORT"]
            response["messages"].append(
                f"At least {self.capacity_policy.minimum_observed_days:g} observed days "
                f"are required; {observed:.3f} are available."
            )
            return None

        zones = response["current_configuration"]["availability_zones"]
        floor = max(1, len(zones) * self.capacity_policy.availability_floor_per_az)
        if min(row.min_size, row.desired_capacity, row.max_size) < floor:
            response["blocking_reasons"] = [
                "CURRENT_CAPACITY_BELOW_AVAILABILITY_FLOOR"
            ]
            return None
        return floor

    def _recommend_v1(self, row: AsgInventory, minimum: float) -> dict[str, Any]:
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        scope_metadata = {
            **metadata,
            "min_size": row.min_size,
            "desired_capacity": row.desired_capacity,
            "max_size": row.max_size,
        }
        scope_reason = asg_scope_reason(scope_metadata, self.scope_policy)
        if scope_reason:
            response = self._base(row, metadata)
            response["classification"] = RecommendationClassification.DEFERRED.value
            response["deferred_reason_codes"] = [scope_reason]
            return response

        current = self.catalog.get(
            row.region or "",
            row.instance_type or "",
            row.platform_normalized or "linux",
        )
        response = self._base(row, metadata, current)
        if current is None or current.capability_source != "describe_instance_types":
            response["blocking_reasons"] = ["CURRENT_INSTANCE_PRICING_OR_SPEC_MISSING"]
            return response

        floor = self._telemetry_gate(row, metadata, response)
        if floor is None:
            return response

        telemetry = response["telemetry_summary"]
        memory = telemetry["memory_percent"]
        preview = (
            not memory["present"]
            or memory.get("status") != "usable"
            or float(memory.get("observed_days") or 0)
            < self.capacity_policy.minimum_observed_days
        )
        reason_codes = _policy_reason_codes(metadata, telemetry)
        if not telemetry["desired_capacity"]["present"]:
            response["messages"].append(
                "Historical desired capacity is unavailable. Enable Auto Scaling "
                "group metrics collection to provide GroupDesiredCapacity; the "
                "current desired value remains available from the group configuration."
            )
        configurations, tier_map = self._configurations(
            row,
            metadata,
            current,
            floor,
            minimum,
            reason_codes,
            preview=preview,
        )
        if preview:
            if not self.capacity_policy.memory_preview_enabled or not configurations:
                return response
            if memory.get("status") == "insufficient_pairing":
                blocker = "ASG_MEMORY_CAPACITY_PAIRING_INSUFFICIENT"
            elif (
                memory.get("present")
                and float(memory.get("observed_days") or 0)
                < self.capacity_policy.minimum_observed_days
            ):
                blocker = "ASG_MEMORY_TELEMETRY_WINDOW_TOO_SHORT"
            else:
                blocker = "MEMORY_METRIC_NOT_ENABLED"
            response["classification"] = RecommendationClassification.PREVIEW.value
            response["savings_previews"] = [
                {
                    "kind": "MEMORY_METRIC_MISSING_CAPACITY_REDUCTION",
                    "classification": RecommendationClassification.PREVIEW.value,
                    "blockers": [blocker],
                    "message": (
                        "CPU history indicates that lower minimum and desired capacity "
                        "may save money, but total memory demand is unknown. Enable or "
                        "repair memory metrics to verify the reduction before changing the group."
                    ),
                    "options": tier_map,
                    "evidence": {
                        "telemetry_summary": telemetry,
                        "configurations": configurations,
                    },
                }
            ]
            return response

        response["recommendations"] = configurations
        response["tiers"] = tier_map
        if tier_map.get("balanced"):
            response["classification"] = tier_map["balanced"]["classification"]
        elif tier_map.get("aggressive"):
            response["classification"] = tier_map["aggressive"]["classification"]
        if not configurations:
            response["blocking_reasons"] = ["CURRENT_CAPACITY_NOT_OVERPROVISIONED"]
        return response

    def _configurations(
        self,
        row: AsgInventory,
        metadata: dict[str, Any],
        current: EC2CatalogEntry,
        floor: int,
        minimum: float,
        reason_codes: list[str],
        *,
        preview: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        windows = metadata.get("rightsizing_metrics") or {}
        unit_price = Decimal(str(current.monthly_usd))
        by_key: dict[tuple[int, int, int], dict[str, Any]] = {}
        tier_keys: dict[str, tuple[int, int, int]] = {}
        for tier, ratio in self.capacity_policy.tier_ratios:
            required_min, min_record, min_days = _selected_requirement(
                windows, tier, self.capacity_policy.minimum_statistic
            )
            required_desired, desired_record, desired_days = _selected_requirement(
                windows, tier, self.capacity_policy.desired_statistic
            )
            if required_min is None or required_desired is None:
                continue
            calculated_min = max(floor, required_min)
            calculated_desired = max(calculated_min, required_desired)
            target_min = min(row.min_size, calculated_min)
            target_desired = min(row.desired_capacity, calculated_desired)
            target_desired = max(target_min, target_desired)
            if target_desired >= row.desired_capacity:
                continue
            savings = Decimal(row.desired_capacity - target_desired) * unit_price
            if not _decimal_eligible(savings, minimum):
                continue
            key = (target_min, target_desired, row.max_size)
            projected_cpu = (
                float(desired_record.get("cpu_used_instances")) / target_desired
                if desired_record
                and desired_record.get("cpu_used_instances") is not None
                else None
            )
            projected_memory = (
                float(desired_record.get("memory_used_instances")) / target_desired
                if not preview
                and desired_record
                and desired_record.get("memory_used_instances") is not None
                else None
            )
            projected_values = [
                value
                for value in (projected_cpu, projected_memory)
                if value is not None
            ]
            projected = max(projected_values) if projected_values else None
            classification = (
                RecommendationClassification.PREVIEW.value
                if preview
                else RecommendationClassification.CONDITIONAL.value
                if reason_codes
                else RecommendationClassification.ACTIONABLE.value
            )
            option = by_key.get(key)
            if option is None:
                option = {
                    "target_min_size": target_min,
                    "target_desired_capacity": target_desired,
                    "target_max_size": row.max_size,
                    "max_size_changed": False,
                    "satisfied_tiers": [],
                    "projected_cpu_util": projected_cpu,
                    "projected_memory_util": projected_memory,
                    "projected_util": projected,
                    "binding_dimension": (
                        "cpu"
                        if preview and desired_record
                        else desired_record.get("binding_dimension")
                        if desired_record
                        else None
                    ),
                    "monthly_savings": round(float(savings), 2),
                    "yearly_savings": round(float(savings * 12), 2),
                    "classification": classification,
                    "risk_assessment": _risk(reason_codes, projected),
                    "reason_codes": list(reason_codes),
                    "evidence": {
                        "tier_ratio": ratio,
                        "minimum_selected_window_days": min_days,
                        "desired_selected_window_days": desired_days,
                        "minimum_record": min_record,
                        "desired_record": desired_record,
                        "availability_floor": floor,
                        "savings_basis": "initial_desired_capacity_delta",
                        "unit_monthly_price": float(unit_price),
                        "current_desired": row.desired_capacity,
                        "target_desired": target_desired,
                        "scaling_policy_may_change_realized_savings": True,
                    },
                }
                by_key[key] = option
            option["satisfied_tiers"].append(tier)
            tier_keys[tier] = key
        configurations = sorted(
            by_key.values(),
            key=lambda item: (
                -item["monthly_savings"],
                item["target_desired_capacity"],
                item["target_min_size"],
            ),
        )
        tier_map = {
            tier: by_key.get(tier_keys.get(tier))
            for tier, _ in self.capacity_policy.tier_ratios
        }
        tier_map["default"] = (
            self.capacity_policy.default_tier
            if tier_map.get(self.capacity_policy.default_tier)
            else None
        )
        return configurations, tier_map

    def _catalog_entries(self, row: AsgInventory) -> dict[str, EC2CatalogEntry]:
        key = (row.region or "", row.platform_normalized or "linux")
        if key not in self._catalog_cache:
            self._catalog_cache[key] = self.catalog.list_region(*key)
        return self._catalog_cache[key]

    def _catalog_coverage(
        self, row: AsgInventory, entries: dict[str, EC2CatalogEntry]
    ) -> dict[str, Any]:
        key = (row.region or "", row.platform_normalized or "linux")
        if key not in self._coverage_cache:
            coverage = coremark_catalog_coverage(entries.values())
            if self.coremark_coverage_override is not None:
                coverage = {
                    "numerator": int(round(self.coremark_coverage_override * 10000)),
                    "denominator": 10000,
                    "ratio": self.coremark_coverage_override,
                }
            metadata_loader = getattr(self.catalog, "specification_metadata", None)
            catalog_metadata = metadata_loader() if callable(metadata_loader) else {}
            self._coverage_cache[key] = {
                **coverage,
                "minimum_ratio": self.coremark_min_coverage,
                "passed": bool(
                    coverage["denominator"]
                    and coverage["ratio"] is not None
                    and coverage["ratio"] >= self.coremark_min_coverage
                ),
                "region": key[0],
                "platform": key[1],
                "catalog_schema_version": catalog_metadata.get("schema_version"),
                "catalog_generated_at": catalog_metadata.get("generated_at"),
                "specification_source_region": catalog_metadata.get("source_region"),
            }
        return self._coverage_cache[key]

    def _effective_instance_optimization(
        self, row: AsgInventory, metadata: dict[str, Any]
    ) -> tuple[
        bool,
        dict[str, EC2CatalogEntry],
        dict[str, Any] | None,
    ]:
        """Compose every V2 rollout gate before scope response shaping."""
        if not (
            self.deployment_enabled
            and self.capacity_policy.instance_optimization_enabled
            and _telemetry_schema_is_v2(metadata)
        ):
            return False, {}, None

        entries = self._catalog_entries(row)
        coverage = self._catalog_coverage(row, entries)
        effective = bool(coverage["passed"])
        if not effective:
            key = (row.region or "", row.platform_normalized or "linux")
            if key not in self._coverage_error_logged:
                logger.error(
                    "ASG instance optimization disabled: CoreMark catalog coverage "
                    "%s is below %.2f for %s/%s",
                    coverage.get("ratio"),
                    self.coremark_min_coverage,
                    key[0],
                    key[1],
                )
                self._coverage_error_logged.add(key)
        return effective, entries, coverage

    def _base_v2(
        self,
        row: AsgInventory,
        metadata: dict[str, Any],
        current: EC2CatalogEntry | None = None,
        coverage: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._base(
            row,
            metadata,
            current,
            instance_optimization_enabled=True,
        )
        response.update(
            {
                "candidate_policy": candidate_policy_evidence(self.candidate_policy),
                "rejection_summary": {},
                "availability_note": AVAILABILITY_NOTE,
                "availability_validated": False,
            }
        )
        response["pricing_evidence"].update(
            {
                "current_unit_monthly_price": (
                    current.monthly_usd if current is not None else None
                ),
                "coremark_catalog_coverage": coverage,
            }
        )
        return response

    @staticmethod
    def _demand_points(
        metadata: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], datetime | None]:
        demand = metadata.get("rightsizing_demand")
        if not isinstance(demand, dict):
            return [], None
        points: list[dict[str, Any]] = []
        for item in demand.get("points") or []:
            if not isinstance(item, dict):
                continue
            timestamp = _parse_timestamp(item.get("timestamp"))
            if timestamp is None:
                continue
            cpu = _nonnegative_number(item.get("cpu_used_instance_equivalents"))
            memory = _nonnegative_number(item.get("memory_used_instance_equivalents"))
            if cpu is None and memory is None:
                continue
            points.append({"timestamp": timestamp, "cpu": cpu, "memory": memory})
        points.sort(key=lambda item: item["timestamp"])
        end = _parse_timestamp(demand.get("end"))
        if end is None and points:
            end = points[-1]["timestamp"]
        return points, end

    @staticmethod
    def _capacity_demand_points(
        points: list[dict[str, Any]], current: EC2CatalogEntry
    ) -> list[dict[str, Any]]:
        """Convert target-independent demand once for all candidate evaluations."""
        return [
            {
                **point,
                "cpu_coremark": (
                    point["cpu"] * current.coremark
                    if point["cpu"] is not None
                    and current.coremark is not None
                    and current.coremark > 0
                    else None
                ),
                "cpu_vcpus": (
                    point["cpu"] * current.vcpus
                    if point["cpu"] is not None
                    and current.vcpus is not None
                    and current.vcpus > 0
                    else None
                ),
                "memory_mib": (
                    point["memory"] * current.memory_mib
                    if point["memory"] is not None
                    and current.memory_mib is not None
                    and current.memory_mib > 0
                    else None
                ),
            }
            for point in points
        ]

    def _target_requirements_by_tier(
        self,
        points: list[dict[str, Any]],
        end: datetime,
        current: EC2CatalogEntry,
        target: EC2CatalogEntry,
        *,
        memory_usable: bool,
    ) -> dict[str, dict[str, Any] | None]:
        basis = comparable_cpu_capacity(current, target)
        if basis is None:
            return {}
        tier_ratios = tuple(self.capacity_policy.tier_ratios)
        point_timestamps: list[datetime] = []
        records_by_tier: dict[str, list[dict[str, Any]]] = {
            tier: [] for tier, _ratio in tier_ratios
        }
        cpu_key = (
            "cpu_coremark"
            if basis.kind == CPUCapacityKind.COREMARK
            else "cpu_vcpus"
        )
        for point in points:
            cpu_used = point[cpu_key]
            memory_used = point["memory_mib"] if memory_usable else None
            if cpu_used is None and memory_used is None:
                continue
            point_timestamps.append(point["timestamp"])
            for tier, ratio in tier_ratios:
                cpu_required = (
                    exact_ceil_division(cpu_used, basis.target_units, ratio)
                    if cpu_used is not None
                    else None
                )
                memory_required = (
                    exact_ceil_division(memory_used, target.memory_mib, ratio)
                    if memory_used is not None
                    and target.memory_mib is not None
                    and target.memory_mib > 0
                    else None
                )
                measured = [
                    value
                    for value in (cpu_required, memory_required)
                    if value is not None
                ]
                required = max(measured)
                if memory_required is None or (
                    cpu_required is not None and cpu_required > memory_required
                ):
                    binding = "cpu"
                elif cpu_required is None or memory_required > cpu_required:
                    binding = "memory"
                else:
                    binding = "cpu_and_memory"
                records_by_tier[tier].append(
                    {
                        "timestamp": point["timestamp"].isoformat(),
                        "required": required,
                        "binding_dimension": binding,
                        "cpu_used_capacity": cpu_used,
                        "memory_used_mib": memory_used,
                    }
                )

        results: dict[str, dict[str, Any] | None] = {}
        for tier, _ratio in tier_ratios:
            window_evidence: dict[str, Any] = {}
            minimum_choices: list[tuple[int, int, dict[str, Any]]] = []
            desired_choices: list[tuple[int, int, dict[str, Any]]] = []
            for days in self.capacity_policy.lookback_days:
                cutoff = end - timedelta(days=days)
                records = records_by_tier[tier][
                    bisect_left(point_timestamps, cutoff) :
                ]
                ordered_records = sorted(
                    records, key=lambda item: (item["required"], item["timestamp"])
                )
                p50_selected = nearest_rank_percentile_sorted(ordered_records, 50)
                p99_selected = nearest_rank_percentile_sorted(ordered_records, 99)
                p50_record = dict(p50_selected) if p50_selected else None
                p99_record = dict(p99_selected) if p99_selected else None
                window_evidence[f"{days}d"] = {
                    "p50": p50_record["required"] if p50_record else None,
                    "p99": p99_record["required"] if p99_record else None,
                    "maximum": max(
                        (record["required"] for record in records), default=None
                    ),
                    "sample_count": len(records),
                    "p50_record": p50_record,
                    "p99_record": p99_record,
                }
                if p50_record:
                    minimum_choices.append(
                        (p50_record["required"], days, p50_record)
                    )
                if p99_record:
                    desired_choices.append(
                        (p99_record["required"], days, p99_record)
                    )
            if not minimum_choices or not desired_choices:
                results[tier] = None
                continue
            required_min, min_days, min_record = max(
                minimum_choices, key=lambda item: (item[0], item[1])
            )
            required_desired, desired_days, desired_record = max(
                desired_choices, key=lambda item: (item[0], item[1])
            )
            results[tier] = {
                "basis": basis,
                "required_min": required_min,
                "required_desired": required_desired,
                "minimum_window_days": min_days,
                "desired_window_days": desired_days,
                "minimum_record": min_record,
                "desired_record": desired_record,
                "windows": window_evidence,
            }
        return results

    @staticmethod
    def _economic_key(option: dict[str, Any]) -> tuple[Any, ...]:
        return (
            -option["_exact_savings"],
            option["_target_cost"],
            option["target_desired_capacity"],
            option["target_min_size"],
            option["target_instance_type"],
        )

    def _build_v2_option(
        self,
        row: AsgInventory,
        current: EC2CatalogEntry,
        target: EC2CatalogEntry,
        tier: str,
        ratio: float,
        requirements: dict[str, Any],
        target_min: int,
        target_desired: int,
        floor: int,
        reasons: list[str],
        current_cost: Decimal,
        target_cost: Decimal,
        *,
        retained_memory: bool,
        classification: str,
    ) -> dict[str, Any]:
        basis = requirements["basis"]
        desired_record = requirements["desired_record"]
        cpu_used = desired_record.get("cpu_used_capacity")
        memory_used = desired_record.get("memory_used_mib")
        projected_cpu = (
            cpu_used / (target_desired * basis.target_units)
            if cpu_used is not None and target_desired > 0
            else None
        )
        projected_memory = (
            memory_used / (target_desired * target.memory_mib)
            if memory_used is not None
            and target.memory_mib is not None
            and target.memory_mib > 0
            and target_desired > 0
            else None
        )
        projected_values = [
            value for value in (projected_cpu, projected_memory) if value is not None
        ]
        projected = max(projected_values) if projected_values else None
        performance_ratio, performance_change = performance_evidence(current, target)
        savings = exact_savings(current_cost, target_cost)
        type_changed = target.instance_type != current.instance_type
        optimization_kind = (
            OptimizationKind.CAPACITY_ONLY
            if not type_changed
            else OptimizationKind.INSTANCE_ONLY
            if target_desired == row.desired_capacity
            else OptimizationKind.COMBINED
        )
        return {
            "target_instance_type": target.instance_type,
            "optimization_kind": optimization_kind,
            "family_changed": instance_family(target.instance_type)
            != instance_family(current.instance_type),
            "architecture_overlap": sorted(
                set(current.architectures) & set(target.architectures)
            ),
            "target_min_size": target_min,
            "target_desired_capacity": target_desired,
            "target_max_size": row.max_size,
            "max_size_changed": False,
            "satisfied_tiers": [],
            "projected_cpu_util": projected_cpu,
            "projected_memory_util": projected_memory,
            "projected_util": projected,
            "binding_dimension": desired_record.get("binding_dimension"),
            "cpu_capacity_basis": basis.kind,
            "performance_ratio": performance_ratio,
            "performance_change_pct": performance_change,
            "current_unit_monthly_price": current.monthly_usd,
            "target_unit_monthly_price": target.monthly_usd,
            "target_monthly_cost": round(float(target_cost), 2),
            "monthly_savings": round(float(savings), 2),
            "yearly_savings": round(float(savings * 12), 2),
            "classification": classification,
            "risk_assessment": _risk_v2(
                reasons,
                projected,
                type_changed=type_changed,
                retained_memory=retained_memory,
            ),
            "reason_codes": list(reasons),
            "evidence": {
                "tier_ratio": ratio,
                "minimum_selected_window_days": requirements["minimum_window_days"],
                "desired_selected_window_days": requirements["desired_window_days"],
                "minimum_record": requirements["minimum_record"],
                "desired_record": desired_record,
                "window_requirements": requirements["windows"],
                "availability_floor": floor,
                "savings_basis": "initial_target_configuration",
                "current_desired": row.desired_capacity,
                "target_desired": target_desired,
                "scaling_policy_may_change_realized_savings": True,
                "current_spec": {
                    "vcpus": current.vcpus,
                    "memory_mib": current.memory_mib,
                    "coremark": current.coremark,
                    "network_performance": current.network_performance,
                    "ebs_max_iops": current.ebs_max_iops,
                    "ebs_max_throughput_mibps": current.ebs_max_throughput_mibps,
                },
                "target_spec": {
                    "vcpus": target.vcpus,
                    "memory_mib": target.memory_mib,
                    "coremark": target.coremark,
                    "network_performance": target.network_performance,
                    "ebs_max_iops": target.ebs_max_iops,
                    "ebs_max_throughput_mibps": target.ebs_max_throughput_mibps,
                },
                "aggregate_capacity": {
                    "current": {
                        name: {
                            "cpu_units": count * basis.current_units,
                            "memory_mib": (
                                count * current.memory_mib
                                if current.memory_mib is not None
                                else None
                            ),
                        }
                        for name, count in (
                            ("min", row.min_size),
                            ("desired", row.desired_capacity),
                            ("max", row.max_size),
                        )
                    },
                    "target": {
                        name: {
                            "cpu_units": count * basis.target_units,
                            "memory_mib": (
                                count * target.memory_mib
                                if target.memory_mib is not None
                                else None
                            ),
                        }
                        for name, count in (
                            ("min", target_min),
                            ("desired", target_desired),
                            ("max", row.max_size),
                        )
                    },
                },
            },
            "_exact_savings": savings,
            "_target_cost": target_cost,
            "_origin_tiers": {tier},
        }

    def _target_options(
        self,
        row: AsgInventory,
        current: EC2CatalogEntry,
        target: EC2CatalogEntry,
        points: list[dict[str, Any]],
        end: datetime,
        floor: int,
        minimum: float,
        base_reasons: list[str],
        *,
        memory_usable: bool,
        architecture_preview: bool = False,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool, set[str]]:
        normal: list[dict[str, Any]] = []
        previews: list[dict[str, Any]] = []
        desired_overflow = False
        rejection_codes: set[str] = set()
        if comparable_cpu_capacity(current, target) is None:
            return normal, previews, False, {"CPU_CAPABILITY_UNKNOWN"}
        if memory_usable and (
            current.memory_mib is None
            or target.memory_mib is None
            or target.memory_mib <= 0
        ):
            return normal, previews, False, {"MEMORY_REQUIREMENT_NOT_MET"}
        current_cost = Decimal(str(current.monthly_usd)) * row.desired_capacity
        requirements_by_tier = self._target_requirements_by_tier(
            points,
            end,
            current,
            target,
            memory_usable=memory_usable,
        )
        for tier, ratio in self.capacity_policy.tier_ratios:
            requirements = requirements_by_tier.get(tier)
            if requirements is None:
                rejection_codes.add("CPU_REQUIREMENT_NOT_MET")
                continue
            calculated_min = max(floor, requirements["required_min"])
            calculated_desired = max(calculated_min, requirements["required_desired"])
            if calculated_desired > row.desired_capacity:
                desired_overflow = True
                rejection_codes.add("TARGET_REQUIRES_CAPACITY_INCREASE")
                continue
            target_min = min(row.min_size, calculated_min)
            target_desired = min(row.desired_capacity, calculated_desired)
            target_desired = max(target_min, target_desired)
            type_changed = target.instance_type != current.instance_type
            if not type_changed and target_desired >= row.desired_capacity:
                continue
            target_cost = Decimal(str(target.monthly_usd)) * target_desired
            if not savings_is_eligible(current_cost, target_cost, minimum):
                rejection_codes.add("TOTAL_CONFIGURATION_SAVINGS_BELOW_MINIMUM")
                continue

            min_cpu_retained, min_memory_retained = raw_capacity_retention(
                current,
                target,
                current_count=row.min_size,
                target_count=target_min,
            )
            desired_cpu_retained, desired_memory_retained = raw_capacity_retention(
                current,
                target,
                current_count=row.desired_capacity,
                target_count=target_desired,
            )
            retained_memory = not memory_usable
            aggregate_memory_retained = (
                min_memory_retained and desired_memory_retained
            )
            memory_passes = memory_usable or aggregate_memory_retained
            raw_capacity_passes = bool(
                min_cpu_retained
                and desired_cpu_retained
                and aggregate_memory_retained
            )
            if architecture_preview and not raw_capacity_passes:
                rejection_codes.add("ARCHITECTURE_INCOMPATIBLE")
                continue

            reasons = list(base_reasons)
            if retained_memory and memory_passes:
                reasons.append("MEMORY_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED")
            if type_changed and not architecture_preview:
                reasons.append(
                    "INSTANCE_TYPE_CHANGE_NETWORK_STORAGE_VALIDATION_REQUIRED"
                )
            reasons = list(dict.fromkeys(reasons))
            classification = (
                RecommendationClassification.OPPORTUNITY.value
                if architecture_preview
                else RecommendationClassification.PREVIEW.value
                if not memory_passes
                else RecommendationClassification.CONDITIONAL.value
                if reasons or type_changed
                else RecommendationClassification.ACTIONABLE.value
            )
            option = self._build_v2_option(
                row,
                current,
                target,
                tier,
                ratio,
                requirements,
                target_min,
                target_desired,
                floor,
                reasons,
                current_cost,
                target_cost,
                retained_memory=retained_memory,
                classification=classification,
            )
            if architecture_preview:
                option["projected_cpu_util"] = None
                option["projected_util"] = option["projected_memory_util"]
                option["performance_ratio"] = None
                option["performance_change_pct"] = None
                option["evidence"]["minimum_record"]["cpu_used_capacity"] = None
                option["evidence"]["desired_record"]["cpu_used_capacity"] = None
                previews.append(option)
            elif not memory_passes:
                rejection_codes.add("MEMORY_REQUIREMENT_NOT_MET")
                previews.append(option)
            else:
                normal.append(option)
        return normal, previews, desired_overflow, rejection_codes

    def _deduplicate_options(
        self, options: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
        for option in options:
            key = (
                option["target_instance_type"],
                option["target_min_size"],
                option["target_desired_capacity"],
                option["target_max_size"],
            )
            existing = by_key.get(key)
            if existing is None or self._economic_key(option) < self._economic_key(
                existing
            ):
                if existing is not None:
                    option["_origin_tiers"].update(existing["_origin_tiers"])
                by_key[key] = option
            else:
                existing["_origin_tiers"].update(option["_origin_tiers"])
        return sorted(by_key.values(), key=self._economic_key)

    def _normal_tiers(self, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        picks = pick_tier_candidates(
            candidates,
            self.capacity_policy.tier_ratios,
            selection_key=self._economic_key,
        )
        for item in candidates:
            item["satisfied_tiers"] = [
                tier for tier, picked in picks.items() if picked is item
            ]
        return {
            **picks,
            "default": (
                self.capacity_policy.default_tier
                if picks.get(self.capacity_policy.default_tier)
                else None
            ),
        }

    def _preview_payload_v2(
        self,
        pool: list[dict[str, Any]],
        *,
        kind: str,
        blocker: str,
        message: str,
        classification: str,
    ) -> dict[str, Any] | None:
        if not pool:
            return None
        target_type = min(pool, key=self._economic_key)["target_instance_type"]
        target_options = self._deduplicate_options(
            [item for item in pool if item["target_instance_type"] == target_type]
        )
        picks: dict[str, dict[str, Any] | None] = {}
        for tier, _ratio in self.capacity_policy.tier_ratios:
            eligible = [
                item for item in target_options if tier in item["_origin_tiers"]
            ]
            picks[tier] = min(eligible, key=self._economic_key, default=None)
        for item in target_options:
            item["satisfied_tiers"] = [
                tier for tier, picked in picks.items() if picked is item
            ]
        best = min(target_options, key=self._economic_key)
        options = {
            **picks,
            "default": (
                self.capacity_policy.default_tier
                if picks.get(self.capacity_policy.default_tier)
                else None
            ),
        }
        return {
            "kind": kind,
            "target_instance_type": target_type,
            "monthly_savings": best["monthly_savings"],
            "yearly_savings": best["yearly_savings"],
            "classification": classification,
            "blockers": [blocker],
            "message": message,
            "options": options,
            "evidence": {
                "aggregate_capacity": best["evidence"]["aggregate_capacity"],
                "cpu_capacity_basis": best["cpu_capacity_basis"],
            },
        }

    @staticmethod
    def _strip_internal(option: dict[str, Any] | None) -> dict[str, Any] | None:
        if option is None:
            return None
        option.pop("_exact_savings", None)
        option.pop("_target_cost", None)
        option.pop("_origin_tiers", None)
        return option

    def _recommend_v2(
        self,
        row: AsgInventory,
        minimum: float,
        limit: int,
        *,
        entries: dict[str, EC2CatalogEntry],
        coverage: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        scope_metadata = {
            **metadata,
            "min_size": row.min_size,
            "desired_capacity": row.desired_capacity,
            "max_size": row.max_size,
        }
        scope_reason = asg_scope_reason(scope_metadata, self.scope_policy)
        if scope_reason:
            response = self._base_v2(row, metadata)
            response["classification"] = RecommendationClassification.DEFERRED.value
            response["deferred_reason_codes"] = [scope_reason]
            return response

        current = entries.get(row.instance_type or "")
        response = self._base_v2(row, metadata, current, coverage)
        if current is None or current.capability_source != "describe_instance_types":
            response["blocking_reasons"] = ["CURRENT_INSTANCE_PRICING_OR_SPEC_MISSING"]
            return response

        floor = self._telemetry_gate(row, metadata, response)
        if floor is None:
            return response

        telemetry = response["telemetry_summary"]
        points, end = self._demand_points(metadata)
        if not points or end is None:
            return self._recommend_v1(row, minimum)
        points = self._capacity_demand_points(points, current)
        memory = telemetry["memory_percent"]
        memory_usable = bool(
            memory["present"]
            and memory.get("status") == "usable"
            and (memory.get("pairing_ratio") or 0)
            >= self.capacity_policy.minimum_pairing_ratio
            and float(memory.get("observed_days") or 0)
            >= self.capacity_policy.minimum_observed_days
        )
        base_reasons = _policy_reason_codes(metadata, telemetry)
        if not telemetry["desired_capacity"]["present"]:
            response["messages"].append(
                "Historical desired capacity is unavailable. Enable Auto Scaling "
                "group metrics collection to provide GroupDesiredCapacity; the "
                "current desired value remains available from the group configuration."
            )

        candidates: list[dict[str, Any]] = []
        memory_preview_pool: list[dict[str, Any]] = []
        graviton_preview_pool: list[dict[str, Any]] = []
        rejected: dict[str, int] = {}
        for target_type in sorted(entries):
            target = entries[target_type]
            changed = target.instance_type != current.instance_type
            if (
                target.monthly_usd is None
                or target.monthly_usd < 0
                or target.capability_source != "describe_instance_types"
            ):
                rejected["TARGET_INSTANCE_PRICING_OR_SPEC_MISSING"] = (
                    rejected.get("TARGET_INSTANCE_PRICING_OR_SPEC_MISSING", 0) + 1
                )
                continue
            if changed and target.instance_type.endswith(".metal"):
                continue
            if changed and not architecture_overlaps(current, target):
                rejected["ARCHITECTURE_INCOMPATIBLE"] = (
                    rejected.get("ARCHITECTURE_INCOMPATIBLE", 0) + 1
                )
                if (
                    self.candidate_policy.graviton_preview_enabled
                    and "x86_64" in current.architectures
                    and "arm64" in target.architectures
                    and family_target_allowed(
                        current.instance_type,
                        target.instance_type,
                        self.candidate_policy,
                    )
                ):
                    _normal, preview_options, _overflow, _codes = self._target_options(
                        row,
                        current,
                        target,
                        points,
                        end,
                        floor,
                        minimum,
                        base_reasons,
                        memory_usable=memory_usable,
                        architecture_preview=True,
                    )
                    graviton_preview_pool.extend(preview_options)
                continue
            if changed and not family_target_allowed(
                current.instance_type, target.instance_type, self.candidate_policy
            ):
                rejected["FAMILY_NOT_ELIGIBLE"] = (
                    rejected.get("FAMILY_NOT_ELIGIBLE", 0) + 1
                )
                continue
            normal, preview_options, _overflow, codes = self._target_options(
                row,
                current,
                target,
                points,
                end,
                floor,
                minimum,
                base_reasons,
                memory_usable=memory_usable,
            )
            candidates.extend(normal)
            if self.candidate_policy.memory_preview_enabled:
                memory_preview_pool.extend(preview_options)
            for code in codes:
                rejected[code] = rejected.get(code, 0) + 1

        candidates = self._deduplicate_options(candidates)
        candidates = limit_preserving_balanced(
            candidates,
            limit,
            self.capacity_policy.tier_ratios,
            selection_key=self._economic_key,
            display_sort_key=self._economic_key,
        )
        tiers = self._normal_tiers(candidates)
        for rank, option in enumerate(candidates, 1):
            option["rank"] = rank

        previews: list[dict[str, Any]] = []
        if self.candidate_policy.memory_preview_enabled:
            if memory.get("status") == "insufficient_pairing":
                blocker = "ASG_MEMORY_CAPACITY_PAIRING_INSUFFICIENT"
            elif (
                memory.get("present")
                and float(memory.get("observed_days") or 0)
                < self.capacity_policy.minimum_observed_days
            ):
                blocker = "ASG_MEMORY_TELEMETRY_WINDOW_TOO_SHORT"
            else:
                blocker = "MEMORY_METRIC_NOT_ENABLED"
            preview = self._preview_payload_v2(
                memory_preview_pool,
                kind="MEMORY_METRIC_MISSING_CAPACITY_REDUCTION",
                blocker=blocker,
                message=(
                    "CPU history indicates that this instance-type and capacity "
                    "configuration may save money, but it reduces aggregate memory "
                    "while total memory demand is unknown. Enable or repair memory "
                    "metrics to verify the change."
                ),
                classification=RecommendationClassification.PREVIEW.value,
            )
            if preview:
                previews.append(preview)
        if self.candidate_policy.graviton_preview_enabled:
            preview = self._preview_payload_v2(
                graviton_preview_pool,
                kind="GRAVITON_MIGRATION",
                blocker="ARCHITECTURE_MIGRATION_REQUIRED",
                message=(
                    "This target uses the arm64 (Graviton) architecture. Realizing "
                    "this saving requires validating and migrating the application "
                    "to arm64. Compatibility is not validated by this recommendation."
                ),
                classification=RecommendationClassification.OPPORTUNITY.value,
            )
            if preview:
                previews.append(preview)

        for option in candidates:
            self._strip_internal(option)
        for tier, _ratio in self.capacity_policy.tier_ratios:
            self._strip_internal(tiers.get(tier))
        for preview in previews:
            for tier, _ratio in self.capacity_policy.tier_ratios:
                self._strip_internal(preview["options"].get(tier))

        response["recommendations"] = candidates
        response["tiers"] = tiers
        response["savings_previews"] = previews
        response["rejection_summary"] = rejected
        if tiers.get("balanced"):
            response["classification"] = tiers["balanced"]["classification"]
        elif tiers.get("aggressive"):
            response["classification"] = tiers["aggressive"]["classification"]
        elif previews:
            response["classification"] = RecommendationClassification.PREVIEW.value
        if not candidates and not previews:
            response["blocking_reasons"] = ["CURRENT_CAPACITY_NOT_OVERPROVISIONED"]
        return response

    def get_confidence_trend(
        self,
        inventory_id: int,
        adapter: AWSAdapter | None = None,
        end_date: datetime | None = None,
    ) -> dict[str, Any] | None:
        row = (
            self.db.query(AsgInventory)
            .filter(AsgInventory.inventory_id == inventory_id)
            .first()
        )
        if row is None:
            return None
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        source = metadata.get("memory_metric_source")
        source_key = json.dumps(source, sort_keys=True, default=str) if source else ""
        key = (
            row.account_id or "",
            row.region or "",
            row.resource_id or "",
            source_key,
            self.TREND_SCHEMA_VERSION,
        )
        cached = asg_trend_cache.get(key)
        if cached is not None:
            cached["cached"] = True
            return cached
        end = end_date or datetime.now(timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        aws_adapter = adapter or AWSAdapter(default_region=row.region or None)
        trend = aws_adapter.get_asg_confidence_trend(
            row.resource_id,
            end - timedelta(days=455),
            end,
            region=row.region,
            memory_source=source,
        )
        generated = datetime.now(timezone.utc)
        response = {
            "inventory_id": row.inventory_id,
            "auto_scaling_group_name": row.resource_id,
            "region": row.region,
            "metric": "CPUUtilization",
            "unit": "Percent",
            "headline_stat": "maximum",
            "bucket_semantics": {
                "maximum": "trailing_window_from_daily_maximum",
                "percentiles": "latest_complete_epoch_aligned_window",
            },
            "daily": trend.get("daily") or [],
            "buckets": trend.get("buckets") or {},
            "generated_at": generated.isoformat(),
            "expires_at": (generated + timedelta(seconds=3600)).isoformat(),
            "cached": False,
        }
        if isinstance(trend.get("memory"), dict):
            response["memory"] = trend["memory"]
        asg_trend_cache.set(key, response)
        return response
