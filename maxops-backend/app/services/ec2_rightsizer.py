"""Inventory-, scan-, and SQLite-backed EC2 rightsizing recommendations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.inventory import EbsInventory, Ec2Inventory
from app.adapters.aws.adapter import AWSAdapter
from app.services.ec2_instance_catalog import EC2CatalogEntry, EC2InstanceCatalog, instance_family
from app.services.ec2_trend_cache import ec2_trend_cache
from rightsizers.common.ec2_candidates import (
    EC2CandidatePolicy,
    architecture_overlaps,
    candidate_policy_evidence,
    exact_savings,
    family_target_allowed,
    gpu_target_allowed,
    limit_preserving_balanced,
    performance_evidence,
    retains_raw_capacity,
    savings_is_eligible,
)
from rightsizers.ec2.ec2_rightsizer import AVAILABILITY_NOTE
from rightsizers.ec2.ec2_rightsizer.constraints import (
    EBSConstraintInput,
    NetworkConstraintInput,
)
from rightsizers.ec2.ec2_rightsizer.evaluation import evaluate_ebs, evaluate_network
from rightsizers.ec2.ec2_rightsizer.models import (
    CapacityKind,
    HardConstraintStatus,
    PerformanceWarningPolicy,
    RecommendationClassification,
    ResourceEvaluation,
    RiskLevel,
)
from rightsizers.ec2.ec2_rightsizer.network_baseline import (
    assumed_network_baseline_mbps as shared_assumed_network_baseline_mbps,
    effective_network_baseline,
)
from rightsizers.ec2.ec2_rightsizer.risk import build_risk_assessment
from rightsizers.ec2.ec2_rightsizer.selection import classify_recommendation
from rightsizers.ec2.ec2_rightsizer.warnings import (
    EBSWarningInput,
    NetworkWarningInput,
    WARNING_MESSAGES,
)


@dataclass(frozen=True)
class EC2ComputePolicy:
    cpu_target_ratio: float = 0.70
    memory_target_ratio: float = 0.70
    tier_ratios: tuple[tuple[str, float], ...] = (
        ("conservative", 0.55),
        ("balanced", 0.70),
        ("aggressive", 0.85),
    )
    decision_statistic: str = "p99"
    lookback_days: tuple[int, ...] = (14, 30, 60)
    policy_version: str = "v1-balanced"

    def __post_init__(self) -> None:
        names: set[str] = set()
        balanced_ratio: float | None = None
        for name, ratio in self.tier_ratios:
            if not name or name in names:
                raise ValueError("tier names must be non-empty and unique")
            if not 0 < ratio <= 1:
                raise ValueError("tier ratios must be in (0, 1]")
            names.add(name)
            if name == "balanced":
                balanced_ratio = ratio
        if balanced_ratio is None:
            raise ValueError("tier ratios must define a balanced tier")
        if balanced_ratio != self.cpu_target_ratio:
            raise ValueError("balanced tier ratio must equal cpu_target_ratio")


@dataclass(frozen=True)
class EC2GpuRightsizingPolicy:
    """Safety policy for GPU capacity and observation-window interpretation."""

    minimum_observed_days: float = 7.0

    def __post_init__(self) -> None:
        if self.minimum_observed_days <= 0:
            raise ValueError("minimum_observed_days must be positive")


@dataclass(frozen=True)
class EC2ScopePolicy:
    allow_unknown_instance_store_usage: bool = False


def _candidate_policy_evidence(policy: EC2CandidatePolicy) -> dict[str, Any]:
    """Keep preview feature switches internal to preserve the response contract."""
    return candidate_policy_evidence(policy)


def _exact_savings(current_price: float, target_price: float) -> Decimal:
    """Return the decimal interpretation of catalog prices for eligibility only."""
    return exact_savings(current_price, target_price)


def _savings_is_eligible(
    current_price: float, target_price: float, min_savings: float
) -> bool:
    return savings_is_eligible(current_price, target_price, min_savings)


def _limit_candidates_preserving_balanced(
    candidates: list[dict[str, Any]], limit: int, policy: EC2ComputePolicy
) -> list[dict[str, Any]]:
    """Limit a savings-ranked list without displacing the legacy Balanced pick."""
    return limit_preserving_balanced(candidates, limit, policy.tier_ratios)


MEMORY_PREVIEW_MESSAGE = (
    "This target has less memory than the current instance. Actual memory usage "
    "is unknown because the CloudWatch agent memory metric is not enabled. Enable "
    "memory metrics to verify whether this saving is achievable."
)
GRAVITON_PREVIEW_MESSAGE = (
    "This target uses the arm64 (Graviton) architecture. Realizing this saving "
    "requires validating and migrating the application to arm64 (rebuilt "
    "binaries/images and compatible dependencies). Compatibility is not validated "
    "by this recommendation."
)


def _preview_payload(
    *,
    kind: str,
    target: EC2CatalogEntry,
    savings: float,
    evaluation: dict[str, Any],
    current: EC2CatalogEntry,
) -> dict[str, Any]:
    graviton = kind == "GRAVITON_MIGRATION"
    compute_evidence = dict(evaluation["compute_evidence"])
    projected_cpu = evaluation["projected_cpu_util"]
    performance_ratio = evaluation["performance_ratio"]
    performance_change = evaluation["performance_change_pct"]
    binding_dimension = evaluation["binding_dimension"]
    if graviton:
        projected_cpu = None
        performance_ratio = None
        performance_change = None
        compute_evidence["required_coremark"] = None
        compute_evidence["target_coremark"] = None
        if binding_dimension == "cpu":
            binding_dimension = None
    return {
        "kind": kind,
        "target_instance_type": target.instance_type,
        "monthly_savings": round(savings, 2),
        "yearly_savings": round(savings * 12.0, 2),
        "classification": (
            RecommendationClassification.OPPORTUNITY.value
            if graviton
            else RecommendationClassification.PREVIEW.value
        ),
        "blockers": [
            "ARCHITECTURE_MIGRATION_REQUIRED"
            if graviton
            else "MEMORY_METRIC_NOT_ENABLED"
        ],
        "message": GRAVITON_PREVIEW_MESSAGE if graviton else MEMORY_PREVIEW_MESSAGE,
        "evidence": {
            "projected_cpu_util": projected_cpu,
            "projected_memory_util": evaluation["projected_memory_util"],
            "projected_util": (
                evaluation["projected_memory_util"]
                if graviton
                else evaluation["projected_util"]
            ),
            "performance_ratio": performance_ratio,
            "performance_change_pct": performance_change,
            "binding_dimension": binding_dimension,
            "compute_evidence": compute_evidence,
            "network_evaluation": evaluation["network_evaluation"],
            "storage_evaluation": evaluation["storage_evaluation"],
            "capacity_retention": {
                "current_vcpus": current.vcpus,
                "target_vcpus": target.vcpus,
                "current_memory_mib": current.memory_mib,
                "target_memory_mib": target.memory_mib,
            },
        },
    }


def assumed_network_baseline_mbps(
    entry: EC2CatalogEntry,
    family_max_vcpus: float | None,
    policy: PerformanceWarningPolicy,
) -> float | None:
    """Compatibility wrapper around the shared EC2-derived baseline policy."""
    return shared_assumed_network_baseline_mbps(
        published_baseline_mbps=entry.network_baseline_mbps,
        reliable_max_mbps=entry.network_reliable_max_mbps,
        vcpus=entry.vcpus,
        family_max_vcpus=family_max_vcpus,
        policy=policy,
    )


def _effective_network_baseline(
    entry: EC2CatalogEntry,
    family_max_vcpus: dict[str, float],
    policy: PerformanceWarningPolicy,
) -> tuple[float | None, CapacityKind, bool]:
    return effective_network_baseline(
        published_baseline_mbps=entry.network_baseline_mbps,
        reliable_max_mbps=entry.network_reliable_max_mbps,
        vcpus=entry.vcpus,
        family_max_vcpus=family_max_vcpus.get(instance_family(entry.instance_type)),
        capacity_kind=entry.network_capacity_kind,
        policy=policy,
    )


def _family_max_vcpus(entries: dict[str, EC2CatalogEntry]) -> dict[str, float]:
    result: dict[str, float] = {}
    for entry in entries.values():
        if entry.instance_type.lower().endswith(".metal") or entry.vcpus is None:
            continue
        family = instance_family(entry.instance_type)
        result[family] = max(result.get(family, 0.0), entry.vcpus)
    return result


def _float(value: Any) -> float | None:
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


def _platform(metadata: dict[str, Any]) -> str:
    text = str(
        metadata.get("platform_normalized")
        or metadata.get("platform")
        or metadata.get("PlatformDetails")
        or "linux"
    ).lower()
    if "windows" in text:
        return "mswin"
    if "red hat" in text or "rhel" in text:
        return "rhel"
    if "suse" in text or "sles" in text:
        return "sles"
    if "ubuntu" in text:
        return "ubuntu"
    return "linux"


def _normalized_metric(
    metadata: dict[str, Any], name: str, stat: str = "p99"
) -> float | None:
    rightsizing = metadata.get("rightsizing_metrics")
    if not isinstance(rightsizing, dict):
        return None
    values: list[float] = []
    for window in ("14d", "30d", "60d"):
        payload = rightsizing.get(window)
        normalized = payload.get("normalized") if isinstance(payload, dict) else None
        metric = normalized.get(name) if isinstance(normalized, dict) else None
        value = _float(metric.get(stat)) if isinstance(metric, dict) else None
        if value is not None:
            values.append(value)
    return max(values) if values else None


def _gpu_demand(metadata: dict[str, Any]) -> tuple[int | None, float | None, int]:
    """Read persisted GPU demand maxima and their best available sample count."""
    required_devices = _normalized_metric(
        metadata, "gpu_required_devices", "maximum"
    )
    required_vram = _normalized_metric(metadata, "gpu_vram_used_mib", "maximum")
    sample_count = 0
    rightsizing = metadata.get("rightsizing_metrics")
    if isinstance(rightsizing, dict):
        for payload in rightsizing.values():
            normalized = payload.get("normalized") if isinstance(payload, dict) else {}
            metric = (
                normalized.get("gpu_required_devices")
                if isinstance(normalized, dict)
                else None
            )
            if isinstance(metric, dict):
                sample_count = max(sample_count, int(metric.get("sample_count") or 0))
    return (
        int(required_devices) if required_devices is not None else None,
        required_vram,
        sample_count,
    )


def _gpu_observation_is_thin(
    metadata: dict[str, Any], policy: EC2GpuRightsizingPolicy
) -> bool:
    """Return whether GPU evidence covers less than the configured minimum days."""
    _devices, _vram, sample_count = _gpu_demand(metadata)
    rightsizing = metadata.get("rightsizing_metrics")
    periods = []
    if isinstance(rightsizing, dict):
        periods = [
            int(payload.get("period_seconds") or 0)
            for payload in rightsizing.values()
            if isinstance(payload, dict)
        ]
    period = max(periods, default=300)
    return sample_count * period / 86400.0 < policy.minimum_observed_days


def _gpu_vram_requirement(
    observed_vram_mib: float, compute_policy: EC2ComputePolicy
) -> float:
    """Apply the tier's unused-capacity margin to VRAM demand only."""
    return observed_vram_mib * (1.0 + (1.0 - compute_policy.memory_target_ratio))


