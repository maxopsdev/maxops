"""Account settings storage, catalog generation, and effective check parameters."""
from __future__ import annotations

import logging

from collections import defaultdict
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from app.actions.registry import action_registry
from app.checks.registry import CheckMetadata, check_registry
from app.config import settings as app_settings
from app.database import Base

logger = logging.getLogger(__name__)
from app.models.settings import AccountSettings, ActionSetting, CheckSetting, UserSettings


def is_s3_optimizer_enabled() -> bool:
    """Return the scan feature flag for S3 optimizer enrichment.

    The default is enabled so existing scans gain the additive metadata.  An
    operator can disable only this enrichment through the normal application
    settings environment configuration without changing the shared scan path.
    """

    from app.config import settings

    return bool(getattr(settings, "s3_optimizer_enabled", True))

PRESET_VALUES = ("conservative", "normal", "aggressive")
ONBOARDING_STEPS = {"information", "role", "account", "checks", "results", "completed"}
DEFAULT_ENVIRONMENT_OPTIONS = [
    "Development",
    "Staging",
    "Production",
    "UAT",
    "QA",
    "Testing",
    "Sandbox",
]

DEFAULT_RIGHTSIZER_POLICY: Dict[str, Any] = {
    "network_medium_ratio": 0.40,
    "network_high_ratio": 0.70,
    "ebs_medium_ratio": 0.40,
    "ebs_high_ratio": 0.70,
    "allow_unknown_instance_store_usage": False,
}

RIGHTSIZER_POLICY_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "key": "network_medium_ratio",
        "label": "Network medium threshold",
        "description": "Network utilization ratio at or above this value is considered medium risk.",
        "control": "slider",
        "unit": "ratio",
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
    },
    {
        "key": "network_high_ratio",
        "label": "Network high threshold",
        "description": "Network utilization ratio at or above this value requires review.",
        "control": "slider",
        "unit": "ratio",
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
    },
    {
        "key": "ebs_medium_ratio",
        "label": "EBS medium threshold",
        "description": "EBS utilization ratio at or above this value is considered medium risk.",
        "control": "slider",
        "unit": "ratio",
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
    },
    {
        "key": "ebs_high_ratio",
        "label": "EBS high threshold",
        "description": "EBS utilization ratio at or above this value requires review.",
        "control": "slider",
        "unit": "ratio",
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
    },
    {
        "key": "allow_unknown_instance_store_usage",
        "label": "Evaluate unknown instance store",
        "description": "Evaluate EC2 instances with unknown local instance-store usage as conditional recommendations.",
        "control": "toggle",
        "unit": None,
        "min": None,
        "max": None,
        "step": None,
    },
]

RESOURCE_TYPE_LABELS: Dict[str, str] = {
    "asg": "Auto Scaling",
    "athena": "Athena",
    "athena_workgroup": "Athena",
    "aurora": "Aurora",
    "aurora_cluster": "Aurora",
    "aurora_cluster_snapshot": "Aurora",
    "aurora_global_cluster": "Aurora",
    "aurora_instance": "Aurora",
    "cloudwatch": "CloudWatch",
    "cloudwatch_alarm": "CloudWatch",
    "cloudwatch_log_group": "CloudWatch",
    "dynamodb": "DynamoDB",
    "ebs": "EBS",
    "ec2": "EC2",
    "ecs": "ECS",
    "efs": "EFS",
    "elasticache": "ElastiCache",
    "emr": "EMR",
    "emr_cluster": "EMR",
    "emr_instance_fleet": "EMR",
    "firehose_delivery_stream": "Kinesis",
    "glue": "Glue",
    "glue_job": "Glue",
    "glue_table": "Glue / Athena",
    "kinesis": "Kinesis",
    "kinesis_stream": "Kinesis",
    "lambda": "Lambda",
    "opensearch": "OpenSearch",
    "opensearch_domain": "OpenSearch",
    "opensearch_index": "OpenSearch",
    "rds": "RDS",
    "rds_instance": "RDS",
    "redshift": "Redshift",
    "redshift_cluster": "Redshift",
    "redshift_snapshot": "Redshift",
    "s3": "S3",
    "snapshot": "Snapshots",
    "snapshots": "Snapshots",
    "vpc": "VPC",
}


