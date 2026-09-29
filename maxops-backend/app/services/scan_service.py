"""Inventory-backed scan orchestration."""
from __future__ import annotations

import copy
import inspect
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple, Type

from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from botocore.exceptions import ClientError

from app.adapters.aws.adapter import AWSAdapter
from app.checks.registry import check_registry
from app.models.inventory import (
    AsgInventory,
    DynamoDbInventory,
    EbsInventory,
    Ec2Inventory,
    ElasticacheInventory,
    MaxOpsInventory,
    RdsInventory,
    S3Inventory,
    SageMakerInventory,
)
from app.models.policy import Policy
from app.models.settings import (
    AccountSettings,
    CheckFilter,
    OnboardingCheckResult,
    OnboardingExecution,
)
from app.pricing import pricing_registry
from app.pricing.base import PricingContext
from app.pricing.baseline import estimate_baseline_monthly_cost
from app.pricing.pricing_s3 import estimate_s3_storage_cost
from pricing.cur.datasets import DEFAULT_CACHE_ROOT
from app.services.aws_pricing_cache import AwsPricingCacheService
from app.services.ec2_instance_catalog import EC2InstanceCatalog
from app.services.settings_service import (
    ensure_account_settings,
    get_effective_check_parameters,
    is_check_enabled,
    is_s3_optimizer_enabled,
)
from app.services.s3_optimizer_enrichment import (
    OPTIMIZER_METADATA_KEYS,
    enrich_s3_buckets,
)
from app.utils.check_filter_applier import apply_check_filters
from app.utils.elasticache import (
    is_elasticache_member_cluster,
    purge_legacy_elasticache_member_rows,
)
from app.utils.resource_tags import sync_resource_tags
from app.utils.resource_snooze import build_exemption_filter_payload
from app.utils.settings_guard import (
    get_settings_regions,
    get_settings_scope,
    require_account_region,
)
from rightsizers.ec2.ec2_rightsizer.normalization.ebs import (
    bytes_to_mibps,
    operations_to_iops,
)
from rightsizers.ec2.ec2_rightsizer.normalization.network import (
    bytes_to_mbps,
    packets_to_pps,
)
from rightsizers.ec2.ec2_rightsizer.normalization.statistics import (
    nearest_rank_percentile,
)
from rightsizers.asg.asg_rightsizer.models import (
    ASGCapacityPolicy,
    ASGScopePolicy,
    asg_scope_reason,
)
from rightsizers.asg.asg_rightsizer.normalization import (
    aggregate_stable_member_memory,
    normalize_asg_metrics,
    series_points,
)
from rightsizers.rds.rds_rightsizer.normalization import normalize_cloudwatch_metrics
from rightsizers.rds.rds_rightsizer.telemetry import normalize_performance_insights
from rightsizers.rds.rds_rightsizer.catalogs import compatible_orderable_options
from rightsizers.rds.rds_rightsizer.inventory import (
    performance_insights_supported,
    scope_reason as rds_scope_reason,
)
from rightsizers.rds.rds_rightsizer.models import RdsRightsizerPolicy

logger = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class EC2GpuTelemetryPolicy:
    """Thresholds for converting aligned GPU telemetry into demand evidence."""

    busy_threshold: float = 5.0
    vram_floor_mib: float = 512.0

    def __post_init__(self) -> None:
        if not 0 <= self.busy_threshold <= 100:
            raise ValueError("busy_threshold must be between 0 and 100")
        if self.vram_floor_mib < 0:
            raise ValueError("vram_floor_mib must not be negative")


EC2_GPU_TELEMETRY_POLICY = EC2GpuTelemetryPolicy()

SUPPORTED_INVENTORY_TYPES = (
    "ec2",
    "s3",
    "rds",
    "ebs",
    "dynamodb",
    "elasticache",
    "asg",
    "sagemaker",
)
INVENTORY_MODELS: Dict[str, Type[Any]] = {
    "ec2": Ec2Inventory,
    "s3": S3Inventory,
    "rds": RdsInventory,
    "ebs": EbsInventory,
    "dynamodb": DynamoDbInventory,
    "elasticache": ElasticacheInventory,
    "asg": AsgInventory,
    "sagemaker": SageMakerInventory,
}


MetadataOverlayStore = Dict[Tuple[str, str], Dict[str, Any]]