def _signal(metadata: dict[str, Any], name: str) -> bool | None:
    rightsizing = metadata.get("rightsizing_metrics")
    if not isinstance(rightsizing, dict):
        return None
    seen = False
    positive = False
    for window in ("14d", "30d", "60d"):
        payload = rightsizing.get(window)
        signals = payload.get("signals") if isinstance(payload, dict) else None
        if isinstance(signals, dict) and name in signals:
            seen = True
            positive = positive or bool(signals[name])
    return positive if seen else None


def _telemetry_summary(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rightsizing = metadata.get("rightsizing_metrics")
    rightsizing = rightsizing if isinstance(rightsizing, dict) else {}
    result: dict[str, dict[str, Any]] = {}
    for name in (
        "cpu_percent",
        "memory_percent",
        "network_in_mbps",
        "network_out_mbps",
    ):
        selected_count = 0
        selected_period = 0
        for window in ("14d", "30d", "60d"):
            payload = rightsizing.get(window)
            normalized = (
                payload.get("normalized") if isinstance(payload, dict) else None
            )
            metric = normalized.get(name) if isinstance(normalized, dict) else None
            count = (
                int(metric.get("sample_count") or 0) if isinstance(metric, dict) else 0
            )
            if count > 0:
                selected_count = count
                selected_period = int(payload.get("period_seconds") or 0)
        observed_days = selected_count * selected_period / 86400.0
        result[name] = {
            "present": selected_count > 0,
            "observed_days": observed_days,
            "thin_data": observed_days < 7.0,
        }
        if name == "memory_percent":
            result[name]["source"] = metadata.get("memory_metric_source")
            result[name]["status"] = (
                "usable"
                if selected_count > 0
                else str(metadata.get("memory_metric_status") or "unavailable")
            )
    return result


def _capacity_ratio(demand: float | None, capacity: float | None) -> float | None:
    if demand is None or capacity is None or capacity <= 0:
        return None
    return demand / capacity


def _current_capacity_evidence(
    current: EC2CatalogEntry,
    metadata: dict[str, Any],
    family_max_vcpus: dict[str, float],
    policy: PerformanceWarningPolicy,
) -> dict[str, Any]:
    memory_p99 = _normalized_metric(metadata, "memory_percent")
    network_in = _normalized_metric(metadata, "network_in_mbps")
    network_out = _normalized_metric(metadata, "network_out_mbps")
    network_in_max = _normalized_metric(metadata, "network_in_mbps", "maximum")
    network_out_max = _normalized_metric(metadata, "network_out_mbps", "maximum")
    baseline, capacity_kind, baseline_is_assumed = _effective_network_baseline(
        current, family_max_vcpus, policy
    )
    memory_used_mib = (
        current.memory_mib * memory_p99 / 100.0
        if current.memory_mib is not None and memory_p99 is not None
        else None
    )
    return {
        "decision_statistic": "p99",
        "memory": {
            "observed_p99_percent": memory_p99,
            "estimated_used_mib": memory_used_mib,
            "current_memory_mib": current.memory_mib,
            "source": metadata.get("memory_metric_source"),
            "fallback_used": memory_p99 is None,
        },
        "network": {
            "observed_in_p99_mbps": network_in,
            "observed_out_p99_mbps": network_out,
            "observed_in_max_mbps": network_in_max,
            "observed_out_max_mbps": network_out_max,
            "baseline_mbps": baseline,
            "baseline_is_assumed": baseline_is_assumed,
            "assumed_baseline": baseline_is_assumed,
            "assumed_baseline_mbps": baseline if baseline_is_assumed else None,
            "reliable_max_mbps": current.network_reliable_max_mbps,
            "network_in_utilization_ratio": _capacity_ratio(network_in, baseline),
            "network_out_utilization_ratio": _capacity_ratio(network_out, baseline),
            "network_in_peak_ratio": _capacity_ratio(
                network_in, current.network_reliable_max_mbps
            ),
            "network_out_peak_ratio": _capacity_ratio(
                network_out, current.network_reliable_max_mbps
            ),
            "capacity_kind": capacity_kind.value,
            "capability_source": current.capability_source,
            "bandwidth_weighting": str(
                metadata.get("network_bandwidth_weighting") or "default"
            ),
        },
    }


def _compute_evidence(
    current: EC2CatalogEntry,
    target: EC2CatalogEntry,
    metadata: dict[str, Any],
    policy: EC2ComputePolicy,
) -> dict[str, Any]:
    cpu_p99 = _normalized_metric(metadata, "cpu_percent")
    memory_p99 = _normalized_metric(metadata, "memory_percent")
    required_coremark, required_memory = _cpu_memory_requirements(
        current, metadata, policy
    )
    memory_used_mib = (
        current.memory_mib * memory_p99 / 100.0
        if current.memory_mib is not None and memory_p99 is not None
        else None
    )
    return {
        "decision_statistic": policy.decision_statistic,
        "observed_cpu_p99_percent": cpu_p99,
        "observed_memory_p99_percent": memory_p99,
        "estimated_memory_used_mib": memory_used_mib,
        "required_coremark": required_coremark,
        "required_memory_mib": required_memory,
        "target_coremark": target.coremark,
        "target_memory_mib": target.memory_mib,
        "projected_target_memory_percent": (
            memory_used_mib / target.memory_mib * 100.0
            if memory_used_mib is not None
            and target.memory_mib is not None
            and target.memory_mib > 0
            else None
        ),
        "memory_metric_source": metadata.get("memory_metric_source"),
        "memory_fallback_used": memory_p99 is None,
        "cpu_target_ratio": policy.cpu_target_ratio,
        "memory_target_ratio": policy.memory_target_ratio,
    }


def _cpu_memory_requirements(
    current: EC2CatalogEntry,
    metadata: dict[str, Any],
    policy: EC2ComputePolicy,
    target_ratio: float | None = None,
) -> tuple[float | None, float | None]:
    cpu_p99 = _normalized_metric(metadata, "cpu_percent")
    memory_p99 = _normalized_metric(metadata, "memory_percent")
    cpu_target_ratio = target_ratio or policy.cpu_target_ratio
    memory_target_ratio = target_ratio or policy.memory_target_ratio
    required_coremark = (
        current.coremark * cpu_p99 / 100.0 / cpu_target_ratio
        if current.coremark is not None and cpu_p99 is not None
        else current.coremark
    )
    required_memory = (
        current.memory_mib * memory_p99 / 100.0 / memory_target_ratio
        if current.memory_mib is not None and memory_p99 is not None
        else current.memory_mib
    )
    return required_coremark, required_memory


def _projected_candidate_utilization(
    current: EC2CatalogEntry,
    target: EC2CatalogEntry,
    metadata: dict[str, Any],
) -> tuple[float | None, float | None, float | None, float | None]:
    """Project CPU/memory pressure onto a target using the decision metrics."""
    cpu_p99 = _normalized_metric(metadata, "cpu_percent")
    memory_p99 = _normalized_metric(metadata, "memory_percent")

    projected_cpu = None
    if cpu_p99 is not None:
        cpu_capacity_ratio: float | None = None
        if current.coremark is not None and target.coremark and target.coremark > 0:
            cpu_capacity_ratio = current.coremark / target.coremark
        elif current.vcpus is not None and target.vcpus and target.vcpus > 0:
            cpu_capacity_ratio = current.vcpus / target.vcpus
        if cpu_capacity_ratio is not None:
            projected_cpu = cpu_p99 / 100.0 * cpu_capacity_ratio

    projected_memory = None
    if (
        memory_p99 is not None
        and current.memory_mib is not None
        and target.memory_mib
        and target.memory_mib > 0
    ):
        projected_memory = current.memory_mib * (memory_p99 / 100.0) / target.memory_mib

    projected_values = [
        value for value in (projected_cpu, projected_memory) if value is not None
    ]
    projected_util = max(projected_values) if projected_values else None
    performance_ratio = (
        target.coremark / current.coremark
        if current.coremark and target.coremark is not None
        else None
    )
    return projected_cpu, projected_memory, projected_util, performance_ratio


def _performance_change_pct(
    current: EC2CatalogEntry, target: EC2CatalogEntry
) -> float | None:
    """Return modeled whole-instance CPU throughput change from CoreMark."""
    return performance_evidence(current, target)[1]


def _retains_current_cpu_capacity(
    current: EC2CatalogEntry, target: EC2CatalogEntry
) -> bool:
    """Apply the no-metric CPU floor without adding a tier headroom margin."""
    if current.coremark is not None and target.coremark is not None:
        return target.coremark >= current.coremark
    if current.vcpus is None:
        return True
    return target.vcpus is not None and target.vcpus >= current.vcpus


def _retains_current_memory_capacity(
    current: EC2CatalogEntry, target: EC2CatalogEntry
) -> bool:
    """Apply the no-metric memory floor without adding a tier headroom margin."""
    if current.memory_mib is None:
        return True
    return target.memory_mib is not None and target.memory_mib >= current.memory_mib


def _binding_dimension(
    projected_cpu: float | None,
    projected_memory: float | None,
    network: ResourceEvaluation,
    storage: ResourceEvaluation,
) -> str | None:
    network_ratios = (
        _float(network.evidence.get("network_in_utilization_ratio")),
        _float(network.evidence.get("network_out_utilization_ratio")),
    )
    storage_ratios = (
        _float(storage.evidence.get("ebs_iops_utilization_ratio")),
        _float(storage.evidence.get("ebs_throughput_utilization_ratio")),
    )
    ratios = {
        "cpu": projected_cpu,
        "memory": projected_memory,
        "network": max(
            (value for value in network_ratios if value is not None), default=None
        ),
        "storage": max(
            (value for value in storage_ratios if value is not None), default=None
        ),
    }
    present = {name: value for name, value in ratios.items() if value is not None}
    return max(present, key=present.get) if present else None


def _tier_option(candidate: dict[str, Any]) -> dict[str, Any]:
    """Return the compact option carried by the recommendation tier envelope."""
    return {
        "target_instance_type": candidate["target_instance_type"],
        "projected_cpu_util": candidate["projected_cpu_util"],
        "projected_memory_util": candidate["projected_memory_util"],
        "projected_util": candidate["projected_util"],
        "performance_ratio": candidate["performance_ratio"],
        "performance_change_pct": candidate["performance_change_pct"],
        "binding_dimension": candidate["binding_dimension"],
        "monthly_savings": candidate["monthly_savings"],
        "yearly_savings": candidate["yearly_savings"],
        "classification": candidate["classification"],
        "risk_assessment": candidate["risk_assessment"],
        "satisfied_tiers": list(candidate["satisfied_tiers"]),
    }


def _recommendation_tiers(
    candidates: list[dict[str, Any]], policy: EC2ComputePolicy
) -> dict[str, Any]:
    picks: dict[str, dict[str, Any] | None] = {}
    for name, ratio in policy.tier_ratios:
        eligible = [
            candidate
            for candidate in candidates
            if candidate["projected_util"] is not None
            and candidate["projected_util"] <= ratio
        ]
        picks[name] = min(
            eligible,
            key=lambda candidate: (
                -candidate["monthly_savings"],
                candidate["target_instance_type"],
            ),
            default=None,
        )

    tiers_by_target: dict[str, list[str]] = {}
    for name, candidate in picks.items():
        if candidate is not None:
            tiers_by_target.setdefault(candidate["target_instance_type"], []).append(
                name
            )
    for candidate in candidates:
        candidate["satisfied_tiers"] = tiers_by_target.get(
            candidate["target_instance_type"], []
        )

    default = "balanced" if picks.get("balanced") is not None else None
    if default is None:
        for name, _ratio in sorted(policy.tier_ratios, key=lambda item: item[1]):
            if picks.get(name) is not None:
                default = name
                break
    return {
        **{
            name: _tier_option(candidate) if candidate is not None else None
            for name, candidate in picks.items()
        },
        "default": default,
    }


class EC2Rightsizer:
    TREND_SCHEMA_VERSION = "trend-v3"

    def __init__(
        self,
        db: Session,
        catalog: EC2InstanceCatalog | None = None,
        warning_policy: PerformanceWarningPolicy | None = None,
        compute_policy: EC2ComputePolicy | None = None,
        scope_policy: EC2ScopePolicy | None = None,
        candidate_policy: EC2CandidatePolicy | None = None,
        gpu_policy: EC2GpuRightsizingPolicy | None = None,
    ) -> None:
        self.db = db
        self.catalog = catalog or EC2InstanceCatalog()
        self.warning_policy = warning_policy or PerformanceWarningPolicy()
        self.compute_policy = compute_policy or EC2ComputePolicy()
        self.scope_policy = scope_policy or EC2ScopePolicy()
        self.candidate_policy = candidate_policy or EC2CandidatePolicy()
        self.gpu_policy = gpu_policy or EC2GpuRightsizingPolicy()

    def list_recommendations(
        self,
        *,
        account_id: str | None = None,
        region: str | None = None,
        state: str = "running",
        min_monthly_savings: float = 0.0,
        candidate_limit: int = 10,
    ) -> list[dict[str, Any]]:
        query = self.db.query(Ec2Inventory)
        if account_id:
            query = query.filter(Ec2Inventory.account_id == account_id)
        if region:
            query = query.filter(Ec2Inventory.region == region)
        if state:
            query = query.filter(Ec2Inventory.state == state)
        return [
            result
            for row in query.order_by(Ec2Inventory.inventory_id).all()
            if (result := self._recommend(row, min_monthly_savings, candidate_limit))
            is not None
        ]

    def get_confidence_trend(
        self,
        inventory_id: int,
        adapter: AWSAdapter | None = None,
        end_date: datetime | None = None,
    ) -> dict[str, Any] | None:
        row = (
            self.db.query(Ec2Inventory)
            .filter(Ec2Inventory.inventory_id == inventory_id)
            .first()
        )
        if row is None:
            return None
        key = (
            row.account_id or "",
            row.region or "",
            row.resource_id or "",
            self.TREND_SCHEMA_VERSION,
        )
        cached = ec2_trend_cache.get(key)
        if cached is not None:
            cached["cached"] = True
            return cached
        end = end_date or datetime.now(timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        aws_adapter = adapter or AWSAdapter(default_region=row.region or None)
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        trend = aws_adapter.get_ec2_confidence_trend(
            row.resource_id,
            end - timedelta(days=455),
            end,
            region=row.region or None,
            memory_source=metadata.get("memory_metric_source"),
        )
        generated_at = datetime.now(timezone.utc)
        response = {
            "inventory_id": row.inventory_id,
            "instance_id": row.resource_id,
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
            "generated_at": generated_at.isoformat(),
            "expires_at": (generated_at + timedelta(seconds=3600)).isoformat(),
            "cached": False,
        }
        if isinstance(trend.get("memory"), dict):
            response["memory"] = trend["memory"]
        ec2_trend_cache.set(key, response)
        return response

    def get_recommendation(
        self,
        inventory_id: int,
        *,
        min_monthly_savings: float = 0.0,
        candidate_limit: int = 10,
    ) -> dict[str, Any] | None:
        row = (
            self.db.query(Ec2Inventory)
            .filter(Ec2Inventory.inventory_id == inventory_id)
            .first()
        )
        return (
            self._recommend(row, min_monthly_savings, candidate_limit) if row else None
        )

    def _recommend(
        self, row: Ec2Inventory, min_savings: float, limit: int
    ) -> dict[str, Any] | None:
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        region = row.region or ""
        platform = _platform(metadata)
        entries = self.catalog.list_region(region, platform)
        family_max_vcpus = _family_max_vcpus(entries)
        structured_entries = [
            entry
            for entry in entries.values()
            if entry.capability_source == "describe_instance_types"
        ]
        capability_catalog = {
            "total_priced_types": len(entries),
            "structured_capability_types": len(structured_entries),
            "coverage_ratio": (
                len(structured_entries) / len(entries) if entries else 0.0
            ),
            "numeric_network_path_available": any(
                entry.network_baseline_mbps is not None for entry in structured_entries
            ),
            "ebs_attachment_path_available": any(
                entry.ebs_attachment_limit is not None for entry in structured_entries
            ),
        }
        current = entries.get(row.instance_type or "")
        metadata_deferred_reason = self._metadata_scope_reason(metadata)
        if metadata_deferred_reason is not None:
            return self._deferred_response(
                row, metadata, capability_catalog, metadata_deferred_reason
            )
        if current is None:
            return {
                "inventory_id": row.inventory_id,
                "resource_id": row.resource_id,
                "current_instance_type": row.instance_type,
                "recommendations": [],
                "savings_previews": [],
                "tiers": _recommendation_tiers([], self.compute_policy),
                "blocking_reasons": ["CURRENT_INSTANCE_PRICING_OR_SPEC_MISSING"],
                "availability_note": AVAILABILITY_NOTE,
                "capability_catalog": capability_catalog,
                "compute_policy": asdict(self.compute_policy),
                "scope_policy": asdict(self.scope_policy),
                "candidate_policy": _candidate_policy_evidence(self.candidate_policy),
                "telemetry_summary": _telemetry_summary(metadata),
            }
        if current.gpu_device_count is not None:
            gpu_status = str(metadata.get("gpu_metric_status") or "unavailable")
            try:
                gpu_observed = int(metadata["gpu_device_count_observed"])
            except (KeyError, TypeError, ValueError):
                gpu_observed = None
            gpu_evidence = {
                "gpu_metric_status": gpu_status,
                "gpu_metric_unavailable_reason": metadata.get(
                    "gpu_metric_unavailable_reason"
                ),
                "gpu_metric_source": metadata.get("gpu_metric_source"),
                "gpu_device_count_catalog": current.gpu_device_count,
                "gpu_device_count_observed": gpu_observed,
            }
            if gpu_status != "usable":
                return self._deferred_response(
                    row,
                    metadata,
                    capability_catalog,
                    "ACCELERATOR_TELEMETRY_UNAVAILABLE",
                    evidence=gpu_evidence,
                )
            if gpu_observed is None or gpu_observed != current.gpu_device_count:
                return self._deferred_response(
                    row,
                    metadata,
                    capability_catalog,
                    "ACCELERATOR_TELEMETRY_INCOMPLETE",
                    evidence=gpu_evidence,
                )
            required_devices, required_vram_mib, _sample_count = _gpu_demand(metadata)
            if required_devices is None or required_vram_mib is None:
                gpu_evidence["gpu_metric_unavailable_reason"] = "demand_unavailable"
                return self._deferred_response(
                    row,
                    metadata,
                    capability_catalog,
                    "ACCELERATOR_TELEMETRY_UNAVAILABLE",
                    evidence=gpu_evidence,
                )
            gpu_response_evidence = {
                "required_devices": required_devices,
                "required_vram_mib": required_vram_mib,
                "required_vram_mib_with_headroom": _gpu_vram_requirement(
                    required_vram_mib, self.compute_policy
                ),
                "device_count_catalog": current.gpu_device_count,
                "device_count_observed": int(gpu_observed),
            }
        else:
            required_devices = None
            required_vram_mib = None
            gpu_response_evidence = None
        instance_store_present = self._instance_store_presence(metadata, current)
        scope_review_codes: tuple[str, ...] = ()
        if instance_store_present is not False:
            if not self.scope_policy.allow_unknown_instance_store_usage:
                return self._deferred_response(
                    row,
                    metadata,
                    capability_catalog,
                    "INSTANCE_STORE_USAGE_UNKNOWN",
                )
            scope_review_codes = ("INSTANCE_STORE_USAGE_UNKNOWN",)
        generation_ratio = max(
            ratio for _name, ratio in self.compute_policy.tier_ratios
        )
        cpu_metric_available = _normalized_metric(metadata, "cpu_percent") is not None
        memory_metric_available = (
            _normalized_metric(metadata, "memory_percent") is not None
        )
        candidates: list[dict[str, Any]] = []
        memory_preview_pool: list[tuple[EC2CatalogEntry, float, bool]] = []
        graviton_preview_pool: list[tuple[EC2CatalogEntry, float]] = []
        rejected: dict[str, int] = {}
        for target in entries.values():
            if target.instance_type == current.instance_type:
                continue
            if not architecture_overlaps(current, target):
                rejected["ARCHITECTURE_INCOMPATIBLE"] = (
                    rejected.get("ARCHITECTURE_INCOMPATIBLE", 0) + 1
                )
                graviton_family_allowed = family_target_allowed(
                    current.instance_type, target.instance_type, self.candidate_policy
                )
                raw_capacity_retained = retains_raw_capacity(current, target)
                if (
                    self.candidate_policy.graviton_preview_enabled
                    and "x86_64" in current.architectures
                    and "arm64" not in current.architectures
                    and "arm64" in target.architectures
                    and graviton_family_allowed
                    and raw_capacity_retained
                    and _savings_is_eligible(
                        current.monthly_usd, target.monthly_usd, min_savings
                    )
                ):
                    graviton_preview_pool.append(
                        (target, current.monthly_usd - target.monthly_usd)
                    )
                continue
            # The GPU capability gate is additive to the family gate. A same-model
            # GPU target may cross a family token, but every other gated class
            # remains protected so an unused GPU cannot surface t*, inf*, or
            # another unsuitable accelerator family as a cheap target.
            same_gpu_model = (
                current.gpu_model is not None
                and target.gpu_model == current.gpu_model
            )
            if not same_gpu_model and not family_target_allowed(
                current.instance_type, target.instance_type, self.candidate_policy
            ):
                rejected["FAMILY_NOT_ELIGIBLE"] = (
                    rejected.get("FAMILY_NOT_ELIGIBLE", 0) + 1
                )
                continue
            gpu_conditional = False
            gpu_review_codes: tuple[str, ...] = ()
            if current.gpu_device_count is not None:
                gpu_allowed, gpu_rejection, gpu_conditional = gpu_target_allowed(
                    current,
                    target,
                    required_devices,
                    _gpu_vram_requirement(required_vram_mib, self.compute_policy),
                )
                if not gpu_allowed:
                    rejected[gpu_rejection] = rejected.get(gpu_rejection, 0) + 1
                    continue
                gpu_review_codes = (
                    ("GPU_UNUSED_FULL_WINDOW",)
                    if gpu_conditional
                    else ()
                )
                if _gpu_observation_is_thin(metadata, self.gpu_policy):
                    gpu_review_codes += ("GPU_OBSERVATION_WINDOW_TOO_SHORT",)
            savings = current.monthly_usd - target.monthly_usd
            if not _savings_is_eligible(
                current.monthly_usd, target.monthly_usd, min_savings
            ):
                continue
            (
                projected_cpu,
                projected_memory,
                _projected_util,
                _performance,
            ) = _projected_candidate_utilization(current, target, metadata)
            cpu_passes = (
                projected_cpu is None or projected_cpu <= generation_ratio
                if cpu_metric_available
                else _retains_current_cpu_capacity(current, target)
            )
            memory_passes = (
                projected_memory is None or projected_memory <= generation_ratio
                if memory_metric_available
                else _retains_current_memory_capacity(current, target)
            )
            if not cpu_passes or not memory_passes:
                code = (
                    "CPU_REQUIREMENT_NOT_MET"
                    if not cpu_passes
                    else "MEMORY_REQUIREMENT_NOT_MET"
                )
                rejected[code] = rejected.get(code, 0) + 1
                if (
                    self.candidate_policy.memory_preview_enabled
                    and not memory_metric_available
                    and cpu_passes
                    and not memory_passes
                ):
                    memory_preview_pool.append((target, savings, cpu_passes))
                continue
            candidate = self._evaluate_candidate(
                row,
                metadata,
                current,
                target,
                cpu_passes,
                memory_passes,
                family_max_vcpus,
                scope_review_codes,
                gpu_review_codes,
            )
            if candidate["classification"] == "REJECTED":
                for code in candidate["reason_codes"]:
                    rejected[code] = rejected.get(code, 0) + 1
                continue
            candidate["monthly_savings"] = round(savings, 2)
            candidate["yearly_savings"] = round(savings * 12.0, 2)
            candidates.append(candidate)

        savings_previews: list[dict[str, Any]] = []
        if self.candidate_policy.memory_preview_enabled:
            for target, savings, cpu_passes in sorted(
                memory_preview_pool,
                key=lambda item: (-round(item[1], 2), item[0].instance_type),
            ):
                evaluation = self._evaluate_candidate(
                    row,
                    metadata,
                    current,
                    target,
                    cpu_passes,
                    True,
                    family_max_vcpus,
                    scope_review_codes,
                    gpu_review_codes,
                )
                if evaluation["classification"] != "REJECTED":
                    savings_previews.append(
                        _preview_payload(
                            kind="MEMORY_METRIC_MISSING_DOWNSIZE",
                            target=target,
                            savings=savings,
                            evaluation=evaluation,
                            current=current,
                        )
                    )
                    break
        if self.candidate_policy.graviton_preview_enabled:
            for target, savings in sorted(
                graviton_preview_pool,
                key=lambda item: (-round(item[1], 2), item[0].instance_type),
            ):
                evaluation = self._evaluate_candidate(
                    row,
                    metadata,
                    current,
                    target,
                    True,
                    True,
                    family_max_vcpus,
                    scope_review_codes,
                )
                if evaluation["classification"] != "REJECTED":
                    savings_previews.append(
                        _preview_payload(
                            kind="GRAVITON_MIGRATION",
                            target=target,
                            savings=savings,
                            evaluation=evaluation,
                            current=current,
                        )
                    )
                    break
        candidates.sort(
            key=lambda item: (-item["monthly_savings"], item["target_instance_type"])
        )
        candidates = _limit_candidates_preserving_balanced(
            candidates, limit, self.compute_policy
        )
        for rank, candidate in enumerate(candidates, 1):
            candidate["rank"] = rank
        tiers = _recommendation_tiers(candidates, self.compute_policy)
        return {
            "inventory_id": row.inventory_id,
            "resource_id": row.resource_id,
            "resource_name": row.resource_name,
            "account_id": row.account_id,
            "region": row.region,
            "state": row.state,
            "current_instance_type": current.instance_type,
            "current_monthly_cost": round(current.monthly_usd, 2),
            "pricing_source": "street_pricing_sqlite",
            "policy": asdict(self.warning_policy),
            "compute_policy": asdict(self.compute_policy),
            "gpu_policy": asdict(self.gpu_policy),
            "gpu_demand_evidence": gpu_response_evidence,
            "candidate_policy": _candidate_policy_evidence(self.candidate_policy),
            "current_capacity_evidence": _current_capacity_evidence(
                current, metadata, family_max_vcpus, self.warning_policy
            ),
            "recommendations": candidates,
            "savings_previews": savings_previews,
            "tiers": tiers,
            "rejection_summary": rejected,
            "availability_note": AVAILABILITY_NOTE,
            "availability_validated": False,
            "capability_catalog": capability_catalog,
            "scope_policy": asdict(self.scope_policy),
            "telemetry_summary": _telemetry_summary(metadata),
        }

    def _evaluate_candidate(
        self,
        row: Ec2Inventory,
        metadata: dict[str, Any],
        current: EC2CatalogEntry,
        target: EC2CatalogEntry,
        cpu_passes: bool,
        memory_passes: bool,
        family_max_vcpus: dict[str, float],
        scope_review_codes: tuple[str, ...] = (),
        gpu_review_codes: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        network_in = _normalized_metric(metadata, "network_in_mbps")
        network_out = _normalized_metric(metadata, "network_out_mbps")
        network_in_max = _normalized_metric(metadata, "network_in_mbps", "maximum")
        network_out_max = _normalized_metric(metadata, "network_out_mbps", "maximum")
        (
            target_baseline,
            target_capacity_kind,
            baseline_is_assumed,
        ) = _effective_network_baseline(target, family_max_vcpus, self.warning_policy)
        combined_iops = _normalized_metric(metadata, "ebs_combined_iops")
        combined_mibps = _normalized_metric(metadata, "ebs_combined_mibps")
        attached_enis = int(metadata.get("attached_eni_count") or 0)
        attached_volumes = int(metadata.get("attached_volume_count") or 0)
        allowance_names = (
            "bw_in_allowance_exceeded",
            "bw_out_allowance_exceeded",
            "pps_allowance_exceeded",
            "conntrack_allowance_exceeded",
        )
        allowance_values = {name: _signal(metadata, name) for name in allowance_names}
        allowance_collected = all(
            value is not None for value in allowance_values.values()
        )
        ebs_exceeded_values = [
            _signal(metadata, name)
            for name in (
                "instance_ebs_iops_exceeded",
                "instance_ebs_throughput_exceeded",
            )
        ]
        ebs_exceeded_collected = all(value is not None for value in ebs_exceeded_values)
        network_relation = self._numeric_relation(
            current.network_baseline_mbps, target.network_baseline_mbps
        )
        if network_relation == "UNKNOWN":
            network_relation = self._class_relation(
                current.network_class, target.network_class
            )
        ebs_relation = self._numeric_relation(
            current.ebs_baseline_iops, target.ebs_baseline_iops
        )
        provisioned_iops, provisioned_throughput = self._attached_volume_requirements(
            metadata
        )
        target_attachment_limit = self._effective_ebs_attachment_limit(
            target, attached_enis
        )
        network_constraints = NetworkConstraintInput(
            attached_eni_count=attached_enis,
            target_eni_limit=target.eni_limit,
            requires_efa=bool(metadata.get("requires_efa")),
            target_supports_efa=target.efa_supported,
            observed_in_p99_mbps=network_in,
            observed_out_p99_mbps=network_out,
            target_reliable_max_mbps=target.network_reliable_max_mbps,
            allowance_exceeded=any(
                value is True for value in allowance_values.values()
            ),
            target_capacity_relation=network_relation,
        )
        bandwidth_weighting = str(
            metadata.get("network_bandwidth_weighting") or "default"
        ).lower()
        network_warnings = NetworkWarningInput(
            observed_in_p99_mbps=network_in,
            observed_out_p99_mbps=network_out,
            target_baseline_mbps=target_baseline,
            capacity_kind=target_capacity_kind,
            current_class=current.network_class,
            target_class=target.network_class,
            bandwidth_allowance_events=(
                allowance_values["bw_in_allowance_exceeded"] is True
                or allowance_values["bw_out_allowance_exceeded"] is True
            ),
            pps_allowance_events=allowance_values["pps_allowance_exceeded"] is True,
            conntrack_allowance_events=(
                allowance_values["conntrack_allowance_exceeded"] is True
            ),
            allowance_metrics_collected=allowance_collected,
            target_peak_mbps=target.network_reliable_max_mbps,
            baseline_is_assumed=baseline_is_assumed,
            bandwidth_weighting_default=bandwidth_weighting == "default",
        )
        ebs_constraints = EBSConstraintInput(
            target_supports_ebs=target.ebs_supported,
            attached_volume_count=attached_volumes,
            target_attachment_limit=target_attachment_limit,
            observed_combined_iops_p99=combined_iops,
            observed_combined_throughput_p99_mibps=combined_mibps,
            target_max_iops=target.ebs_max_iops,
            target_max_throughput_mibps=target.ebs_max_throughput_mibps,
            required_provisioned_iops=provisioned_iops,
            required_provisioned_throughput_mibps=provisioned_throughput,
            exceeded_or_throttled=any(value is True for value in ebs_exceeded_values),
            target_capacity_relation=ebs_relation,
        )
        ebs_warnings = EBSWarningInput(
            combined_iops,
            combined_mibps,
            target.ebs_baseline_iops,
            target.ebs_baseline_throughput_mibps,
            CapacityKind.BASELINE
            if target.ebs_baseline_iops and target.ebs_baseline_throughput_mibps
            else CapacityKind.UNKNOWN,
            any(value is True for value in ebs_exceeded_values),
            ebs_exceeded_collected,
            _signal(metadata, "high_ebs_queue") is True,
            _signal(metadata, "high_ebs_latency") is True,
            _signal(metadata, "ebs_burst_balance_depleted") is True,
            _signal(metadata, "ebs_directional_metrics_incomplete") is True,
        )
        network = evaluate_network(
            network_constraints, network_warnings, self.warning_policy
        )
        network_evidence = {
            **network.evidence,
            "observed_in_p99_mbps": network_in,
            "observed_out_p99_mbps": network_out,
            "observed_in_max_mbps": network_in_max,
            "observed_out_max_mbps": network_out_max,
            "target_baseline_mbps": target_baseline,
            "baseline_mbps": target_baseline,
            "assumed_baseline": baseline_is_assumed,
            "assumed_baseline_mbps": (target_baseline if baseline_is_assumed else None),
            "target_peak_mbps": target.network_reliable_max_mbps,
            "target_reliable_max_mbps": target.network_reliable_max_mbps,
            "target_capacity_kind": target_capacity_kind.value,
            "capacity_kind": target_capacity_kind.value,
            "capability_source": target.capability_source,
            "bandwidth_weighting": bandwidth_weighting,
        }
        network = ResourceEvaluation(
            network.hard_constraint_status,
            network.risk_level,
            network.warnings,
            network_evidence,
        )
        storage = (
            ResourceEvaluation(HardConstraintStatus.NOT_APPLICABLE, RiskLevel.LOW)
            if attached_volumes == 0
            else evaluate_ebs(ebs_constraints, ebs_warnings, self.warning_policy)
        )
        (
            projected_cpu_util,
            projected_memory_util,
            projected_util,
            performance_ratio,
        ) = _projected_candidate_utilization(current, target, metadata)
        performance_change_pct = _performance_change_pct(current, target)
        binding_dimension = _binding_dimension(
            projected_cpu_util, projected_memory_util, network, storage
        )
        classification = classify_recommendation(
            cpu_passes=cpu_passes,
            memory_passes=memory_passes,
            network=network,
            storage=storage,
        )
        if (
            scope_review_codes
            and classification != RecommendationClassification.REJECTED
        ):
            classification = RecommendationClassification.CONDITIONAL
        if (
            gpu_review_codes
            and classification != RecommendationClassification.REJECTED
        ):
            classification = RecommendationClassification.CONDITIONAL
        telemetry_risk = (
            RiskLevel.LOW
            if _normalized_metric(metadata, "cpu_percent") is not None
            else RiskLevel.HIGH
        )
        memory_available = _normalized_metric(metadata, "memory_percent") is not None
        risk_reason_codes = scope_review_codes + gpu_review_codes
        if current.gpu_device_count is not None:
            risk_reason_codes += ("GPU_DEVICE_DEMAND_USES_WINDOW_MAXIMUM",)
        if not memory_available:
            risk_reason_codes += (
                "MEMORY_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED",
            )
        risk = build_risk_assessment(
            network,
            storage,
            telemetry=telemetry_risk,
            compute=RiskLevel.LOW if cpu_passes else RiskLevel.HIGH,
            # Missing memory does not create target memory risk because the
            # conservative fallback requires the target to retain current RAM.
            memory=RiskLevel.LOW if memory_passes else RiskLevel.HIGH,
            compatibility=RiskLevel.HIGH if scope_review_codes else RiskLevel.LOW,
            reason_codes=risk_reason_codes,
        )
        warning_details = [
            {
                "code": code,
                "message": WARNING_MESSAGES.get(
                    code, code.replace("_", " ").capitalize() + "."
                ),
            }
            for code in risk.reason_codes
        ]
        return {
            "target_instance_type": target.instance_type,
            "family_changed": instance_family(target.instance_type)
            != instance_family(current.instance_type),
            "target_monthly_cost": round(target.monthly_usd, 2),
            "projected_cpu_util": projected_cpu_util,
            "projected_memory_util": projected_memory_util,
            "projected_util": projected_util,
            "performance_ratio": performance_ratio,
            "performance_change_pct": performance_change_pct,
            "binding_dimension": binding_dimension,
            "classification": classification.value,
            "risk_assessment": asdict(risk),
            "compute_evidence": _compute_evidence(
                current, target, metadata, self.compute_policy
            ),
            "gpu_evidence": self._gpu_candidate_evidence(
                current, target, metadata
            ),
            "network_evaluation": asdict(network),
            "storage_evaluation": asdict(storage),
            "reason_codes": list(risk.reason_codes),
            "warning_details": warning_details,
            "constraint_coverage": {
                "numeric_network_baseline": target.network_baseline_mbps is not None,
                "reliable_network_maximum": target.network_reliable_max_mbps
                is not None,
                "ebs_attachment_limit": target.ebs_attachment_limit is not None,
                "capability_source": target.capability_source,
            },
            "required_review": classification.value == "CONDITIONAL",
            "action_parameters": {
                "instance_id": row.resource_id,
                "instance_type": target.instance_type,
            },
        }

    def _gpu_candidate_evidence(
        self,
        current: EC2CatalogEntry,
        target: EC2CatalogEntry,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Expose GPU demand and target capacity without modeling performance."""
        if current.gpu_device_count is None:
            return None
        required_devices, required_vram, _sample_count = _gpu_demand(metadata)
        return {
            "required_devices": required_devices,
            "required_vram_mib": required_vram,
            "required_vram_mib_with_headroom": (
                _gpu_vram_requirement(required_vram, self.compute_policy)
                if required_vram is not None
                else None
            ),
            "source_gpu_model": current.gpu_model,
            "target_gpu_model": target.gpu_model,
            "source_gpu_device_count": current.gpu_device_count,
            "target_gpu_device_count": target.gpu_device_count,
            "target_gpu_memory_mib_per_device": target.gpu_memory_mib_per_device,
        }

    def _attached_volume_requirements(
        self, metadata: dict[str, Any]
    ) -> tuple[float | None, float | None]:
        volume_ids = metadata.get("attached_volume_ids")
        if not isinstance(volume_ids, list) or not volume_ids:
            return None, None
        rows = (
            self.db.query(EbsInventory)
            .filter(EbsInventory.resource_id.in_(volume_ids))
            .all()
        )
        if not rows:
            return None, None
        iops_values = [_float(row.iops) for row in rows]
        throughput_values = [_float(row.throughput) for row in rows]
        # Do not turn a missing provisioned setting into zero.
        total_iops = (
            sum(value for value in iops_values if value is not None)
            if any(value is not None for value in iops_values)
            else None
        )
        total_throughput = (
            sum(value for value in throughput_values if value is not None)
            if any(value is not None for value in throughput_values)
            else None
        )
        return total_iops, total_throughput

    @staticmethod
    def _metadata_scope_reason(metadata: dict[str, Any]) -> str | None:
        context = str(metadata.get("management_context") or "").lower()
        if context == "asg":
            return "MANAGED_BY_ASG"
        if context == "ecs":
            return "MANAGED_BY_ECS"
        if context == "kubernetes":
            return "MANAGED_BY_KUBERNETES"
        if str(metadata.get("tenancy") or "default").lower() != "default":
            return "UNSUPPORTED_TENANCY"
        if metadata.get("lifecycle"):
            return "UNSUPPORTED_LIFECYCLE"
        return None

    @staticmethod
    def _instance_store_presence(
        metadata: dict[str, Any], current: EC2CatalogEntry
    ) -> bool | None:
        persisted = metadata.get("instance_store_present")
        if persisted is True:
            return True
        if current.capability_source == "describe_instance_types":
            count = current.instance_store_device_count
            if count is not None and count > 0:
                return True
        if persisted is False:
            return False
        if current.capability_source == "describe_instance_types":
            count = current.instance_store_device_count
            return False if count == 0 else None
        return None

    def _deferred_response(
        self,
        row: Ec2Inventory,
        metadata: dict[str, Any],
        capability_catalog: dict[str, Any],
        reason: str,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "inventory_id": row.inventory_id,
            "resource_id": row.resource_id,
            "resource_name": row.resource_name,
            "account_id": row.account_id,
            "region": row.region,
            "state": row.state,
            "current_instance_type": row.instance_type,
            "classification": RecommendationClassification.DEFERRED.value,
            "recommendations": [],
            "savings_previews": [],
            "tiers": _recommendation_tiers([], self.compute_policy),
            "deferred_reason_codes": [reason],
            "deferred_evidence": evidence or {},
            "scope_policy": asdict(self.scope_policy),
            "candidate_policy": _candidate_policy_evidence(self.candidate_policy),
            "compute_policy": asdict(self.compute_policy),
            "availability_note": AVAILABILITY_NOTE,
            "availability_validated": False,
            "capability_catalog": capability_catalog,
            "telemetry_summary": _telemetry_summary(metadata),
        }

    @staticmethod
    def _effective_ebs_attachment_limit(
        target: EC2CatalogEntry, attached_eni_count: int
    ) -> int | None:
        if target.ebs_attachment_limit is None:
            return None
        if target.ebs_attachment_limit_type == "shared":
            return max(
                0,
                target.ebs_attachment_limit
                - attached_eni_count
                - (target.instance_store_device_count or 0),
            )
        return target.ebs_attachment_limit

    @staticmethod
    def _class_relation(current: str, target: str) -> str:
        order = {"LOW": 0, "MODERATE": 1, "HIGH": 2, "VERY_HIGH": 3, "EXTREME": 4}
        if current not in order or target not in order:
            return "UNKNOWN"
        return (
            "GREATER"
            if order[target] > order[current]
            else "LOWER"
            if order[target] < order[current]
            else "EQUAL"
        )

    @staticmethod
    def _numeric_relation(current: float | None, target: float | None) -> str:
        if current is None or target is None:
            return "UNKNOWN"
        return (
            "GREATER" if target > current else "LOWER" if target < current else "EQUAL"
        )