def _resource_group_key(resource_type: str) -> str:
    if resource_type.startswith("aurora_"):
        return "aurora"
    if resource_type.startswith("cloudwatch_"):
        return "cloudwatch"
    if resource_type in {"glue_job", "glue_table"}:
        return "glue"
    if resource_type in {"athena_workgroup"}:
        return "athena"
    if resource_type in {"kinesis_stream", "firehose_delivery_stream"}:
        return "kinesis"
    if resource_type in {"opensearch_domain", "opensearch_index"}:
        return "opensearch"
    if resource_type in {"rds", "rds_instance"}:
        return "rds"
    if resource_type in {"emr_cluster", "emr_instance_fleet"}:
        return "emr"
    if resource_type in {"redshift_cluster", "redshift_snapshot"}:
        return "redshift"
    if resource_type == "snapshot":
        return "snapshots"
    return resource_type


RESOURCE_GROUP_DESCRIPTIONS: Dict[str, str] = {
    "asg": "Tune scaling efficiency and spare capacity checks for Auto Scaling Groups.",
    "athena": "Control thresholds for file layout, engine version, and result bucket hygiene.",
    "aurora": "Tune idle detection, sizing, retention, and engine optimization checks for Aurora.",
    "cloudwatch": "Adjust signal thresholds for noisy alarms and inactive log groups.",
    "dynamodb": "Control utilization and capacity mode recommendations for DynamoDB tables and GSIs.",
    "ebs": "Tune stale, oversized, and underutilized EBS volume detection.",
    "ec2": "Control how aggressively idle and unused EC2 instances are flagged.",
    "ecs": "Tune idle cluster and task reservation efficiency checks for ECS.",
    "efs": "Configure lifecycle, usage, and performance checks for EFS.",
    "elasticache": "Tune low-usage and modernization checks for ElastiCache.",
    "emr": "Adjust activity, version, and cluster-sizing checks for EMR.",
    "glue": "Tune Glue version, partitioning, and DPU utilization checks.",
    "kinesis": "Control inactive, oversharded, and Firehose delivery optimization checks.",
    "lambda": "Tune memory, concurrency, and logging thresholds for Lambda.",
    "opensearch": "Adjust shard density, storage, node sizing, and activity thresholds for OpenSearch.",
    "rds": "Control idle and modernization checks for RDS databases.",
    "redshift": "Tune idle, stale snapshot, and cluster overprovisioning checks for Redshift.",
    "s3": "Configure lifecycle and data management checks for S3 buckets.",
    "snapshots": "Control how old snapshots need to be before they are flagged.",
    "vpc": "Review network cost and observability checks for VPC resources.",
}

CONTROL_LABELS: Dict[str, str] = {
    "cpu_threshold": "CPU threshold",
    "cpu_max_threshold": "CPU max threshold",
    "cpu_p90_threshold": "CPU P90 threshold",
    "cpu_p95_threshold": "CPU P95 threshold",
    "cpu_p99_threshold": "CPU P99 threshold",
    "network_threshold": "Network threshold",
    "connections_threshold": "Connections threshold",
    "lookback_days": "Lookback window",
    "idle_days": "Idle window",
    "stopped_days": "Stopped window",
    "min_age_days": "Minimum age",
    "max_retention_days": "Max retention days",
    "trigger_count_threshold": "Trigger count threshold",
}


def _friendly_label(name: str) -> str:
    if name in CONTROL_LABELS:
        return CONTROL_LABELS[name]
    return name.replace("_", " ").replace("pct", "%").title()


def _primary_region(regions: Iterable[str]) -> Optional[str]:
    for region in regions:
        normalized = str(region).strip()
        if normalized:
            return normalized
    return None


def _normalize_regions(regions: Optional[Iterable[str]]) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()
    for region in regions or []:
        value = str(region).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _normalize_account(value: Optional[str]) -> str:
    return str(value or "").strip()


def _normalize_environment(value: Optional[str]) -> str:
    return str(value or "").strip()


# The account settings row survives: it carries the account and profile being
# switched to, and every other table hangs off it.
PRESERVED_TABLES = {"account_settings"}


def reseed_default_policies(db: Session) -> int:
    """Put the default check policies back after a reset. Does not commit.

    The wipe empties `policies` along with everything else, and seeding only
    runs at startup, so without this a reset would leave the account with no
    checks to run until the container was restarted.
    """
    from app.utils.seed_data import seed_policies

    try:
        return seed_policies(db)
    except Exception as exc:  # noqa: BLE001 - a reset must not fail on reseeding
        logger.warning("Could not reseed default policies after a reset: %s", exc)
        return 0