class CachedAWSAdapter:
    """Per-scan cache for expensive AWS inventory/config reads."""

    def __init__(
        self,
        adapter: AWSAdapter,
        overlay_store: Optional[MetadataOverlayStore] = None,
    ):
        self._adapter = adapter
        self._cache: Dict[Tuple[Any, ...], Any] = {}
        self._overlay_store = overlay_store if overlay_store is not None else {}

    def get_resources(
        self,
        resource_type: str,
        filters: Optional[Dict[str, Any]] = None,
        region: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        key = ("get_resources", resource_type, repr(filters or {}), region)
        if key not in self._cache:
            self._cache[key] = self._adapter.get_resources(
                resource_type, filters, region
            )
        resources = copy.deepcopy(self._cache[key])
        overlays = self._overlay_store
        for resource in resources:
            if not isinstance(resource, dict):
                continue
            resource_id = resource.get("resource_id") or resource.get("name")
            resource_overlays = overlays.get((resource_type, str(resource_id)))
            if not resource_overlays:
                continue
            metadata = resource.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                resource["metadata"] = metadata
            metadata.update(copy.deepcopy(resource_overlays))
        return resources

    def overlay_resource_metadata(
        self,
        resource_type: str,
        resource_id: str,
        key: str,
        value: Any,
    ) -> None:
        """Record additive metadata for every adapter used by this scan.

        ``get_resources`` returns a deep copy and S3 listing is global, so a
        bucket can be returned by an adapter configured for another region.
        A scan-shared store makes the enrichment visible regardless of which
        regional adapter later serves the policy check.
        """

        overlay_key = (resource_type, str(resource_id))
        self._overlay_store.setdefault(overlay_key, {})[key] = copy.deepcopy(value)

    def get_resource_utilization(
        self,
        resource_id: str,
        resource_type: str,
        start_date: datetime,
        end_date: datetime,
        region: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Cache per-scan utilization reads and isolate every caller's copy.

        The key deliberately omits the exact end timestamp. Each check captures
        its own ``datetime.utcnow()`` once before iterating instances, so the
        two EC2 checks' timestamps differ by however long the first check took
        to run — minutes on a large account, not microseconds. Keying on the
        window length instead lets the memo fire regardless of that gap, and
        keeps behaviour independent of what time the scan happens to start.
        This adapter is built per region per scan (``adapter_cache`` in
        ``run_inventory_scan``), so entries cannot outlive the scan.
        """
        window_days = round((end_date - start_date).total_seconds() / 86400)
        key = (
            "get_resource_utilization",
            resource_id,
            resource_type,
            region,
            window_days,
        )
        if key not in self._cache:
            self._cache[key] = self._adapter.get_resource_utilization(
                resource_id,
                resource_type,
                start_date,
                end_date,
                region=region,
            )
        return copy.deepcopy(self._cache[key])

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._adapter, name)
        if name == "get_rds_rightsizing_prices":
            def cached_rds_prices(
                db_instance: Dict[str, Any],
                target_classes: List[str],
                *,
                region: Optional[str] = None,
            ) -> Any:
                key = (
                    name,
                    str(db_instance.get("Engine") or ""),
                    str(db_instance.get("EngineVersion") or ""),
                    str(db_instance.get("LicenseModel") or ""),
                    bool(db_instance.get("MultiAZ")),
                    region,
                )
                if key not in self._cache:
                    # GetProducts cannot filter a set of instance classes, so
                    # collect the dimension-identical regional catalog once.
                    self._cache[key] = attr(db_instance, [], region=region)
                result = copy.deepcopy(self._cache[key])
                prices = result.get("class_prices") if isinstance(result, dict) else None
                if isinstance(prices, dict):
                    wanted = set(target_classes)
                    result["class_prices"] = {
                        instance_type: value
                        for instance_type, value in prices.items()
                        if instance_type in wanted
                    }
                return result

            return cached_rds_prices
        if name == "get_rds_orderable_options":
            def cached_orderable(*args: Any, **kwargs: Any) -> Any:
                key = (name, repr(args), repr(sorted(kwargs.items())))
                if key not in self._cache:
                    self._cache[key] = attr(*args, **kwargs)
                return copy.deepcopy(self._cache[key])

            return cached_orderable
        if not callable(attr) or not (
            name.startswith("get_s3_bucket_") or name.startswith("list_s3_bucket_")
        ):
            return attr

        def cached_call(*args: Any, **kwargs: Any) -> Any:
            key = (name, repr(args), repr(sorted(kwargs.items())))
            if key not in self._cache:
                self._cache[key] = attr(*args, **kwargs)
            return copy.deepcopy(self._cache[key])

        return cached_call


def _get_cached_adapter(
    adapter_cache: Dict[Optional[str], CachedAWSAdapter],
    region: Optional[str],
    overlay_store: Optional[MetadataOverlayStore] = None,
) -> CachedAWSAdapter:
    if region not in adapter_cache:
        adapter_cache[region] = CachedAWSAdapter(
            AWSAdapter(default_region=region), overlay_store
        )
    return adapter_cache[region]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_scan_progress(
    db: Session,
    execution: Optional[OnboardingExecution],
    *,
    phase: str,
    message: str,
    current_region: Optional[str] = None,
    current_resource_type: Optional[str] = None,
    current_check_id: Optional[str] = None,
    current_check_name: Optional[str] = None,
    check_index: Optional[int] = None,
    total_checks: Optional[int] = None,
    inventory_counts: Optional[Dict[str, int]] = None,
    completed_checks: Optional[int] = None,
    failed_checks: Optional[int] = None,
) -> None:
    if execution is None:
        return

    payload = dict(execution.results_json or {})
    progress = dict(payload.get("progress") or {})
    progress.update(
        {
            "phase": phase,
            "message": message,
            "current_region": current_region,
            "current_resource_type": current_resource_type,
            "current_check_id": current_check_id,
            "current_check_name": current_check_name,
            "check_index": check_index,
            "total_checks": total_checks,
            "inventory_counts": inventory_counts
            or payload.get("inventory_counts")
            or {},
            "completed_checks": completed_checks
            if completed_checks is not None
            else execution.completed_checks,
            "failed_checks": failed_checks
            if failed_checks is not None
            else execution.failed_checks,
            "updated_at": _utc_now_iso(),
        }
    )
    payload["progress"] = progress
    if inventory_counts is not None:
        payload["inventory_counts"] = inventory_counts
    execution.results_json = payload
    execution.completed_checks = (
        completed_checks if completed_checks is not None else execution.completed_checks
    )
    execution.failed_checks = (
        failed_checks if failed_checks is not None else execution.failed_checks
    )
    db.add(execution)
    db.commit()
    db.refresh(execution)


def _serialize_metadata(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serialize_metadata(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize_metadata(item) for item in value]
    return value


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_float(metadata: Dict[str, Any], keys: Iterable[str]) -> Optional[float]:
    for key in keys:
        value = _as_float(metadata.get(key))
        if value is not None:
            return value
    return None


def _dedupe_resources(resources: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str, str, str]] = set()
    for resource in resources:
        key = (
            str(resource.get("resource_type") or ""),
            str(resource.get("resource_id") or ""),
            str(resource.get("region") or ""),
            str(resource.get("account_id") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(resource)
    return deduped


def _canonical_resource_type(resource_type: str) -> str:
    if resource_type in {"dynamodb_table", "dynamodb_gsi"}:
        return "dynamodb"
    if resource_type in {"elasticache_replication_group", "elasticache_cluster"}:
        return "elasticache"
    if resource_type == "rds_instance":
        return "rds"
    if resource_type in {
        "sagemaker_notebook",
        "sagemaker_endpoint",
        "sagemaker_training_job",
    }:
        return "sagemaker"
    return resource_type


def _inventory_call_types(resource_type: str) -> Tuple[str, ...]:
    if resource_type == "elasticache":
        return ("elasticache_replication_group", "elasticache_cluster")
    if resource_type == "dynamodb":
        return ("dynamodb",)
    if resource_type == "sagemaker":
        return (
            "sagemaker_notebook",
            "sagemaker_endpoint",
            "sagemaker_training_job",
        )
    return (resource_type,)


def _resource_regions(resource_type: str, regions: List[str]) -> List[Optional[str]]:
    if resource_type == "s3":
        return [regions[0] if regions else "us-east-1"]
    return regions


def _resource_in_selected_regions(
    resource: Dict[str, Any], selected_regions: List[str]
) -> bool:
    if not selected_regions:
        return False
    region = str(resource.get("region") or "").strip()
    return not region or region in selected_regions


def _resource_metadata(resource: Dict[str, Any]) -> Dict[str, Any]:
    metadata = resource.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _ec2_pricing_platform(metadata: Dict[str, Any]) -> str:
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


def _enrich_ec2_instance_store_capability(
    resource: Dict[str, Any],
    catalog: EC2InstanceCatalog,
    catalog_cache: Dict[Tuple[str, str], Dict[str, Any]],
) -> None:
    metadata = dict(_resource_metadata(resource))
    region = str(resource.get("region") or "")
    instance_type = str(
        resource.get("instance_type") or metadata.get("instance_type") or ""
    )
    platform = _ec2_pricing_platform(metadata)
    key = (region, platform)
    if key not in catalog_cache:
        catalog_cache[key] = catalog.list_region(region, platform)
    entry = catalog_cache[key].get(instance_type)
    count = (
        entry.instance_store_device_count
        if entry is not None and entry.capability_source == "describe_instance_types"
        else None
    )
    metadata["instance_store_present"] = count > 0 if count is not None else None
    gpu_device_count = getattr(entry, "gpu_device_count", None)
    if entry is not None and gpu_device_count is not None:
        metadata["gpu_device_count_catalog"] = gpu_device_count
        metadata["gpu_fractional"] = bool(getattr(entry, "gpu_fractional", False))
        metadata["gpu_model_catalog"] = getattr(entry, "gpu_model", None)
        metadata["gpu_memory_mib_per_device_catalog"] = (
            getattr(entry, "gpu_memory_mib_per_device", None)
        )
    resource["metadata"] = metadata


def _asg_scope_blocked(metadata: Dict[str, Any]) -> bool:
    return asg_scope_reason(metadata, ASGScopePolicy()) is not None


def _activity_time(item: Dict[str, Any]) -> datetime:
    value = _parse_datetime(item.get("StartTime") or item.get("EndTime"))
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _asg_activity_signals(metadata: Dict[str, Any]) -> Dict[str, bool]:
    latest: Dict[str, Dict[str, Any]] = {}
    for activity in sorted(
        metadata.get("scaling_activities") or [], key=_activity_time, reverse=True
    ):
        text = " ".join(
            str(activity.get(name) or "")
            for name in ("Description", "Cause", "Details")
        ).casefold()
        if any(token in text for token in ("launch", "add", "increase", "scale out")):
            kind = "launch"
        elif any(
            token in text for token in ("terminat", "remove", "decrease", "scale in")
        ):
            kind = "terminate"
        else:
            kind = "other"
        latest.setdefault(kind, activity)
    failed_statuses = {"failed", "cancelled"}
    failed = any(
        str(item.get("StatusCode") or "").casefold() in failed_statuses
        for item in latest.values()
    )
    launch = latest.get("launch") or {}
    launch_failed = str(launch.get("StatusCode") or "").casefold() in failed_statuses
    shortage_text = " ".join(
        str(launch.get(name) or "")
        for name in ("StatusMessage", "Cause", "Details")
    ).casefold()
    shortage = launch_failed and any(
        token in shortage_text
        for token in (
            "insufficient capacity",
            "capacity-oversubscribed",
            "capacity not available",
            "unfulfillable capacity",
        )
    )
    return {
        "scaling_failure_seen": failed,
        "capacity_shortage_seen": shortage,
        "desired_in_service_mismatch_seen": int(
            metadata.get("desired_capacity") or 0
        )
        != len(metadata.get("in_service_instance_ids") or []),
    }


def _asg_stable_members_proven(
    metadata: Dict[str, Any],
    raw: Dict[str, Any],
    start: datetime,
    policy: ASGCapacityPolicy,
) -> bool:
    instance_ids = sorted(metadata.get("in_service_instance_ids") or [])
    members = {
        str(item.get("InstanceId")): item
        for item in metadata.get("member_instances") or []
        if item.get("InstanceId")
    }
    if not instance_ids or sorted(members) != instance_ids:
        return False
    for item in members.values():
        launch_time = _parse_datetime(item.get("LaunchTime"))
        if launch_time is None:
            return False
        if launch_time.tzinfo is None:
            launch_time = launch_time.replace(tzinfo=timezone.utc)
        if launch_time.astimezone(timezone.utc) > start:
            return False
    metrics = raw.get("metrics") or {}
    desired = series_points(metrics.get("desired_capacity") or {})
    in_service = series_points(metrics.get("in_service_instances") or {})
    expected = len(instance_ids)
    if not desired or not in_service:
        return False
    period = int(raw.get("period_seconds") or 300)
    required_samples = int(
        max(policy.lookback_days)
        * 86400
        / period
        * policy.minimum_pairing_ratio
    )
    if len(desired) < required_samples or len(in_service) < required_samples:
        return False
    if min(desired) > start + timedelta(seconds=period) or min(
        in_service
    ) > start + timedelta(seconds=period):
        return False
    if any(int(value) != expected for value in desired.values()) or any(
        int(value) != expected for value in in_service.values()
    ):
        return False
    for activity in metadata.get("scaling_activities") or []:
        if _activity_time(activity) < start:
            continue
        text = " ".join(
            str(activity.get(name) or "")
            for name in ("Description", "Cause", "Details")
        ).casefold()
        if any(
            token in text
            for token in (
                "launch",
                "terminat",
                "replace",
                "add",
                "remove",
                "increase",
                "decrease",
                "scale out",
                "scale in",
            )
        ):
            return False
    return True


def _enrich_asg_rightsizing_metrics(
    adapter: CachedAWSAdapter,
    resource: Dict[str, Any],
    region: Optional[str],
    generated_at: datetime,
) -> None:
    metadata = dict(_resource_metadata(resource))
    if _asg_scope_blocked(metadata):
        resource["metadata"] = metadata
        return
    policy = ASGCapacityPolicy()
    start = generated_at - timedelta(days=max(policy.lookback_days))
    try:
        raw = adapter.get_asg_rightsizing_metrics(
            str(resource.get("resource_id") or ""),
            start,
            generated_at,
            region=region,
            period_seconds=policy.period_seconds,
        )
        memory = (raw.get("metrics") or {}).get("memory_percent") or {}
        if not memory.get("values") and _asg_stable_members_proven(
            metadata, raw, start, policy
        ):
            member_payload = adapter.get_asg_member_memory_metrics(
                list(metadata.get("in_service_instance_ids") or []),
                start,
                generated_at,
                region=region,
                period_seconds=policy.period_seconds,
            )
            aggregated = aggregate_stable_member_memory(
                member_payload, metadata.get("in_service_instance_ids") or []
            )
            if aggregated:
                raw["metrics"]["memory_percent"] = {
                    **aggregated,
                    "period_seconds": policy.period_seconds,
                    "statistic": "Average",
                }
        signals = _asg_activity_signals(metadata)
        windows, telemetry, demand = normalize_asg_metrics(
            raw, generated_at, policy, signals=signals, include_demand=True
        )
        metadata["rightsizing_metrics"] = windows
        metadata["rightsizing_demand"] = demand
        metadata["telemetry_summary"] = telemetry
        metadata["memory_metric_source"] = telemetry["memory_percent"].get(
            "source"
        )
        metadata["operational_signals"] = signals
    except Exception as exc:
        logger.warning(
            "ASG rightsizing metric enrichment failed for %s: %s",
            resource.get("resource_id"),
            exc,
        )
        metadata["rightsizing_metric_error"] = str(exc)
    resource["metadata"] = metadata


def _enrich_elasticache_rightsizing_metrics(
    adapter: AWSAdapter,
    resource: Dict[str, Any],
    region: Optional[str],
    end_date: datetime,
) -> None:
    metadata = dict(_resource_metadata(resource))
    member_ids = metadata.get("member_cluster_ids") or metadata.get("MemberClusters")
    if not isinstance(member_ids, list) or not member_ids:
        member_ids = [str(resource.get("resource_id") or "")]
    member_ids = [str(item) for item in member_ids if item]
    if not member_ids:
        return
    node_type = str(
        metadata.get("cache_node_type") or metadata.get("CacheNodeType") or ""
    )
    try:
        raw = adapter.get_elasticache_rightsizing_metrics(
            member_ids,
            end_date - timedelta(days=60),
            end_date,
            region=region,
            include_cpu_credits=node_type.startswith(("cache.t3", "cache.t4g")),
        )
    except Exception as exc:
        logger.warning(
            "ElastiCache rightsizing telemetry failed for %s: %s",
            resource.get("resource_id"),
            exc,
        )
        return
    period = int(raw.get("period_seconds") or 300)
    roles = (
        metadata.get("member_roles")
        if isinstance(metadata.get("member_roles"), dict)
        else {}
    )
    created = (
        metadata.get("member_created_at")
        if isinstance(metadata.get("member_created_at"), dict)
        else {}
    )
    per_node: Dict[str, Dict[str, Any]] = {}
    group_metric_summaries: Dict[str, List[Dict[str, Any]]] = {}
    read_rate_points: Dict[str, Dict[str, float]] = {}

    def values(
        payload: Dict[str, Any], *, rate: bool = False, mbps: bool = False
    ) -> List[float]:
        result: List[float] = []
        for value in payload.get("values") or []:
            number = _as_float(value)
            if number is None:
                continue
            result.append(
                bytes_to_mbps(number, period)
                if mbps
                else number / period
                if rate
                else number
            )
        return result

    def summary(numbers: List[float]) -> Dict[str, Any]:
        if not numbers:
            return {
                "p99": None,
                "p95": None,
                "max": None,
                "avg": None,
                "sample_count": 0,
                "period_seconds": period,
            }
        return {
            "p99": nearest_rank_percentile(numbers, 99),
            "p95": nearest_rank_percentile(numbers, 95),
            "max": max(numbers),
            "avg": sum(numbers) / len(numbers),
            "sample_count": len(numbers),
            "period_seconds": period,
        }

    def window_summary(
        payload: Dict[str, Any], *, rate: bool = False, mbps: bool = False
    ) -> Dict[str, Any]:
        raw_values = values(payload, rate=rate, mbps=mbps)
        result = summary(raw_values)
        timestamps = payload.get("timestamps") or []
        pairs: List[Tuple[datetime, float]] = []
        for timestamp, number in zip(timestamps, raw_values):
            parsed = _parse_datetime(timestamp)
            if parsed is None:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            pairs.append((parsed, number))
        window_p99 = []
        for days in (14, 30, 60):
            numbers = [
                number
                for timestamp, number in pairs
                if timestamp >= end_date - timedelta(days=days)
            ]
            percentile = nearest_rank_percentile(numbers, 99) if numbers else None
            result[f"p99_{days}d"] = percentile
            if percentile is not None:
                window_p99.append(percentile)
        if window_p99:
            result["p99"] = max(window_p99)
        return result

    aliases = {
        "engine_cpu_percent": (False, False),
        "host_cpu_percent": (False, False),
        "memory_percent": (False, False),
        "bytes_used_for_cache": (False, False),
        "freeable_memory_bytes": (False, False),
        "network_in_mbps": (False, True),
        "network_out_mbps": (False, True),
        "evictions": (False, False),
        "get_type_cmds": (True, False),
        "set_type_cmds": (True, False),
        "swap_usage_bytes": (False, False),
        "replication_lag_seconds": (False, False),
        "curr_connections": (False, False),
        "traffic_management_active": (False, False),
        "cpu_credit_balance": (False, False),
        "cpu_credit_usage": (False, False),
        "network_bw_in_allowance_exceeded": (False, False),
        "network_bw_out_allowance_exceeded": (False, False),
        "network_packets_per_second_allowance_exceeded": (False, False),
        "network_conntrack_allowance_exceeded": (False, False),
    }
    source_names = {
        "network_in_mbps": "network_in_bytes",
        "network_out_mbps": "network_out_bytes",
    }
    for member_id, node_metrics in (raw.get("per_node") or {}).items():
        node: Dict[str, Any] = {}
        for alias, (rate, mbps) in aliases.items():
            payload = node_metrics.get(source_names.get(alias, alias)) or {}
            node[alias] = window_summary(payload, rate=rate, mbps=mbps)
            group_metric_summaries.setdefault(alias, []).append(node[alias])
        role_evidence = (
            roles.get(member_id) if isinstance(roles.get(member_id), dict) else {}
        )
        is_master_values = values(node_metrics.get("is_master") or {})
        inferred_role = (
            "primary"
            if is_master_values and is_master_values[-1] >= 0.5
            else "replica"
            if is_master_values
            else "unknown"
        )
        persisted_role = str(role_evidence.get("role") or "unknown").lower()
        role = (
            inferred_role if persisted_role in {"unknown", inferred_role} else "unknown"
        )
        read_payload = node_metrics.get("get_type_cmds") or {}
        read_raw = values(read_payload)
        read_rates = [value / period for value in read_raw]
        timestamps = [
            _parse_datetime(item) for item in read_payload.get("timestamps") or []
        ]
        timestamps = [
            item.replace(tzinfo=timezone.utc) if item and item.tzinfo is None else item
            for item in timestamps
            if item
        ]
        read_rate_points[member_id] = {
            timestamp.isoformat(): rate
            for timestamp, rate in zip(timestamps, read_rates)
        }
        created_at = _parse_datetime(created.get(member_id))
        if created_at is not None and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        expected_seconds = (
            min(60 * 86400, max((end_date - created_at).total_seconds(), 0))
            if created_at is not None
            else None
        )
        expected_samples = (
            expected_seconds / period if expected_seconds is not None else None
        )
        node.update(
            {
                "role": role,
                "node_group_id": role_evidence.get("node_group_id") or "0001",
                "member_created_at": created_at.isoformat() if created_at else None,
                "engine_cpu_p99": node["engine_cpu_percent"]["p99"],
                "memory_p99": node["memory_percent"]["p99"],
                "read_ops_p99": node["get_type_cmds"]["p99"],
                "read_ops_max": max(read_rates) if read_rates else None,
                "read_ops_sum": sum(read_raw) if read_raw else None,
                "read_coverage_ratio": min(len(read_raw) / expected_samples, 1.0)
                if expected_samples
                else None,
                "read_observed_days": len(read_raw) * period / 86400,
                "read_latest_sample_age_seconds": max(
                    (end_date - max(timestamps)).total_seconds(), 0
                )
                if timestamps
                else None,
                "set_ops_p99": node["set_type_cmds"]["p99"],
            }
        )
        per_node[member_id] = node
    rightsizing: Dict[str, Any] = {}
    for alias, summaries in group_metric_summaries.items():
        # Each statistic binds independently. Sparse counters often have a
        # zero p99 even when another node has a nonzero maximum; selecting one
        # whole node summary by p99 would discard that safety signal.
        aggregate = summary([])
        for field in ("p99", "p95", "max", "avg"):
            known = [
                item.get(field) for item in summaries if item.get(field) is not None
            ]
            aggregate[field] = max(known) if known else None
        aggregate["sample_count"] = max(
            (int(item.get("sample_count") or 0) for item in summaries), default=0
        )
        for days in (14, 30, 60):
            field = f"p99_{days}d"
            known = [
                item.get(field) for item in summaries if item.get(field) is not None
            ]
            aggregate[field] = max(known) if known else None
        rightsizing[alias] = aggregate
    rightsizing["per_node"] = per_node
    if read_rate_points and all(read_rate_points.values()):
        aligned_timestamps = set.intersection(
            *(set(points) for points in read_rate_points.values())
        )
        aligned_sums = [
            sum(points[timestamp] for points in read_rate_points.values())
            for timestamp in sorted(aligned_timestamps)
        ]
        rightsizing["group_read_ops_p99"] = (
            nearest_rank_percentile(aligned_sums, 99) if aligned_sums else None
        )
    else:
        rightsizing["group_read_ops_p99"] = None
    rightsizing["observed_days"] = max(
        (node.get("read_observed_days") or 0 for node in per_node.values()), default=0
    )
    rightsizing["windows_days"] = [14, 30, 60]
    metadata["rightsizing_metrics"] = rightsizing
    metadata["telemetry_summary"] = {
        "observed_days": rightsizing["observed_days"],
        "windows_days": [14, 30, 60],
        "period_seconds": period,
    }
    resource["metadata"] = metadata


def _aws_failure_status(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        code = str(exc.response.get("Error", {}).get("Code") or "")
        if code in {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}:
            return "ACCESS_DENIED"
    return "ERROR"


def _rds_attribution_required(
    metrics: Dict[str, Any], policy: RdsRightsizerPolicy
) -> bool:
    selected = metrics.get("selected_decision_values") or {}
    windows = metrics.get("windows") or {}
    required_names = (
        "cpu_percent",
        "freeable_memory_bytes",
        "read_latency_seconds",
        "write_latency_seconds",
        "disk_queue_depth",
        "connections",
    )
    if any(
        all((windows.get(window) or {}).get(name, {}).get("status") in {"EMPTY", "INVALID"} for window in ("14d", "30d", "60d"))
        for name in required_names
    ):
        return True
    return bool(
        float(selected.get("cpu_percent") or 0) >= policy.pi_cpu_trigger_percent
        or max(float(selected.get("read_latency_seconds") or 0), float(selected.get("write_latency_seconds") or 0)) >= policy.pi_latency_trigger_seconds
        or float(selected.get("disk_queue_depth") or 0) >= policy.pi_queue_depth_trigger
        or float(selected.get("connections") or 0) >= policy.pi_connections_trigger
        or float(selected.get("swap_bytes") or 0) >= policy.swap_warning_bytes
    )


def _enrich_rds_rightsizing(
    adapter: CachedAWSAdapter,
    resource: Dict[str, Any],
    region: Optional[str],
    end_date: datetime,
) -> None:
    metadata = dict(_resource_metadata(resource))
    if rds_scope_reason(metadata, str(resource.get("state") or "")):
        resource["metadata"] = metadata
        return
    policy = RdsRightsizerPolicy()
    identifier = str(resource.get("resource_id") or "")
    try:
        try:
            orderable_options = adapter.get_rds_orderable_options(
                str(metadata.get("Engine") or ""),
                str(metadata.get("EngineVersion") or ""),
                license_model=str(metadata.get("LicenseModel") or ""),
                region=region,
            )
            orderable_status = "SUCCESS"
        except Exception as exc:
            orderable_options = []
            orderable_status = _aws_failure_status(exc)
        context = adapter.get_rds_rightsizing_context(
            metadata,
            region=region,
            orderable_options=orderable_options,
            orderable_status=orderable_status,
        )
        filtered = compatible_orderable_options(metadata, context.get("orderable_options") or [])
        context["orderable_options"] = filtered
        context["orderable_target_classes"] = sorted(
            {str(item.get("DBInstanceClass")) for item in filtered if item.get("DBInstanceClass")}
        )
        priced_classes = sorted(
            set(context["orderable_target_classes"])
            | {str(metadata.get("DBInstanceClass") or "")}
        )
        try:
            price_snapshot = adapter.get_rds_rightsizing_prices(
                metadata,
                [item for item in priced_classes if item],
                region=region,
            )
            context["pricing_status"] = price_snapshot.get("status") or "SUCCESS"
            context["class_prices"] = price_snapshot.get("class_prices") or {}
            context["storage_prices"] = price_snapshot.get("storage_prices") or {}
            context["pricing_source"] = price_snapshot.get("source")
            context["pricing_source_versions"] = price_snapshot.get("catalog_versions") or []
        except Exception as exc:
            logger.warning("RDS exact pricing lookup failed for %s: %s", identifier, exc)
            context["pricing_status"] = _aws_failure_status(exc)
            context["class_prices"] = {}
            context["storage_prices"] = {}
        context.update(
            class_capability_catalog_version="rds-class-v1",
            storage_capability_policy_version="rds-storage-v1",
            pricing_catalog_version="rds-pricing-v1",
        )
        metadata["rightsizing_context"] = context
        metadata["orderable_option_key"] = context.get("orderable_option_key")
        metadata["orderable_target_classes"] = context.get("orderable_target_classes")
        metadata["valid_storage_options"] = context.get("valid_storage_options")
        metadata["last_rds_event_at"] = context.get("last_rds_event_at")
        metadata["recent_event_categories"] = context.get("recent_event_categories")
        metadata["pending_maintenance_actions"] = context.get("pending_maintenance_actions")
    except Exception as exc:
        logger.warning("RDS rightsizing context failed for %s: %s", identifier, exc)
        metadata["rightsizing_context"] = {
            "orderable_status": _aws_failure_status(exc),
            "valid_storage_status": _aws_failure_status(exc),
            "reason_code": "RDS_RUNTIME_CONTEXT_COLLECTION_FAILED",
        }
    try:
        raw = adapter.get_rds_rightsizing_metrics(
            identifier,
            end_date - timedelta(days=60),
            end_date,
            region=region,
            period_seconds=300,
        )
        created = _parse_datetime(metadata.get("InstanceCreateTime") or metadata.get("created_at"))
        not_applicable: set[str] = set()
        db_class = str(metadata.get("DBInstanceClass") or "")
        storage_type = str(metadata.get("StorageType") or "")
        if not db_class.startswith("db.t"):
            not_applicable.update({"cpu_credit_balance", "cpu_credit_usage"})
        if storage_type != "gp2":
            not_applicable.add("burst_balance_percent")
        if not metadata.get("ReadReplicaSourceDBInstanceIdentifier"):
            not_applicable.add("replica_lag_seconds")
        metrics = normalize_cloudwatch_metrics(
            raw,
            end_date,
            resource_created_at=created,
            not_applicable=not_applicable,
        )
        required = _rds_attribution_required(metrics, policy)
        metrics["performance_insights"]["required"] = required
        if not required:
            metrics["performance_insights"]["status"] = "NOT_NEEDED"
        elif not performance_insights_supported(
            metadata,
            (metadata.get("rightsizing_context") or {}).get("orderable_options") or [],
        ):
            metrics["performance_insights"]["status"] = "UNSUPPORTED"
        elif not metadata.get("PerformanceInsightsEnabled"):
            metrics["performance_insights"]["status"] = "DISABLED"
        elif not metadata.get("DbiResourceId"):
            metrics["performance_insights"]["status"] = "UNSUPPORTED"
        else:
            try:
                pi_raw = adapter.get_rds_performance_insights_metrics(
                    str(metadata["DbiResourceId"]),
                    end_date - timedelta(
                        days=min(
                            policy.attribution_default_days,
                            policy.attribution_max_days,
                        )
                    ),
                    end_date,
                    region=region,
                    period_seconds=300,
                )
                metrics["performance_insights"] = normalize_performance_insights(pi_raw)
            except Exception as exc:
                metrics["performance_insights"].update(
                    status=_aws_failure_status(exc),
                    required=True,
                )
        rightsizing = dict(metadata.get("rightsizing_metrics") or {})
        rightsizing["rds_v1"] = metrics
        metadata["rightsizing_metrics"] = rightsizing
    except Exception as exc:
        logger.warning("RDS rightsizing metrics failed for %s: %s", identifier, exc)
        rightsizing = dict(metadata.get("rightsizing_metrics") or {})
        rightsizing["rds_v1"] = {
            "generated_at": end_date.isoformat(),
            "period_seconds": 300,
            "collection": {
                "status": _aws_failure_status(exc),
                "reason_code": "RDS_CLOUDWATCH_COLLECTION_FAILED",
            },
            "windows": {},
            "selected_decision_values": {},
            "performance_insights": {"status": "NOT_NEEDED", "required": False},
            "normalization_version": "rds-v1-exact",
        }
        metadata["rightsizing_metrics"] = rightsizing
    resource["metadata"] = metadata


def _metric_points(payload: Dict[str, Any], cutoff: datetime) -> Dict[str, float]:
    points: Dict[str, float] = {}
    timestamps = (
        payload.get("timestamps") if isinstance(payload.get("timestamps"), list) else []
    )
    values = payload.get("values") if isinstance(payload.get("values"), list) else []
    for timestamp, value in zip(timestamps, values):
        parsed = _parse_datetime(timestamp)
        if parsed is None:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed >= cutoff:
            number = _as_float(value)
            if number is not None:
                points[parsed.isoformat()] = number
    return points


def _rate_summary(points: Dict[str, float]) -> Dict[str, Optional[float]]:
    values = list(points.values())
    return {
        "p95": nearest_rank_percentile(values, 95.0),
        "p99": nearest_rank_percentile(values, 99.0),
        "maximum": max(values) if values else None,
        "sample_count": len(values),
    }


def _gpu_demand_from_device_series(
    device_payloads: Dict[str, Any],
    cutoff: datetime,
    policy: EC2GpuTelemetryPolicy,
) -> tuple[Dict[str, int], Dict[str, float], Dict[str, float], dict[str, Any]]:
    """Compute aligned GPU demand and report the devices that contribute to it.

    A device with only one of ``utilization_gpu`` or ``memory_used`` is unknown,
    not idle. It is excluded from the demand math so the completeness guard can
    defer the recommendation instead of treating incomplete telemetry as full
    device coverage. Empty or absent series therefore produce zero observed
    devices and empty demand evidence.
    """
    devices: dict[str, tuple[Dict[str, float], Dict[str, float]]] = {}
    for device_index, payload in device_payloads.items():
        if not isinstance(payload, dict):
            continue
        utilization = _metric_points(payload.get("utilization_gpu") or {}, cutoff)
        memory_used = _metric_points(payload.get("memory_used") or {}, cutoff)
        if utilization and memory_used:
            devices[str(device_index)] = (utilization, memory_used)
    observed_count = len(devices)
    all_timestamps = set()
    aligned_timestamps = None
    for utilization, memory_used in devices.values():
        timestamps = set(utilization) & set(memory_used)
        all_timestamps.update(set(utilization) | set(memory_used))
        aligned_timestamps = (
            timestamps
            if aligned_timestamps is None
            else aligned_timestamps & timestamps
        )
    aligned_timestamps = aligned_timestamps or set()
    required_devices: Dict[str, int] = {}
    vram_peaks: Dict[str, float] = {}
    busiest_utilization: Dict[str, float] = {}
    for timestamp in sorted(aligned_timestamps):
        busy_count = 0
        memory_values: list[float] = []
        utilization_values: list[float] = []
        for utilization, memory_used in devices.values():
            gpu_utilization = utilization[timestamp]
            used_mib = memory_used[timestamp]
            if (
                gpu_utilization > policy.busy_threshold
                or used_mib > policy.vram_floor_mib
            ):
                busy_count += 1
            memory_values.append(used_mib)
            utilization_values.append(gpu_utilization)
        required_devices[timestamp] = busy_count
        vram_peaks[timestamp] = max(memory_values)
        busiest_utilization[timestamp] = max(utilization_values)
    evidence = {
        "device_count_observed": observed_count,
        "aligned_sample_count": len(aligned_timestamps),
        "dropped_sample_count": len(all_timestamps - aligned_timestamps),
        "semantics": "all_devices_reported",
    }
    return required_devices, vram_peaks, busiest_utilization, evidence


def _enrich_ec2_rightsizing_metrics(
    adapter: CachedAWSAdapter,
    resource: Dict[str, Any],
    region: Optional[str],
    end_date: datetime,
) -> None:
    """Persist exact normalized telemetry for later read-only evaluation."""
    resource_id = str(resource.get("resource_id") or "").strip()
    if not resource_id:
        return
    try:
        raw = adapter.get_ec2_rightsizing_metrics(
            resource_id,
            end_date - timedelta(days=60),
            end_date,
            region=region,
            period_seconds=300,
        )
    except Exception as exc:
        logger.warning(
            "EC2 rightsizing metric enrichment failed for %s: %s", resource_id, exc
        )
        return
    metrics = raw.get("metrics") if isinstance(raw, dict) else {}
    if not isinstance(metrics, dict):
        return
    period = int(raw.get("period_seconds") or 300)
    metadata = _resource_metadata(resource)
    gpu_device_count_discovered = 0
    if "gpu_metric_status" in raw:
        metadata["gpu_metric_status"] = raw.get("gpu_metric_status")
        metadata["gpu_metric_unavailable_reason"] = raw.get(
            "gpu_metric_unavailable_reason"
        )
        metadata["gpu_metric_source"] = raw.get("gpu_metric_source")
        metadata["gpu_memory_metric_source"] = raw.get("gpu_memory_metric_source")
        metadata["gpu_memory_total_metric_source"] = raw.get(
            "gpu_memory_total_metric_source"
        )
        gpu_device_count_discovered = int(
            raw.get("gpu_device_count_observed") or 0
        )
    memory_payload = metrics.get("memory_percent")
    if isinstance(memory_payload, dict):
        if memory_payload.get("source"):
            metadata["memory_metric_source"] = memory_payload["source"]
        memory_points: Dict[str, float] = {}
        for lookback_days in (14, 30, 60):
            memory_points = _metric_points(
                memory_payload, end_date - timedelta(days=lookback_days)
            )
            if memory_points:
                break
        if memory_points:
            ordered_memory_points = sorted(
                memory_points.items(), key=lambda item: item[0], reverse=True
            )
            metadata["avg_memory_utilization"] = sum(
                value for _, value in ordered_memory_points
            ) / len(ordered_memory_points)
            metric_history = dict(metadata.get("metric_history") or {})
            metric_history["memoryutilization"] = {
                "timestamps": [timestamp for timestamp, _ in ordered_memory_points],
                "average": [value for _, value in ordered_memory_points],
                "maximum": [],
                "p90": [],
                "p95": [],
                "p99": [],
            }
            metadata["metric_history"] = metric_history
            metadata["memory_metric_status"] = "usable"
        else:
            metadata["memory_metric_status"] = "unavailable"
    windows: Dict[str, Any] = {}
    gpu_devices = metrics.get("gpu_devices")
    has_gpu_series = isinstance(gpu_devices, dict) and bool(gpu_devices)
    for days in (14, 30, 60):
        cutoff = end_date - timedelta(days=days)
        raw_points = {
            key: _metric_points(payload, cutoff)
            for key, payload in metrics.items()
            if isinstance(payload, dict)
        }
        normalized_points: Dict[str, Dict[str, float]] = {}

        def convert(source: str, target: str, converter: Any) -> None:
            normalized_points[target] = {
                timestamp: converter(value, period)
                for timestamp, value in raw_points.get(source, {}).items()
            }

        normalized_points["cpu_percent"] = raw_points.get("cpu_percent", {})
        normalized_points["memory_percent"] = raw_points.get("memory_percent", {})
        convert("network_in_bytes", "network_in_mbps", bytes_to_mbps)
        convert("network_out_bytes", "network_out_mbps", bytes_to_mbps)
        convert("network_packets_in", "network_in_pps", packets_to_pps)
        convert("network_packets_out", "network_out_pps", packets_to_pps)
        convert("ebs_read_operations", "ebs_read_iops", operations_to_iops)
        convert("ebs_write_operations", "ebs_write_iops", operations_to_iops)
        convert("ebs_read_bytes", "ebs_read_mibps", bytes_to_mibps)
        convert("ebs_write_bytes", "ebs_write_mibps", bytes_to_mibps)

        combination_evidence: Dict[str, Dict[str, Any]] = {}

        def combine(left: str, right: str, target: str) -> Dict[str, float]:
            left_points, right_points = normalized_points.get(
                left, {}
            ), normalized_points.get(right, {})
            timestamps = left_points.keys() | right_points.keys()
            paired = left_points.keys() & right_points.keys()
            combination_evidence[target] = {
                "left_sample_count": len(left_points),
                "right_sample_count": len(right_points),
                "paired_sample_count": len(paired),
                "unpaired_sample_count": len(timestamps - paired),
                "semantics": "observed_component_sum_lower_bound",
            }
            # An unpaired point is retained as observed demand from one
            # direction. The absent component is not asserted to be zero; the
            # incomplete pairing is recorded and raises telemetry uncertainty.
            return {
                timestamp: left_points.get(timestamp, 0.0)
                + right_points.get(timestamp, 0.0)
                for timestamp in timestamps
            }

        normalized_points["ebs_combined_iops"] = combine(
            "ebs_read_iops", "ebs_write_iops", "ebs_combined_iops"
        )
        normalized_points["ebs_combined_mibps"] = combine(
            "ebs_read_mibps", "ebs_write_mibps", "ebs_combined_mibps"
        )
        aggregation_evidence: Dict[str, Any] = dict(combination_evidence)
        if has_gpu_series:
            (
                gpu_required_devices,
                gpu_vram_peaks,
                gpu_busiest_utilization,
                gpu_evidence,
            ) = _gpu_demand_from_device_series(
                gpu_devices, cutoff, EC2_GPU_TELEMETRY_POLICY
            )
            gpu_evidence["gpu_device_count_discovered"] = (
                gpu_device_count_discovered
            )
            aggregation_evidence["gpu_devices"] = gpu_evidence
            total_values = []
            for payload in gpu_devices.values():
                if not isinstance(payload, dict):
                    continue
                total_points = _metric_points(
                    payload.get("memory_total") or {}, cutoff
                )
                total_values.extend(total_points.values())
            if total_values:
                catalog_memory = _as_float(
                    metadata.get("gpu_memory_mib_per_device_catalog")
                )
                aggregation_evidence["gpu_memory_total"] = {
                    "maximum_mib": max(total_values),
                    "sample_count": len(total_values),
                    "catalog_memory_mib_per_device": catalog_memory,
                    "catalog_mismatch_evidence": (
                        catalog_memory is not None
                        and any(value != catalog_memory for value in total_values)
                    ),
                    "semantics": "consistency_evidence_only",
                }
        else:
            gpu_required_devices = {}
            gpu_vram_peaks = {}
            gpu_busiest_utilization = {}
        signal_names = (
            "bw_in_allowance_exceeded",
            "bw_out_allowance_exceeded",
            "pps_allowance_exceeded",
            "conntrack_allowance_exceeded",
            "instance_ebs_iops_exceeded",
            "instance_ebs_throughput_exceeded",
        )
        signals = {
            name: any(value > 0 for value in raw_points[name].values())
            for name in signal_names
            if raw_points.get(name)
        }
        balance_values = list(
            raw_points.get("ebs_io_balance_percent", {}).values()
        ) + list(raw_points.get("ebs_byte_balance_percent", {}).values())
        if balance_values:
            signals["ebs_burst_balance_depleted"] = min(balance_values) < 20.0
        if any(
            item["unpaired_sample_count"] > 0 for item in combination_evidence.values()
        ):
            signals["ebs_directional_metrics_incomplete"] = True
        windows[f"{days}d"] = {
            "lookback_days": days,
            "period_seconds": period,
            "normalized": {
                name: _rate_summary(points)
                for name, points in normalized_points.items()
            },
            "signals": signals,
            "aggregation_evidence": aggregation_evidence,
            "normalization_version": "ec2-v1-exact",
        }
        if has_gpu_series:
            windows[f"{days}d"]["normalized"]["gpu_required_devices"] = {
                "maximum": max(gpu_required_devices.values())
                if gpu_required_devices
                else None,
                "sample_count": len(gpu_required_devices),
            }
            windows[f"{days}d"]["normalized"]["gpu_vram_used_mib"] = _rate_summary(
                gpu_vram_peaks
            )
            windows[f"{days}d"]["normalized"][
                "gpu_percent_busiest_device"
            ] = _rate_summary(gpu_busiest_utilization)
    if has_gpu_series:
        # The 60-day decision window is the completeness guard's source of
        # truth. Persist the count that actually contributed to aligned demand,
        # not the adapter's discovery count, so partial device telemetry defers.
        metadata["gpu_device_count_observed"] = int(
            windows["60d"]["aggregation_evidence"]["gpu_devices"][
                "device_count_observed"
            ]
        )
    elif "gpu_metric_status" in raw:
        metadata["gpu_device_count_observed"] = 0
    metadata["rightsizing_metrics"] = windows
    resource["metadata"] = metadata


def _next_inventory_id(db: Session, model: Type[Any]) -> int:
    current = db.query(func.max(model.inventory_id)).scalar()
    return int(current or 0) + 1


def _inventory_resource_key(
    resource_type: str, resource_id: str, region: Optional[str] = None
) -> str:
    if resource_type in {"asg", "sagemaker"}:
        return f"{resource_type}:{region or ''}:{resource_id}"
    return f"{resource_type}:{resource_id}"


def _get_or_create_inventory_row(
    db: Session,
    model: Type[Any],
    resource_id: str,
    *,
    account_id: Optional[str] = None,
    region: Optional[str] = None,
) -> Any:
    query = db.query(model).filter(model.resource_id == resource_id)
    if model in {AsgInventory, SageMakerInventory}:
        query = query.filter(
            model.account_id == account_id,
            model.region == region,
        )
    existing = query.first()
    if existing:
        return existing
    values: Dict[str, Any] = {
        "inventory_id": _next_inventory_id(db, model),
        "resource_id": resource_id,
    }
    if model is AsgInventory:
        values.update(
            account_id=account_id,
            region=region,
            min_size=0,
            desired_capacity=0,
            max_size=0,
        )
    elif model is SageMakerInventory:
        values.update(account_id=account_id, region=region, resource_subtype="unknown")
    row = model(**values)
    db.add(row)
    db.flush()
    return row


def _build_pricing_context(
    check_id: str, resources: List[Dict[str, Any]], db: Session
) -> Optional[PricingContext]:
    pricing_metadata = pricing_registry.get_pricing(check_id)
    if not pricing_metadata:
        return None
    return PricingContext(
        check_id=check_id,
        resources=resources,
        db=db,
        savings_ratio=pricing_metadata.parameters.get("savings_ratio"),
        full_savings=pricing_metadata.parameters.get("full_savings"),
        metadata={"note": pricing_metadata.parameters.get("note")}
        if pricing_metadata.parameters.get("note")
        else None,
    )


def _apply_pricing(
    db: Session, check_id: str, resources: List[Dict[str, Any]]
) -> float:
    if not resources:
        return 0.0

    pricing_service = AwsPricingCacheService()
    pricing_map = pricing_service.get_prices_for_resources(db, resources)
    for resource in resources:
        resource_id = resource.get("resource_id")
        if resource_id and resource_id in pricing_map:
            resource["pricing"] = pricing_map[resource_id]
            metadata = resource.get("metadata")
            if isinstance(metadata, dict):
                metadata["pricing"] = pricing_map[resource_id]

    pricing_context = _build_pricing_context(check_id, resources, db)
    if pricing_context:
        pricing_registry.apply_pricing(pricing_context)

    return sum(
        (resource.get("metadata") or {}).get(
            "potential_savings_yearly",
            ((resource.get("metadata") or {}).get("potential_savings_monthly", 0) or 0)
            * 12,
        )
        or 0
        for resource in resources
    )


def _store_inventory_resource(
    db: Session,
    resource_type: str,
    resource: Dict[str, Any],
    account_id: Optional[str],
    generated_at: datetime,
    s3_price_map: Optional[Dict[str, Any]] = None,
    s3_optimizer_metadata: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    if resource_type == "elasticache" and is_elasticache_member_cluster(resource):
        return None
    model = INVENTORY_MODELS.get(resource_type)
    resource_id = str(resource.get("resource_id") or "").strip()
    if not model or not resource_id:
        return None

    resolved_account_id = resource.get("account_id") or account_id
    resolved_region = resource.get("region")
    if resource_type == "asg" and (
        not str(resolved_account_id or "").strip()
        or not str(resolved_region or "").strip()
    ):
        raise ValueError(
            "ASG inventory requires non-empty account_id and region identity"
        )
    row = _get_or_create_inventory_row(
        db,
        model,
        resource_id,
        account_id=resolved_account_id,
        region=resolved_region,
    )
    metadata = _resource_metadata(resource)
    if resource_type == "s3" and s3_optimizer_metadata is None:
        existing_metadata = row.metadata_json
        if isinstance(existing_metadata, dict):
            for key in OPTIMIZER_METADATA_KEYS:
                if key not in metadata and key in existing_metadata:
                    metadata[key] = copy.deepcopy(existing_metadata[key])
    if resource_type == "s3" and s3_optimizer_metadata is not None:
        # Enrichment runs before the ORM row exists on a first scan.  Merge
        # its complete additive shape here so creation and update use one
        # persistence path instead of relying on a later backfill.
        metadata.update(s3_optimizer_metadata)
        optimizer_result = metadata.get("s3_optimizer")
        if isinstance(optimizer_result, dict):
            optimizer_result["inventory_id"] = int(row.inventory_id)
    tags = resource.get("tags") or metadata.get("tags") or {}

    row.resource_id = resource_id
    row.resource_name = (
        resource.get("resource_name") or metadata.get("name") or resource_id
    )
    row.resource_type = resource_type
    row.account_id = resolved_account_id
    row.region = resource.get("region")
    row.state = resource.get("state") or metadata.get("state")
    row.generated_at = generated_at
    row.tags_json = _serialize_metadata(tags)
    row.metadata_json = _serialize_metadata(metadata)
    row.aws_payload_json = _serialize_metadata(resource)
    row.source_path = "aws-scan"

    if resource_type == "ec2":
        monthly_cost = estimate_baseline_monthly_cost(resource_type, resource)
        metadata["monthly_cost_estimate"] = round(monthly_cost, 4)
        resource["metadata"] = metadata
        row.availability_zone = resource.get("availability_zone") or metadata.get(
            "availability_zone"
        )
        row.instance_type = resource.get("instance_type") or metadata.get(
            "instance_type"
        )
        row.usage_profile = metadata.get("usage_profile")
        row.launch_time = _parse_datetime(
            resource.get("launch_time") or metadata.get("launch_time")
        )
        row.avg_cpu_utilization = _as_float(metadata.get("avg_cpu_utilization"))
        row.avg_memory_utilization = _as_float(metadata.get("avg_memory_utilization"))
        row.avg_network_in = _as_float(metadata.get("avg_network_in"))
        row.avg_network_out = _as_float(metadata.get("avg_network_out"))
        row.monthly_cost_estimate = monthly_cost
        row.metric_history_json = _serialize_metadata(metadata.get("metric_history"))
    elif resource_type == "s3":
        if metadata.get("estimated_monthly_cost") is None:
            storage_estimate = estimate_s3_storage_cost(
                resource_id,
                resource.get("region") or "us-east-1",
                price_map=(s3_price_map or {}).get("resolved") or None,
            )
            metadata["bucket_size_gb"] = round(
                storage_estimate.get("bucket_size_gb") or 0.0, 4
            )
            metadata["object_count"] = int(storage_estimate.get("object_count") or 0)
            metadata["estimated_monthly_cost"] = round(
                storage_estimate.get("estimated_monthly_cost") or 0.0, 4
            )
            resource["metadata"] = metadata
        row.creation_date = _parse_datetime(
            resource.get("creation_date") or metadata.get("creation_date")
        )
        row.versioning_status = metadata.get("versioning_status")
        row.logging_enabled = str(bool(metadata.get("logging_enabled"))).lower()
        row.inventory_configuration_count = _as_int(
            metadata.get("inventory_configuration_count")
        )
        row.replication_rule_count = _as_int(metadata.get("replication_rule_count"))
        row.bucket_size_gb = _as_float(metadata.get("bucket_size_gb"))
        row.object_count = _as_int(metadata.get("object_count"))
        row.estimated_monthly_cost = _first_float(
            metadata,
            ("estimated_monthly_cost", "monthly_cost_estimate", "monthly_cost"),
        )
    elif resource_type == "rds":
        monthly_cost = estimate_baseline_monthly_cost(resource_type, resource)
        metadata["monthly_cost_estimate"] = round(monthly_cost, 4)
        resource["metadata"] = metadata
        row.availability_zone = resource.get("availability_zone") or metadata.get(
            "availability_zone"
        )
        row.engine = resource.get("engine") or metadata.get("engine")
        row.db_instance_class = resource.get("db_instance_class") or metadata.get(
            "instance_class"
        )
        row.created_at_source = _parse_datetime(
            resource.get("created_at") or metadata.get("created_at")
        )
        row.avg_cpu_utilization = _as_float(metadata.get("avg_cpu_utilization"))
        row.avg_connections = _as_float(metadata.get("avg_connections"))
        row.avg_read_iops = _as_float(metadata.get("avg_read_iops"))
        row.avg_write_iops = _as_float(metadata.get("avg_write_iops"))
        row.monthly_cost_estimate = monthly_cost
        row.metric_history_json = _serialize_metadata(metadata.get("metric_history"))
    elif resource_type == "ebs":
        row.availability_zone = resource.get("availability_zone") or metadata.get(
            "availability_zone"
        )
        row.volume_type = metadata.get("volume_type") or resource.get("volume_type")
        row.attached = str(
            bool(resource.get("attached") or metadata.get("attached"))
        ).lower()
        row.size_gb = _as_float(metadata.get("size") or resource.get("size_gb"))
        row.iops = _as_int(metadata.get("iops") or resource.get("iops"))
        row.throughput = _as_int(
            metadata.get("throughput") or resource.get("throughput")
        )
        row.avg_iops = _as_float(metadata.get("avg_iops"))
        row.avg_throughput_mb = _as_float(metadata.get("avg_throughput_mb"))
        monthly_cost = estimate_baseline_monthly_cost(resource_type, resource)
        metadata["monthly_cost_estimate"] = round(monthly_cost, 4)
        resource["metadata"] = metadata
        row.monthly_cost_estimate = monthly_cost
        row.metric_history_json = _serialize_metadata(metadata.get("metric_history"))
    elif resource_type == "dynamodb":
        read_capacity_units = _as_float(metadata.get("read_capacity_units"))
        write_capacity_units = _as_float(metadata.get("write_capacity_units"))
        billing_mode = metadata.get("BillingMode") or metadata.get("billing_mode")
        if not billing_mode and (
            (read_capacity_units or 0.0) > 0 or (write_capacity_units or 0.0) > 0
        ):
            billing_mode = "PROVISIONED"
        monthly_cost = estimate_baseline_monthly_cost(resource_type, resource)
        metadata["monthly_cost_estimate"] = round(monthly_cost, 4)
        resource["metadata"] = metadata
        row.billing_mode = billing_mode
        row.table_name = (
            metadata.get("TableName") or resource.get("resource_name") or resource_id
        )
        row.table_class = metadata.get("table_class")
        row.item_count = _as_int(metadata.get("item_count"))
        row.read_capacity_units = read_capacity_units
        row.write_capacity_units = write_capacity_units
        row.monthly_cost_estimate = monthly_cost
        row.metric_history_json = _serialize_metadata(metadata.get("metric_history"))
    elif resource_type == "elasticache":
        monthly_cost = estimate_baseline_monthly_cost(resource_type, resource)
        metadata["monthly_cost_estimate"] = round(monthly_cost, 4)
        resource["metadata"] = metadata
        row.availability_zone = resource.get("availability_zone") or metadata.get(
            "availability_zone"
        )
        row.engine = metadata.get("engine") or metadata.get("Engine")
        row.engine_version = metadata.get("engine_version") or metadata.get(
            "EngineVersion"
        )
        row.cache_node_type = metadata.get("cache_node_type") or metadata.get(
            "CacheNodeType"
        )
        row.monthly_cost_estimate = monthly_cost
        row.avg_curritems = _as_float(metadata.get("avg_curritems"))
        row.avg_keycount = _as_float(metadata.get("avg_keycount"))
        row.metric_history_json = _serialize_metadata(metadata.get("metric_history"))
    elif resource_type == "asg":
        row.min_size = int(resource.get("min_size") or metadata.get("min_size") or 0)
        row.desired_capacity = int(
            resource.get("desired_capacity")
            or metadata.get("desired_capacity")
            or 0
        )
        row.max_size = int(resource.get("max_size") or metadata.get("max_size") or 0)
        row.instance_type = resource.get("instance_type") or metadata.get(
            "instance_type"
        )
        row.platform_normalized = resource.get(
            "platform_normalized"
        ) or metadata.get("platform_normalized")
    elif resource_type == "sagemaker":
        row.resource_type = "sagemaker"
        row.resource_subtype = resource.get("resource_subtype") or metadata.get(
            "resource_subtype"
        ) or {
            "sagemaker_notebook": "notebook",
            "sagemaker_endpoint": "endpoint",
            "sagemaker_training_job": "training_job",
        }.get(str(resource.get("aws_resource_type") or ""), "unknown")
        row.instance_type = resource.get("instance_type") or metadata.get("instance_type")
        row.instance_count = _as_int(resource.get("instance_count") or metadata.get("instance_count"))
        row.status = resource.get("status") or resource.get("state") or metadata.get("status")
        row.created_at = _parse_datetime(
            resource.get("created_at") or metadata.get("creation_time")
        )
        row.metric_history_json = _serialize_metadata(metadata.get("metric_history"))

    row.metadata_json = _serialize_metadata(metadata)
    row.aws_payload_json = _serialize_metadata(resource)
    sync_resource_tags(
        db,
        resource_type=resource_type,
        inventory_id=int(row.inventory_id),
        resource_id=resource_id,
        raw_tags=row.tags_json,
    )
    return int(row.inventory_id)


def _collect_inventory(
    db: Session,
    resource_types: Iterable[str],
    settings: AccountSettings,
    generated_at: datetime,
    adapter_cache: Dict[Optional[str], CachedAWSAdapter],
    execution: Optional[OnboardingExecution] = None,
    overlay_store: Optional[MetadataOverlayStore] = None,
) -> Dict[str, Any]:
    account_id, _ = get_settings_scope(settings)
    regions = get_settings_regions(settings)
    resource_map: Dict[str, int] = {}
    inventory_counts: Dict[str, int] = {}
    errors: List[Dict[str, str]] = []

    scoped_resource_types = list(resource_types)
    for resource_index, resource_type in enumerate(scoped_resource_types, start=1):
        canonical_type = _canonical_resource_type(resource_type)
        if canonical_type not in SUPPORTED_INVENTORY_TYPES:
            continue

        collected: List[Dict[str, Any]] = []
        for region in _resource_regions(canonical_type, regions):
            for call_type in _inventory_call_types(canonical_type):
                _set_scan_progress(
                    db,
                    execution,
                    phase="inventory",
                    message=f"Collecting {canonical_type.upper()} inventory in {region or 'global'}",
                    current_region=region,
                    current_resource_type=canonical_type,
                    inventory_counts=inventory_counts,
                )
                try:
                    adapter = _get_cached_adapter(adapter_cache, region, overlay_store)
                    resources = adapter.get_resources(call_type, region=region)
                except Exception as exc:
                    logger.warning(
                        "Inventory collection failed for %s in %s: %s",
                        call_type,
                        region,
                        exc,
                    )
                    errors.append(
                        {
                            "resource_type": call_type,
                            "region": region or "",
                            "error": str(exc),
                        }
                    )
                    continue

                for resource in resources:
                    normalized = dict(resource)
                    normalized["resource_type"] = canonical_type
                    normalized.setdefault("region", region)
                    if account_id and not normalized.get("account_id"):
                        normalized["account_id"] = account_id
                    if not _resource_in_selected_regions(normalized, regions):
                        continue
                    collected.append(normalized)

        deduped = _dedupe_resources(collected)
        ec2_catalog = EC2InstanceCatalog() if canonical_type == "ec2" else None
        ec2_catalog_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}
        inventory_counts[canonical_type] = len(deduped)
        _set_scan_progress(
            db,
            execution,
            phase="inventory",
            message=(
                f"Persisting {len(deduped)} {canonical_type.upper()} resources "
                f"({resource_index}/{len(scoped_resource_types)})"
            ),
            current_resource_type=canonical_type,
            inventory_counts=inventory_counts,
        )
        s3_price_maps: Dict[str, Dict[str, Any]] = {}
        s3_optimizer_metadata: Dict[str, Dict[str, Any]] = {}
        if canonical_type == "s3" and is_s3_optimizer_enabled():
            buckets_by_region: Dict[str, List[Dict[str, Any]]] = {}
            for bucket in deduped:
                bucket_region = str(bucket.get("region") or "us-east-1")
                buckets_by_region.setdefault(bucket_region, []).append(bucket)
            for bucket_region, region_buckets in buckets_by_region.items():
                region_adapter = _get_cached_adapter(
                    adapter_cache, bucket_region, overlay_store
                )
                enrichment = enrich_s3_buckets(
                    db,
                    region_adapter,
                    region_buckets,
                    bucket_region,
                    DEFAULT_CACHE_ROOT,
                    {"window_days": 90},
                )
                s3_price_maps[bucket_region] = enrichment
                s3_optimizer_metadata.update(enrichment.get("metadata_by_bucket", {}))
        for resource in deduped:
            if canonical_type == "ec2":
                metric_adapter = _get_cached_adapter(
                    adapter_cache, resource.get("region"), overlay_store
                )
                if ec2_catalog is not None:
                    _enrich_ec2_instance_store_capability(
                        resource, ec2_catalog, ec2_catalog_cache
                    )
                _enrich_ec2_rightsizing_metrics(
                    metric_adapter, resource, resource.get("region"), generated_at
                )
            elif canonical_type == "elasticache":
                metric_adapter = _get_cached_adapter(
                    adapter_cache, resource.get("region"), overlay_store
                )
                _enrich_elasticache_rightsizing_metrics(
                    metric_adapter,
                    resource,
                    resource.get("region"),
                    generated_at,
                )
            elif canonical_type == "rds":
                metric_adapter = _get_cached_adapter(
                    adapter_cache, resource.get("region"), overlay_store
                )
                _enrich_rds_rightsizing(
                    metric_adapter,
                    resource,
                    resource.get("region"),
                    generated_at,
                )
            elif canonical_type == "asg":
                metric_adapter = _get_cached_adapter(
                    adapter_cache, resource.get("region"), overlay_store
                )
                _enrich_asg_rightsizing_metrics(
                    metric_adapter,
                    resource,
                    resource.get("region"),
                    generated_at,
                )
            inventory_id = _store_inventory_resource(
                db,
                canonical_type,
                resource,
                account_id,
                generated_at,
                s3_price_map=s3_price_maps.get(str(resource.get("region") or "us-east-1")),
                s3_optimizer_metadata=s3_optimizer_metadata.get(
                    str(resource.get("resource_id") or "")
                ),
            )
            if inventory_id is not None:
                resource_id = str(resource.get("resource_id") or "")
                resource_map[
                    _inventory_resource_key(
                        canonical_type, resource_id, resource.get("region")
                    )
                ] = inventory_id

    db.flush()
    return {
        "resource_map": resource_map,
        "inventory_counts": inventory_counts,
        "errors": errors,
    }


def _execution_regions_for_check(
    check_resource_type: str, regions: List[str]
) -> List[str]:
    canonical_type = _canonical_resource_type(check_resource_type)
    if canonical_type == "s3":
        return [regions[0] if regions else "us-east-1"]
    return regions


def _parameters_for_region(
    check: Any, parameters: Dict[str, Any], region: str
) -> Dict[str, Any]:
    if not check:
        return parameters
    try:
        signature = inspect.signature(check.check_function)
    except (AttributeError, TypeError, ValueError):
        return parameters

    accepts_region = "region" in signature.parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    if not accepts_region:
        return parameters

    return {**parameters, "region": region}


def _execute_check_across_settings(
    check_id: str,
    parameters: Optional[Dict[str, Any]],
    settings: AccountSettings,
    check_resource_type: str,
    adapter_cache: Dict[Optional[str], CachedAWSAdapter],
    db: Session,
    execution: Optional[OnboardingExecution] = None,
    check_name: Optional[str] = None,
    check_index: Optional[int] = None,
    total_checks: Optional[int] = None,
    overlay_store: Optional[MetadataOverlayStore] = None,
) -> List[Dict[str, Any]]:
    check = check_registry.get_check(check_id)
    if not check:
        logger.warning(
            "Skipping unknown policy check %s during scan; its policy may be stale",
            check_id,
        )
        return []

    account_id, _ = get_settings_scope(settings)
    regions = _execution_regions_for_check(
        check_resource_type, get_settings_regions(settings)
    )
    if not regions:
        return []

    resolved_parameters = get_effective_check_parameters(db, check_id, parameters)
    resources: List[Dict[str, Any]] = []
    for region in regions:
        _set_scan_progress(
            db,
            execution,
            phase="policies",
            message=f"Running {check_name or check_id} in {region}",
            current_region=region,
            current_resource_type=_canonical_resource_type(check_resource_type),
            current_check_id=check_id,
            current_check_name=check_name,
            check_index=check_index,
            total_checks=total_checks,
        )
        adapter = _get_cached_adapter(adapter_cache, region, overlay_store)
        region_parameters = _parameters_for_region(check, resolved_parameters, region)
        region_resources = check_registry.execute_check(
            check_id=check_id,
            aws_adapter=adapter,
            parameters=region_parameters,
        )
        for resource in region_resources:
            normalized = dict(resource)
            normalized["resource_type"] = _canonical_resource_type(
                str(normalized.get("resource_type") or "")
            )
            normalized.setdefault("region", region)
            if account_id and not normalized.get("account_id"):
                normalized["account_id"] = account_id
            if not _resource_in_selected_regions(
                normalized, get_settings_regions(settings)
            ):
                continue
            resources.append(normalized)

    return _dedupe_resources(resources)


def _store_maxops_finding(
    db: Session,
    check: Any,
    resource: Dict[str, Any],
    inventory_id: int,
    generated_at: datetime,
) -> None:
    resource_type = _canonical_resource_type(check.resource_type)
    existing = (
        db.query(MaxOpsInventory)
        .filter(
            MaxOpsInventory.resource_type == resource_type,
            MaxOpsInventory.inventory_id == inventory_id,
        )
        .first()
    )
    if not existing:
        existing = MaxOpsInventory(
            resource_type=resource_type, inventory_id=inventory_id
        )
        db.add(existing)

    metadata = _resource_metadata(resource)
    monthly_savings = _as_float(metadata.get("potential_savings_monthly"))
    yearly_savings = _as_float(metadata.get("potential_savings_yearly"))
    if metadata.get("savings_period") == "monthly":
        yearly_savings = None
    elif yearly_savings is None and monthly_savings is not None:
        yearly_savings = monthly_savings * 12

    existing.resource_id = resource.get("resource_id")
    existing.resource_name = resource.get("resource_name") or resource.get(
        "resource_id"
    )
    existing.account_id = resource.get("account_id")
    existing.region = resource.get("region")
    existing.generated_at = generated_at
    existing.check_id = check.check_id
    existing.finding_type = check.check_id
    existing.title = metadata.get("title") or check.name
    existing.description = metadata.get("description") or check.description
    existing.severity = metadata.get("severity") or "medium"
    existing.confidence_score = _as_float(metadata.get("confidence_score"))
    existing.risk_score = _as_float(metadata.get("risk_score"))
    existing.recommended_action = (
        metadata.get("recommended_action") or check.default_action
    )
    existing.recommended_actions_json = _serialize_metadata(
        metadata.get("recommended_actions")
    )
    existing.available_actions_json = _serialize_metadata(
        metadata.get("available_actions")
    )
    existing.potential_savings_monthly = monthly_savings
    existing.potential_savings_yearly = yearly_savings
    existing.evidence_json = _serialize_metadata(metadata.get("evidence"))
    existing.current_config_json = _serialize_metadata(metadata.get("current_config"))
    existing.target_config_json = _serialize_metadata(metadata.get("target_config"))
    existing.metadata_json = _serialize_metadata(metadata)
    existing.source_path = "aws-scan"


def _active_policy_checks(db: Session, resource_type: Optional[str]) -> List[Any]:
    query = db.query(Policy).filter(
        Policy.status == "active", Policy.check_id.isnot(None)
    )
    if resource_type:
        canonical_type = _canonical_resource_type(resource_type)
        aliases = {resource_type, canonical_type}
        if canonical_type == "dynamodb":
            aliases.update({"dynamodb_table", "dynamodb_gsi"})
        elif canonical_type == "elasticache":
            aliases.update({"elasticache_replication_group", "elasticache_cluster"})
        elif canonical_type == "rds":
            aliases.add("rds_instance")
        query = query.filter(Policy.resource_type.in_(aliases))
    policies = query.order_by(Policy.id.asc()).all()

    checks = []
    seen: set[str] = set()
    for policy in policies:
        if not policy.check_id or policy.check_id in seen:
            continue
        check = check_registry.get_check(policy.check_id)
        if not check:
            logger.warning(
                "Skipping unknown active policy check %s; its policy may be stale",
                policy.check_id,
            )
            continue
        seen.add(policy.check_id)
        checks.append((check, policy.parameters_json or check.parameters))
    return checks


def _resolve_scan_inputs(
    db: Session, resource_type: Optional[str]
) -> Tuple[AccountSettings, Optional[str], List[Any]]:
    settings = require_account_region(db)
    settings = ensure_account_settings(db) or settings
    canonical_scope = _canonical_resource_type(resource_type) if resource_type else None

    check_entries = _active_policy_checks(db, resource_type)
    if not check_entries:
        fallback_checks = check_registry.list_checks(resource_type)
        check_entries = [(check, check.parameters) for check in fallback_checks]

    check_entries = [
        (check, parameters)
        for check, parameters in check_entries
        if is_check_enabled(db, check.check_id)
    ]

    return settings, canonical_scope, check_entries


def create_scan_execution(
    db: Session, resource_type: Optional[str] = None
) -> Dict[str, Any]:
    settings, canonical_scope, check_entries = _resolve_scan_inputs(db, resource_type)
    selected_regions = get_settings_regions(settings)
    execution = OnboardingExecution(
        settings_id=settings.id,
        status="running",
        total_checks=len(check_entries),
        completed_checks=0,
        failed_checks=0,
        results_json={
            "resource_type": canonical_scope,
            "selected_regions": selected_regions,
            "inventory_counts": {},
            "inventory_errors": [],
            "checks_completed": 0,
            "checks_failed": 0,
            "progress": {
                "phase": "queued",
                "message": "Scan queued",
                "current_region": None,
                "current_resource_type": canonical_scope,
                "current_check_id": None,
                "current_check_name": None,
                "check_index": 0,
                "total_checks": len(check_entries),
                "inventory_counts": {},
                "completed_checks": 0,
                "failed_checks": 0,
                "updated_at": _utc_now_iso(),
            },
        },
    )
    db.add(execution)
    db.commit()
    db.refresh(execution)
    return {
        "execution_id": execution.id,
        "status": execution.status,
        "resource_type": canonical_scope,
        "selected_regions": selected_regions,
        "total_checks": len(check_entries),
        "progress": execution.results_json.get("progress"),
    }


def _build_scan_status(db: Session, execution: OnboardingExecution) -> Dict[str, Any]:
    payload = execution.results_json or {}
    results: Dict[str, Dict[str, Any]] = {}
    error_details: List[Dict[str, str]] = []
    for check_result in execution.check_results:
        potential_savings_yearly = 0.0
        if check_result.metadata_json:
            potential_savings_yearly = (
                check_result.metadata_json.get("potential_savings_yearly", 0.0) or 0.0
            )
        results[check_result.check_id] = {
            "check_id": check_result.check_id,
            "name": check_result.name,
            "description": check_result.description,
            "resource_type": check_result.resource_type,
            "status": check_result.status,
            "resources_found": check_result.resources_found,
            "potential_savings_yearly": round(float(potential_savings_yearly), 6),
            "error": check_result.error,
            "execution_time": (
                execution.completed_at.isoformat()
                if execution.completed_at
                else execution.started_at.isoformat()
                if execution.started_at
                else None
            ),
        }
        if check_result.status == "failed":
            error_details.append(
                {
                    "check_id": check_result.check_id,
                    "name": check_result.name,
                    "resource_type": check_result.resource_type,
                    "error": check_result.error or "Unknown error",
                }
            )

    return {
        "execution_id": execution.id,
        "status": execution.status,
        "resource_type": payload.get("resource_type"),
        "selected_regions": payload.get("selected_regions") or [],
        "inventory_counts": payload.get("inventory_counts") or {},
        "inventory_errors": payload.get("inventory_errors") or [],
        "error_details": error_details,
        "checks_completed": execution.completed_checks
        or payload.get("checks_completed")
        or 0,
        "checks_failed": execution.failed_checks or payload.get("checks_failed") or 0,
        "total_checks": execution.total_checks or 0,
        "progress": payload.get("progress") or {},
        "results": results,
        "execution_time": (
            execution.completed_at.isoformat()
            if execution.completed_at
            else execution.started_at.isoformat()
            if execution.started_at
            else None
        ),
    }


def get_scan_status(db: Session, execution_id: int) -> Dict[str, Any]:
    execution = (
        db.query(OnboardingExecution)
        .filter(OnboardingExecution.id == execution_id)
        .first()
    )
    if not execution:
        raise ValueError(f"Scan execution '{execution_id}' not found")
    return _build_scan_status(db, execution)


CANCELED_SCAN_STATUS = "canceled"


def cancel_running_scans(db: Session) -> int:
    """Mark every in-progress scan canceled and return how many were signalled.

    Scans run in a background thread with no handle to interrupt, so this is
    cooperative: run_inventory_scan re-reads its own row between checks and
    stops when it sees this status.
    """
    running = (
        db.query(OnboardingExecution)
        .filter(OnboardingExecution.status == "running")
        .all()
    )
    now = datetime.now(timezone.utc)
    for execution in running:
        execution.status = CANCELED_SCAN_STATUS
        execution.completed_at = now
        execution.error_message = "Scan stopped because the AWS account changed."
        db.add(execution)
    db.commit()
    return len(running)


def count_running_scans(db: Session) -> int:
    """How many scans are in progress right now."""
    return (
        db.query(OnboardingExecution)
        .filter(OnboardingExecution.status == "running")
        .count()
    )


def _scan_was_canceled(db: Session, execution: Optional[OnboardingExecution]) -> bool:
    """True once another session has marked this run canceled.

    The status is expired first: the cancelling request commits on a different
    session, so the copy held here is stale until it is re-read.
    """
    if execution is None:
        return False
    try:
        db.expire(execution, ["status"])
        return execution.status == CANCELED_SCAN_STATUS
    except SQLAlchemyError:
        return False


def mark_scan_failed(db: Session, execution_id: int, error: str) -> None:
    execution = (
        db.query(OnboardingExecution)
        .filter(OnboardingExecution.id == execution_id)
        .first()
    )
    if not execution:
        return
    payload = dict(execution.results_json or {})
    payload["error"] = error
    payload["progress"] = {
        **(payload.get("progress") or {}),
        "phase": "failed",
        "message": error,
        "updated_at": _utc_now_iso(),
    }
    execution.status = "failed"
    execution.error_message = error
    execution.completed_at = datetime.now(timezone.utc)
    execution.results_json = payload
    db.add(execution)
    db.commit()


def run_inventory_scan(
    db: Session,
    resource_type: Optional[str] = None,
    execution_id: Optional[int] = None,
) -> Dict[str, Any]:
    settings, canonical_scope, check_entries = _resolve_scan_inputs(db, resource_type)
    generated_at = datetime.now(timezone.utc)
    selected_regions = get_settings_regions(settings)

    if execution_id is not None:
        execution = (
            db.query(OnboardingExecution)
            .filter(OnboardingExecution.id == execution_id)
            .first()
        )
        if not execution:
            raise ValueError(f"Scan execution '{execution_id}' not found")
    else:
        execution = OnboardingExecution(
            settings_id=settings.id,
            status="running",
            total_checks=len(check_entries),
            completed_checks=0,
            failed_checks=0,
            results_json={
                "resource_type": canonical_scope,
                "selected_regions": selected_regions,
                "inventory_counts": {},
                "inventory_errors": [],
                "checks_completed": 0,
                "checks_failed": 0,
            },
        )
        db.add(execution)
        db.commit()
        db.refresh(execution)

    execution.status = "running"
    execution.total_checks = len(check_entries)
    db.add(execution)
    db.commit()
    db.refresh(execution)

    inventory_scope = (
        [canonical_scope] if canonical_scope else list(SUPPORTED_INVENTORY_TYPES)
    )
    adapter_cache: Dict[Optional[str], CachedAWSAdapter] = {}
    overlay_store: MetadataOverlayStore = {}
    _set_scan_progress(
        db,
        execution,
        phase="inventory",
        message=f"Starting inventory scan for {', '.join(selected_regions)}",
        current_resource_type=canonical_scope,
        total_checks=len(check_entries),
    )
    collected = _collect_inventory(
        db,
        inventory_scope,
        settings,
        generated_at,
        adapter_cache,
        execution,
        overlay_store,
    )
    resource_map: Dict[str, int] = collected["resource_map"]
    if "elasticache" in inventory_scope:
        purge_legacy_elasticache_member_rows(db)
        db.flush()

    if canonical_scope:
        db.query(MaxOpsInventory).filter(
            MaxOpsInventory.resource_type == canonical_scope
        ).delete(synchronize_session=False)
    else:
        db.query(MaxOpsInventory).delete(synchronize_session=False)

    completed = 0
    failed = 0
    response_results: Dict[str, Dict[str, Any]] = {}
    error_details: List[Dict[str, str]] = []

    canceled = False
    for check_index, (check, parameters) in enumerate(check_entries, start=1):
        if _scan_was_canceled(db, execution):
            canceled = True
            break
        check_id = check.check_id
        try:
            resources = _execute_check_across_settings(
                check_id,
                parameters,
                settings,
                check.resource_type,
                adapter_cache,
                db,
                execution,
                check.name,
                check_index,
                len(check_entries),
                overlay_store,
            )
            _set_scan_progress(
                db,
                execution,
                phase="policies",
                message=f"Applying filters and pricing for {check.name}",
                current_resource_type=_canonical_resource_type(check.resource_type),
                current_check_id=check_id,
                current_check_name=check.name,
                check_index=check_index,
                total_checks=len(check_entries),
                inventory_counts=collected["inventory_counts"],
                completed_checks=completed,
                failed_checks=failed,
            )
            exemption_list = build_exemption_filter_payload(db, check_id)
            check_filter = (
                db.query(CheckFilter).filter(CheckFilter.check_id == check_id).first()
            )
            if check_filter and check_filter.filters_json:
                resources = apply_check_filters(
                    resources,
                    check_filter.filters_json,
                    check.resource_type,
                    exemption_list,
                )
            elif exemption_list:
                resources = apply_check_filters(
                    resources, [], check.resource_type, exemption_list
                )

            total_savings = _apply_pricing(db, check_id, resources)
            stored_resources = []
            for resource in resources:
                canonical_type = _canonical_resource_type(
                    str(resource.get("resource_type") or check.resource_type)
                )
                resource_id = str(resource.get("resource_id") or "")
                map_key = _inventory_resource_key(
                    canonical_type, resource_id, resource.get("region")
                )
                inventory_id = resource_map.get(map_key)
                if canonical_type in INVENTORY_MODELS:
                    inventory_id = _store_inventory_resource(
                        db,
                        canonical_type,
                        resource,
                        resource.get("account_id"),
                        generated_at,
                    )
                    if inventory_id is not None:
                        resource_map[map_key] = inventory_id
                if (
                    inventory_id is not None
                    and canonical_type in SUPPORTED_INVENTORY_TYPES
                ):
                    _store_maxops_finding(
                        db, check, resource, inventory_id, generated_at
                    )

                stored_resources.append(
                    {
                        "resource_id": resource.get("resource_id", ""),
                        "resource_type": canonical_type,
                        "resource_name": resource.get("resource_name"),
                        "region": resource.get("region"),
                        "account_id": resource.get("account_id"),
                        "tags": _serialize_metadata(resource.get("tags")),
                        "metadata": _serialize_metadata(resource.get("metadata", {})),
                        "pricing": _serialize_metadata(resource.get("pricing")),
                    }
                )

            check_result = OnboardingCheckResult(
                execution_id=execution.id,
                check_id=check_id,
                name=check.name,
                description=check.description,
                resource_type=_canonical_resource_type(check.resource_type),
                status="completed",
                resources_found=len(resources),
                error=None,
                metadata_json={
                    "potential_savings_yearly": round(total_savings, 6)
                    if total_savings
                    else 0.0,
                    "resources": stored_resources,
                },
            )
            db.add(check_result)
            completed += 1
            _set_scan_progress(
                db,
                execution,
                phase="policies",
                message=f"Completed {check.name}: {len(resources)} resources matched",
                current_resource_type=_canonical_resource_type(check.resource_type),
                current_check_id=check_id,
                current_check_name=check.name,
                check_index=check_index,
                total_checks=len(check_entries),
                inventory_counts=collected["inventory_counts"],
                completed_checks=completed,
                failed_checks=failed,
            )
            response_results[check_id] = {
                "check_id": check_id,
                "name": check.name,
                "description": check.description,
                "resource_type": _canonical_resource_type(check.resource_type),
                "status": "completed",
                "resources_found": len(resources),
                "potential_savings_yearly": round(total_savings, 6)
                if total_savings
                else 0.0,
                "error": None,
                "execution_time": generated_at.isoformat(),
            }
        except Exception as exc:
            logger.exception("Scan check failed for %s", check_id)
            error_details.append(
                {
                    "check_id": check_id,
                    "name": check.name,
                    "resource_type": _canonical_resource_type(check.resource_type),
                    "error": str(exc),
                }
            )
            db.add(
                OnboardingCheckResult(
                    execution_id=execution.id,
                    check_id=check_id,
                    name=check.name,
                    description=check.description,
                    resource_type=_canonical_resource_type(check.resource_type),
                    status="failed",
                    resources_found=0,
                    error=str(exc),
                    metadata_json={"resources": []},
                )
            )
            failed += 1
            _set_scan_progress(
                db,
                execution,
                phase="policies",
                message=f"Failed {check.name}: {exc}",
                current_resource_type=_canonical_resource_type(check.resource_type),
                current_check_id=check_id,
                current_check_name=check.name,
                check_index=check_index,
                total_checks=len(check_entries),
                inventory_counts=collected["inventory_counts"],
                completed_checks=completed,
                failed_checks=failed,
            )
            response_results[check_id] = {
                "check_id": check_id,
                "name": check.name,
                "description": check.description,
                "resource_type": _canonical_resource_type(check.resource_type),
                "status": "failed",
                "resources_found": 0,
                "potential_savings_yearly": 0.0,
                "error": str(exc),
                "execution_time": generated_at.isoformat(),
            }

    if canceled or _scan_was_canceled(db, execution):
        execution.status = CANCELED_SCAN_STATUS
    else:
        execution.status = "completed" if failed == 0 else "failed"
    execution.completed_checks = completed
    execution.failed_checks = failed
    execution.completed_at = generated_at
    execution.results_json = {
        "resource_type": canonical_scope,
        "selected_regions": selected_regions,
        "inventory_counts": collected["inventory_counts"],
        "inventory_errors": collected["errors"],
        "error_details": error_details,
        "checks_completed": completed,
        "checks_failed": failed,
        "progress": {
            "phase": "completed" if failed == 0 else "failed",
            "message": "Scan completed"
            if failed == 0
            else "Scan completed with errors",
            "current_region": None,
            "current_resource_type": canonical_scope,
            "current_check_id": None,
            "current_check_name": None,
            "check_index": len(check_entries),
            "total_checks": len(check_entries),
            "inventory_counts": collected["inventory_counts"],
            "completed_checks": completed,
            "failed_checks": failed,
            "updated_at": _utc_now_iso(),
        },
    }
    db.commit()
    db.refresh(execution)

    return {
        "execution_id": execution.id,
        "status": execution.status,
        "resource_type": canonical_scope,
        "selected_regions": selected_regions,
        "inventory_counts": collected["inventory_counts"],
        "inventory_errors": collected["errors"],
        "error_details": error_details,
        "checks_completed": completed,
        "checks_failed": failed,
        "total_checks": len(check_entries),
        "progress": execution.results_json.get("progress")
        if execution.results_json
        else {},
        "results": response_results,
        "execution_time": generated_at.isoformat(),
    }