def clear_account_inventory_state(db: Session) -> Dict[str, int]:
    """Empty every table but the account settings row. Does not commit.

    Driven off the table metadata rather than a hand-written model list, so a
    table added later is covered without anyone remembering to add it. The
    list this replaced had already fallen four tables behind, leaving tags,
    snooze audits and action history describing resources from an account the
    user had moved off. Deletion runs children-first so foreign keys hold.
    """
    import app.models  # noqa: F401 - registers every table on the metadata

    deleted: Dict[str, int] = {}
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in PRESERVED_TABLES:
            continue
        deleted[table.name] = db.execute(table.delete()).rowcount
    return deleted


def _normalize_environment_options(
    values: Optional[Iterable[str]],
    selected_environment: Optional[str] = None,
) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()

    for environment in [*DEFAULT_ENVIRONMENT_OPTIONS, *(values or []), selected_environment]:
        value = _normalize_environment(environment)
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(value)

    return normalized


def _sanitize_parameters(parameters: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in parameters.items() if key != "region"}


def _float_setting(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = fallback
    return min(max(parsed, 0.0), 1.0)


def normalize_rightsizer_policy(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    values = dict(DEFAULT_RIGHTSIZER_POLICY)
    if isinstance(payload, dict):
        values.update(payload)

    network_medium = _float_setting(
        values.get("network_medium_ratio"),
        DEFAULT_RIGHTSIZER_POLICY["network_medium_ratio"],
    )
    network_high = _float_setting(
        values.get("network_high_ratio"),
        DEFAULT_RIGHTSIZER_POLICY["network_high_ratio"],
    )
    ebs_medium = _float_setting(
        values.get("ebs_medium_ratio"),
        DEFAULT_RIGHTSIZER_POLICY["ebs_medium_ratio"],
    )
    ebs_high = _float_setting(
        values.get("ebs_high_ratio"),
        DEFAULT_RIGHTSIZER_POLICY["ebs_high_ratio"],
    )

    if network_medium > network_high:
        network_medium = network_high
    if ebs_medium > ebs_high:
        ebs_medium = ebs_high

    return {
        "network_medium_ratio": round(network_medium, 4),
        "network_high_ratio": round(network_high, 4),
        "ebs_medium_ratio": round(ebs_medium, 4),
        "ebs_high_ratio": round(ebs_high, 4),
        "allow_unknown_instance_store_usage": bool(values.get("allow_unknown_instance_store_usage")),
    }


def build_rightsizer_policy_catalog(settings: Optional[AccountSettings]) -> Dict[str, Any]:
    onboarding_data = settings.onboarding_data if settings and isinstance(settings.onboarding_data, dict) else {}
    policy = normalize_rightsizer_policy(onboarding_data.get("rightsizer_policy"))
    definitions = [
        {
            **definition,
            "advanced": False,
            "options": [True, False] if definition["control"] == "toggle" else None,
            "preset_values": {
                "conservative": DEFAULT_RIGHTSIZER_POLICY[definition["key"]],
                "normal": DEFAULT_RIGHTSIZER_POLICY[definition["key"]],
                "aggressive": DEFAULT_RIGHTSIZER_POLICY[definition["key"]],
            },
            "value": policy[definition["key"]],
        }
        for definition in RIGHTSIZER_POLICY_DEFINITIONS
    ]
    return {
        "parameters": policy,
        "parameter_definitions": definitions,
    }


def save_rightsizer_policy(
    db: Session,
    settings: AccountSettings,
    policy_payload: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    policy = normalize_rightsizer_policy(policy_payload)
    current_data = settings.onboarding_data if isinstance(settings.onboarding_data, dict) else {}
    settings.onboarding_data = {**current_data, "rightsizer_policy": policy}
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return policy


def get_effective_rightsizer_policy(db: Session) -> Dict[str, Any]:
    return build_rightsizer_policy_catalog(ensure_account_settings(db))["parameters"]


def _clamp_number(value: float, minimum: Optional[float], maximum: Optional[float]) -> float:
    if minimum is not None and value < minimum:
        value = minimum
    if maximum is not None and value > maximum:
        value = maximum
    return value


def _infer_control_metadata(name: str, value: Any) -> Dict[str, Any]:
    if isinstance(value, bool):
        return {"control": "toggle", "unit": None, "min": None, "max": None, "step": None, "advanced": False}

    if isinstance(value, list):
        return {"control": "token-list", "unit": None, "min": None, "max": None, "step": None, "advanced": True}

    if isinstance(value, str):
        control = "select" if "version" in name or "release" in name else "text"
        return {"control": control, "unit": None, "min": None, "max": None, "step": None, "advanced": control == "text"}

    if not isinstance(value, (int, float)):
        return {"control": "text", "unit": None, "min": None, "max": None, "step": None, "advanced": True}

    lower_name = name.lower()
    unit = None
    control = "stepper"
    minimum: Optional[float] = 0
    maximum: Optional[float] = None
    step: Optional[float] = 1 if isinstance(value, int) else 0.1
    advanced = False

    if lower_name in {"min_acu_threshold", "avg_capacity_ratio_threshold", "max_write_iops"}:
        control = "dial"
        if lower_name == "min_acu_threshold":
            unit = "ACU"
            maximum = max(256, float(value) * 4 if value else 256)
            step = 0.5
        elif lower_name == "avg_capacity_ratio_threshold":
            unit = "ratio"
            maximum = 1.0 if value <= 1 else max(5.0, float(value) * 2)
            step = 0.05 if maximum <= 1 else 0.1
        else:
            unit = "IOPS"
            maximum = max(10000, float(value) * 4 if value else 10000)
            step = 10 if value and value >= 100 else 1
        return {
            "control": control,
            "unit": unit,
            "min": minimum,
            "max": maximum,
            "step": step,
            "advanced": advanced,
        }

    if any(token in lower_name for token in ("days", "hours", "minutes", "age")):
        control = "dial"
        unit = "days" if "days" in lower_name or "age" in lower_name else "hours" if "hours" in lower_name else "minutes"
        maximum = 120 if "days" in lower_name or "age" in lower_name else 72 if "hours" in lower_name else 180
        step = 1
    elif "threshold" in lower_name:
        control = "dial"
        if "bytes" in lower_name:
            unit = "bytes"
            maximum = max(float(value) * 4 if value else 1_000_000_000, 1_000_000_000)
            step = max(1.0, round(maximum / 100))
            advanced = True
        elif "throughput" in lower_name and "mb" in lower_name:
            unit = "MB/s"
            maximum = max(500, float(value) * 5)
            step = 0.5
        elif "iops" in lower_name:
            unit = "IOPS"
            maximum = max(10000, float(value) * 4 if value else 10000)
            step = 10 if value and value >= 100 else 1
        elif "network" in lower_name and "gb" in lower_name:
            unit = "GB"
            maximum = max(100, float(value) * 5 if value else 100)
            step = 0.5
        elif "ms" in lower_name:
            unit = "ms"
            maximum = max(5000, float(value) * 4 if value else 5000)
            step = 1
        elif any(token in lower_name for token in ("pct", "utilization", "cpu", "memory", "jvm")):
            unit = "%"
            maximum = 100
            step = 1 if isinstance(value, int) else 0.5
        else:
            unit = None
            maximum = max(100, float(value) * 4 if value else 100)
            step = 1 if isinstance(value, int) else 0.5
    elif "ratio" in lower_name:
        control = "slider"
        unit = "ratio"
        maximum = 1.0 if value <= 1 else max(5.0, float(value) * 2)
        step = 0.05 if maximum <= 1 else 0.1
    elif "pct" in lower_name or "utilization" in lower_name or "cpu" in lower_name or "memory" in lower_name or "jvm" in lower_name:
        control = "slider"
        unit = "%"
        maximum = 100
        step = 1 if isinstance(value, int) else 0.5
    elif "size" in lower_name and "mb" in lower_name:
        control = "slider"
        unit = "MB"
        maximum = max(512, float(value) * 4)
        step = 1
    elif "size" in lower_name and "gib" in lower_name:
        control = "slider"
        unit = "GiB"
        maximum = max(512, float(value) * 4)
        step = 1
    elif "size" in lower_name and "gb" in lower_name:
        control = "slider"
        unit = "GB"
        maximum = max(1024, float(value) * 4)
        step = 1
    elif "throughput" in lower_name and "mb" in lower_name:
        control = "slider"
        unit = "MB/s"
        maximum = max(500, float(value) * 5)
        step = 0.5
    elif "iops" in lower_name:
        control = "slider"
        unit = "IOPS"
        maximum = max(10000, float(value) * 4 if value else 10000)
        step = 10 if value and value >= 100 else 1
    elif "network" in lower_name and "gb" in lower_name:
        control = "slider"
        unit = "GB"
        maximum = max(100, float(value) * 5 if value else 100)
        step = 0.5
    elif "bytes" in lower_name:
        control = "slider"
        unit = "bytes"
        maximum = max(float(value) * 4 if value else 1_000_000_000, 1_000_000_000)
        step = max(1.0, round(maximum / 100))
        advanced = True
    elif "count" in lower_name or lower_name.startswith("min_") or lower_name.startswith("max_"):
        control = "stepper"
        unit = None
        maximum = max(100, float(value) * 4 if value else 100)
        step = 1

    if lower_name in {"period_seconds", "max_activity_records"} or "include_" in lower_name or "exclude_" in lower_name:
        advanced = True

    return {
        "control": control,
        "unit": unit,
        "min": minimum,
        "max": maximum,
        "step": step,
        "advanced": advanced,
    }


def _preset_multiplier(name: str, preset: str) -> float:
    lower_name = name.lower()
    if preset == "normal":
        return 1.0

    higher_is_more_aggressive = any(
        token in lower_name
        for token in (
            "threshold",
            "utilization",
            "ratio",
            "network",
            "connections",
            "queries",
            "bytes",
            "records",
            "items",
            "lag",
            "cpu",
            "memory",
            "search_rate",
            "index_rate",
            "retention",
            "shards_per_node",
            "replicas",
        )
    )
    lower_is_more_aggressive = any(
        token in lower_name
        for token in ("days", "hours", "minutes", "age", "min_size", "min_old", "min_readers", "min_instances", "min_data_nodes")
    )

    if preset == "conservative":
        if lower_is_more_aggressive:
            return 1.75
        if higher_is_more_aggressive:
            return 0.7
        return 1.0

    if preset == "aggressive":
        if lower_is_more_aggressive:
            return 0.55
        if higher_is_more_aggressive:
            return 1.35
        return 1.0

    return 1.0


def _apply_preset_value(name: str, value: Any, preset: str) -> Any:
    if preset == "normal":
        return deepcopy(value)

    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        return deepcopy(value)
    if isinstance(value, str):
        return value
    if not isinstance(value, (int, float)):
        return deepcopy(value)

    meta = _infer_control_metadata(name, value)
    multiplier = _preset_multiplier(name, preset)
    computed = float(value) * multiplier
    if isinstance(value, int):
        computed = round(computed)
    computed = _clamp_number(computed, meta["min"], meta["max"])
    if meta["step"]:
        step = float(meta["step"])
        if step > 0:
            computed = round(computed / step) * step
    return int(computed) if isinstance(value, int) else round(float(computed), 4)


def build_check_preset_parameters(check: CheckMetadata) -> Dict[str, Dict[str, Any]]:
    base = _sanitize_parameters(check.parameters or {})
    presets: Dict[str, Dict[str, Any]] = {}
    for preset in PRESET_VALUES:
        preset_params: Dict[str, Any] = {}
        for name, value in base.items():
            preset_params[name] = _apply_preset_value(name, value, preset)
        presets[preset] = preset_params
    return presets


def build_parameter_definition(name: str, value: Any, presets: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    meta = _infer_control_metadata(name, value)
    options = None
    if isinstance(value, bool):
        options = [True, False]
    elif isinstance(value, str) and meta["control"] == "select":
        options = sorted({str(preset_values.get(name, value)) for preset_values in presets.values()})
    return {
        "key": name,
        "label": _friendly_label(name),
        "description": f"Controls {name.replace('_', ' ')} for this check.",
        "control": meta["control"],
        "unit": meta["unit"],
        "min": meta["min"],
        "max": meta["max"],
        "step": meta["step"],
        "advanced": meta["advanced"],
        "options": options,
        "preset_values": {preset: preset_values.get(name) for preset, preset_values in presets.items()},
    }


def get_account_settings(db: Session) -> Optional[AccountSettings]:
    settings = db.query(AccountSettings).first()
    if settings:
        settings.regions = _normalize_regions(settings.regions)
        settings.environment_options = _normalize_environment_options(
            getattr(settings, "environment_options", None),
            settings.environment,
        )
    return settings


def get_or_create_onboarding_settings(db: Session) -> AccountSettings:
    settings = ensure_account_settings(db)
    if settings:
        return settings

    settings = AccountSettings(
        environment="Production",
        account="",
        environment_options=_normalize_environment_options(None, "Production"),
        regions=[],
        onboarding_completed=False,
        onboarding_step="information",
        onboarding_data={},
    )
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


def ensure_account_settings(db: Session) -> Optional[AccountSettings]:
    settings = get_account_settings(db)
    if settings:
        return settings

    legacy = db.query(UserSettings).first()
    if not legacy:
        return None

    regions = _normalize_regions(getattr(legacy, "regions", None) or [getattr(legacy, "region", None)])
    account = _normalize_account(getattr(legacy, "account", None))
    environment = _normalize_environment(getattr(legacy, "environment", None))

    if not environment:
        environment = "Production"

    settings = AccountSettings(
        environment=environment,
        account=account or "default-account",
        environment_options=_normalize_environment_options(None, environment),
        regions=regions or ["us-east-1"],
        onboarding_completed=bool(getattr(legacy, "onboarding_completed", False)),
        onboarding_step="completed" if bool(getattr(legacy, "onboarding_completed", False)) else "account",
        onboarding_data={},
    )
    db.add(settings)
    db.flush()

    legacy_defaults = {
        "ec2_idle_instances": {
            "idle_days": getattr(legacy, "idle_days", 7),
            "cpu_threshold": 5.0,
        },
        "ec2_unused_instances": {
            "stopped_days": getattr(legacy, "idle_days", 30),
        },
        "rds_idle_databases": {
            "idle_days": getattr(legacy, "idle_days", 7),
            "cpu_threshold": 5.0,
            "connections_threshold": max(getattr(legacy, "a_days", 5), 1),
        },
    }

    for check_id, parameters in legacy_defaults.items():
        check = check_registry.get_check(check_id)
        if not check:
            continue
        normalized_parameters = {**_sanitize_parameters(check.parameters), **parameters}
        db.add(
            CheckSetting(
                account_settings_id=settings.id,
                check_id=check_id,
                resource_type=check.resource_type,
                preset="normal",
                parameters_json=normalized_parameters,
                is_customized=True,
            )
        )

    db.commit()
    db.refresh(settings)
    return settings


def update_onboarding_progress(
    db: Session,
    onboarding_step: Optional[str] = None,
    onboarding_data: Optional[Dict[str, Any]] = None,
) -> AccountSettings:
    settings = get_or_create_onboarding_settings(db)

    if onboarding_step is not None:
        normalized_step = str(onboarding_step or "").strip()
        if normalized_step not in ONBOARDING_STEPS:
            raise ValueError("Invalid onboarding step.")
        settings.onboarding_step = normalized_step

    if onboarding_data is not None:
        current_data = settings.onboarding_data if isinstance(settings.onboarding_data, dict) else {}
        settings.onboarding_data = {**current_data, **onboarding_data}

    db.commit()
    db.refresh(settings)
    return settings


def save_onboarding_iam_role_result(db: Session, role_result: Dict[str, Any]) -> AccountSettings:
    settings = get_or_create_onboarding_settings(db)
    account_id = _normalize_account(role_result.get("aws_account_id"))
    if account_id:
        settings.account = account_id

    current_data = settings.onboarding_data if isinstance(settings.onboarding_data, dict) else {}
    settings.onboarding_data = {
        **current_data,
        "iam_role": role_result,
    }
    settings.onboarding_step = "account"

    db.commit()
    db.refresh(settings)
    return settings


def save_onboarding_account_draft(
    db: Session,
    environment: Optional[str] = None,
    account: Optional[str] = None,
    regions: Optional[Iterable[str]] = None,
) -> AccountSettings:
    settings = get_or_create_onboarding_settings(db)

    normalized_environment = _normalize_environment(environment)
    if normalized_environment:
        settings.environment = normalized_environment
        settings.environment_options = _normalize_environment_options(
            getattr(settings, "environment_options", None),
            normalized_environment,
        )

    if account is not None:
        settings.account = _normalize_account(account)

    if regions is not None:
        settings.regions = _normalize_regions(regions)

    if not settings.onboarding_completed:
        settings.onboarding_step = "account"

    db.commit()
    db.refresh(settings)
    return settings


def upsert_account_settings(
    db: Session,
    environment: str,
    account: str,
    environment_options: Optional[Iterable[str]],
    regions: Iterable[str],
    onboarding_completed: bool,
) -> AccountSettings:
    normalized_environment = _normalize_environment(environment)
    normalized_account = _normalize_account(account)
    normalized_environment_options = _normalize_environment_options(environment_options, normalized_environment)
    normalized_regions = _normalize_regions(regions)

    if not normalized_environment:
        raise ValueError("Environment is required.")
    if not normalized_account:
        raise ValueError("Account is required.")
    if not normalized_regions:
        raise ValueError("At least one region is required.")

    settings = ensure_account_settings(db)
    if not settings:
        settings = AccountSettings(
            environment=normalized_environment,
            account=normalized_account,
            environment_options=normalized_environment_options,
            regions=normalized_regions,
            onboarding_completed=onboarding_completed,
            onboarding_step="completed" if onboarding_completed else "account",
            onboarding_data={},
        )
        db.add(settings)
    else:
        account_changed = _normalize_account(settings.account) != normalized_account
        if account_changed:
            clear_account_inventory_state(db)
            reseed_default_policies(db)
        settings.environment = normalized_environment
        settings.account = normalized_account
        settings.environment_options = normalized_environment_options
        settings.regions = normalized_regions
        settings.onboarding_completed = onboarding_completed
        settings.onboarding_step = "completed" if onboarding_completed else "account"

    db.commit()
    db.refresh(settings)
    return settings


def save_check_settings(
    db: Session,
    settings: AccountSettings,
    check_payloads: Iterable[Dict[str, Any]],
) -> List[CheckSetting]:
    existing = {
        row.check_id: row
        for row in db.query(CheckSetting).filter(CheckSetting.account_settings_id == settings.id).all()
    }

    saved_rows: List[CheckSetting] = []
    for payload in check_payloads:
        check_id = str(payload.get("check_id") or "").strip()
        preset = str(payload.get("preset") or "normal").strip().lower()
        parameters = payload.get("parameters") or {}
        enabled = bool(payload.get("enabled", True))
        if not check_id:
            continue
        if preset not in PRESET_VALUES:
            preset = "normal"

        check = check_registry.get_check(check_id)
        if not check:
            continue

        preset_defaults = build_check_preset_parameters(check)[preset]
        merged_parameters = {**preset_defaults, **_sanitize_parameters(parameters)}
        is_customized = merged_parameters != preset_defaults

        row = existing.get(check_id)
        if row is None:
            row = CheckSetting(
                account_settings_id=settings.id,
                check_id=check_id,
                resource_type=check.resource_type,
            )
            db.add(row)

        row.resource_type = check.resource_type
        row.preset = preset
        row.parameters_json = merged_parameters
        row.is_customized = is_customized
        row.enabled = enabled
        saved_rows.append(row)

    db.commit()
    return saved_rows


def get_effective_check_parameters(
    db: Session,
    check_id: str,
    explicit_parameters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    check = check_registry.get_check(check_id)
    if not check:
        return explicit_parameters or {}

    base_parameters = _sanitize_parameters(check.parameters or {})
    settings = ensure_account_settings(db)
    if settings:
        saved = (
            db.query(CheckSetting)
            .filter(CheckSetting.account_settings_id == settings.id, CheckSetting.check_id == check_id)
            .first()
        )
        if saved and isinstance(saved.parameters_json, dict):
            base_parameters = {**base_parameters, **saved.parameters_json}
    if explicit_parameters:
        base_parameters = {**base_parameters, **_sanitize_parameters(explicit_parameters)}
    return base_parameters


def is_check_enabled(db: Session, check_id: str) -> bool:
    """Whether a check is enabled per saved Settings (defaults to True)."""
    settings = ensure_account_settings(db)
    if not settings:
        return True
    row = (
        db.query(CheckSetting)
        .filter(CheckSetting.account_settings_id == settings.id, CheckSetting.check_id == check_id)
        .first()
    )
    if row is None:
        return True
    return bool(row.enabled)


def is_action_enabled(db: Session, action_key: str) -> bool:
    """Whether an action can run: the global env gate wins outright, then the
    per-action saved setting (defaults to True when actions are globally on)."""
    if not app_settings.actions_enabled:
        return False
    settings = ensure_account_settings(db)
    if not settings:
        return True
    row = (
        db.query(ActionSetting)
        .filter(ActionSetting.account_settings_id == settings.id, ActionSetting.action_key == action_key)
        .first()
    )
    if row is None:
        return True
    return bool(row.enabled)


def save_action_settings(
    db: Session,
    settings: AccountSettings,
    action_payloads: Iterable[Dict[str, Any]],
) -> List[ActionSetting]:
    existing = {
        row.action_key: row
        for row in db.query(ActionSetting).filter(ActionSetting.account_settings_id == settings.id).all()
    }

    saved_rows: List[ActionSetting] = []
    for payload in action_payloads:
        action_key = str(payload.get("action_key") or "").strip()
        enabled = bool(payload.get("enabled", True))
        if not action_key:
            continue
        if not action_registry.get_action(action_key):
            continue

        row = existing.get(action_key)
        if row is None:
            row = ActionSetting(
                account_settings_id=settings.id,
                action_key=action_key,
            )
            db.add(row)

        row.enabled = enabled
        saved_rows.append(row)

    db.commit()
    return saved_rows


def get_execution_regions(settings: AccountSettings) -> List[str]:
    regions = _normalize_regions(settings.regions)
    return regions or ["us-east-1"]


def build_account_settings_summary(settings: AccountSettings) -> Dict[str, Any]:
    regions = _normalize_regions(settings.regions)
    return {
        "id": settings.id,
        "environment": settings.environment,
        "account": settings.account,
        "environment_options": _normalize_environment_options(
            getattr(settings, "environment_options", None),
            settings.environment,
        ),
        "region": _primary_region(regions),
        "regions": regions,
        "onboarding_completed": settings.onboarding_completed,
        "onboarding_step": getattr(settings, "onboarding_step", None) or "information",
        "onboarding_data": settings.onboarding_data if isinstance(settings.onboarding_data, dict) else {},
        "created_at": settings.created_at,
        "updated_at": settings.updated_at,
    }


def build_settings_catalog(db: Session) -> Dict[str, Any]:
    settings = ensure_account_settings(db)
    saved_settings = {
        row.check_id: row
        for row in (settings.check_settings if settings else [])
    }
    grouped_checks: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for check in sorted(check_registry.list_checks(), key=lambda item: (RESOURCE_TYPE_LABELS.get(item.resource_type, item.resource_type), item.name)):
        group_key = _resource_group_key(check.resource_type)
        presets = build_check_preset_parameters(check)
        saved = saved_settings.get(check.check_id)
        active_preset = saved.preset if saved and saved.preset in PRESET_VALUES else "normal"
        parameters = deepcopy(presets[active_preset])
        if saved and isinstance(saved.parameters_json, dict):
            parameters.update(saved.parameters_json)
        parameter_definitions = [
            {
                **build_parameter_definition(name, value, presets),
                "value": parameters.get(name),
            }
            for name, value in _sanitize_parameters(check.parameters or {}).items()
        ]
        grouped_checks[group_key].append(
            {
                "check_id": check.check_id,
                "name": check.name,
                "description": check.description,
                "resource_type": check.resource_type,
                "default_action": check.default_action,
                "preset": active_preset,
                "parameters": parameters,
                "is_customized": bool(saved.is_customized) if saved else False,
                "enabled": bool(saved.enabled) if saved else True,
                "preset_descriptions": {
                    "conservative": "Lower noise. Flags only strong optimization signals.",
                    "normal": "Balanced thresholds for general-purpose workloads.",
                    "aggressive": "Higher sensitivity. Surfaces more possible savings opportunities.",
                },
                "parameter_definitions": parameter_definitions,
            }
        )

    resource_groups = []
    for group_key, checks in sorted(grouped_checks.items(), key=lambda item: RESOURCE_TYPE_LABELS.get(item[0], item[0])):
        resource_groups.append(
            {
                "resource_type": group_key,
                "label": RESOURCE_TYPE_LABELS.get(group_key, group_key.replace("_", " ").title()),
                "description": RESOURCE_GROUP_DESCRIPTIONS.get(group_key, "Tune optimization checks for this resource type."),
                "check_count": len(checks),
                "checks": checks,
            }
        )

    saved_action_settings = {
        row.action_key: row
        for row in (settings.action_settings if settings else [])
    }
    grouped_actions: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    seen_action_group_pairs: set = set()
    for action in sorted(action_registry.list_actions(), key=lambda item: item.name):
        saved_action = saved_action_settings.get(action.action_key)
        action_entry = {
            "action_key": action.action_key,
            "name": action.name,
            "description": action.description,
            "resource_types": action.resource_types,
            "enabled": bool(saved_action.enabled) if saved_action else True,
        }
        for resource_type in action.resource_types:
            group_key = _resource_group_key(resource_type)
            pair = (group_key, action.action_key)
            if pair in seen_action_group_pairs:
                continue
            seen_action_group_pairs.add(pair)
            grouped_actions[group_key].append(action_entry)

    action_groups = []
    for group_key, actions in sorted(grouped_actions.items(), key=lambda item: RESOURCE_TYPE_LABELS.get(item[0], item[0])):
        action_groups.append(
            {
                "resource_type": group_key,
                "label": RESOURCE_TYPE_LABELS.get(group_key, group_key.replace("_", " ").title()),
                "action_count": len(actions),
                "actions": actions,
            }
        )

    return {
        "global_settings": build_account_settings_summary(settings) if settings else None,
        "resource_groups": resource_groups,
        "actions_enabled": app_settings.actions_enabled,
        "action_groups": action_groups,
    }
