"""Generate deterministic, DB-import-friendly ASG synthetic data from JSON config."""

from __future__ import annotations

import argparse
import json
import random
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

CURRENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parents[0]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.checks.base import estimate_ec2_monthly_cost


CONFIG_PATH = CURRENT_DIR / "asg_syn_config.json"
HISTORY_SERIES_COUNT = 15
HISTORY_SERIES_SPACING_DAYS = 30


class GeneratedArtifacts:
    def __init__(self, inventory: Dict[str, Any], maxops: List[Dict[str, Any]]) -> None:
        self.inventory = inventory
        self.maxops = maxops


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path, payload: Dict[str, Any] | List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def resolve_output_dir(config: Dict[str, Any], override: Optional[Path]) -> Path:
    if override is not None:
        return override if override.is_absolute() else (REPO_ROOT / override)
    configured = Path(config["generator"]["output_dir"])
    return configured if configured.is_absolute() else (REPO_ROOT / configured)


def validate_weights(name: str, weights: Dict[str, float]) -> None:
    if not weights:
        raise ValueError(f"{name} must not be empty")
    total = sum(weights.values())
    if total <= 0:
        raise ValueError(f"{name} weights must sum to > 0")
    for key, value in weights.items():
        if value < 0:
            raise ValueError(f"{name} weight for {key} must be non-negative")


def validate_config(config: Dict[str, Any]) -> None:
    required = [
        "generator",
        "regions",
        "distributions",
        "required_profiles",
        "tag_dimensions",
        "naming",
        "instance_targets",
        "usage_profiles",
        "finding_profiles",
        "action_catalog",
    ]
    for section in required:
        if section not in config:
            raise ValueError(f"Missing required config section: {section}")

    generator = config["generator"]
    for key in ["seed", "resource_count", "account_id", "default_region", "output_dir"]:
        if key not in generator:
            raise ValueError(f"Missing generator.{key}")
    if generator["resource_count"] <= 0:
        raise ValueError("generator.resource_count must be > 0")

    region_names = {region["name"] for region in config["regions"]}
    if generator["default_region"] not in region_names:
        raise ValueError("generator.default_region must be present in regions")

    for name in ["environments", "teams", "owners", "cost_centers", "business_units", "usage_profiles"]:
        validate_weights(f"distributions.{name}", config["distributions"][name])

    usage_profiles = config["usage_profiles"]
    for profile_name in config["required_profiles"]:
        if profile_name not in usage_profiles:
            raise ValueError(f"required_profiles references unknown profile {profile_name}")

    for profile_name, profile in usage_profiles.items():
        for key in [
            "finding_type",
            "severity",
            "default_instance_type",
            "min_size",
            "desired_capacity",
            "max_size",
            "avg_cpu_utilization",
            "network_gb_total",
            "on_demand_instances",
            "spot_instances",
            "scheduled_actions_count",
            "high_desired_schedules_count",
            "max_scheduled_desired_capacity",
            "scale_out_activities",
            "scale_in_activities",
            "warm_pool_instances",
            "inservice_instances",
            "instance_types_detected",
            "allowed_families",
            "target_desired_capacity",
        ]:
            if key not in profile:
                raise ValueError(f"usage_profiles.{profile_name}.{key} is required")
        check_id = profile.get("check_id")
        if check_id is not None and check_id not in config["finding_profiles"]:
            raise ValueError(f"usage_profiles.{profile_name}.check_id references unknown finding profile")

    for name, finding in config["finding_profiles"].items():
        for key in [
            "title",
            "finding_type",
            "severity",
            "confidence_range",
            "risk_score_range",
            "recommended_action",
            "alternative_actions",
            "savings_factor",
            "reason_template",
        ]:
            if key not in finding:
                raise ValueError(f"finding_profiles.{name}.{key} is required")
        for action_key in [finding["recommended_action"], *finding["alternative_actions"]]:
            if action_key not in config["action_catalog"]:
                raise ValueError(f"finding_profiles.{name} references unknown action {action_key}")


def weighted_choice(
    rng: random.Random,
    weights: Dict[str, float],
    allowed: Optional[Iterable[str]] = None,
) -> str:
    allowed_set = set(allowed) if allowed is not None else None
    filtered = {
        key: value for key, value in weights.items() if allowed_set is None or key in allowed_set
    }
    validate_weights("weighted_choice", filtered)
    keys = list(filtered.keys())
    return rng.choices(keys, weights=[filtered[key] for key in keys], k=1)[0]


def choice(rng: random.Random, values: List[str]) -> str:
    return values[rng.randrange(0, len(values))]


def rand_range(rng: random.Random, low: float, high: float, digits: int = 2) -> float:
    if low == high:
        return round(low, digits)
    return round(rng.uniform(low, high), digits)


def deterministic_generated_at(seed: int) -> datetime:
    base = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    return base + timedelta(seconds=seed % 86400)


def _history_timestamps(anchor: datetime) -> List[str]:
    base = anchor.astimezone(timezone.utc).replace(hour=14, minute=0, second=0, microsecond=0)
    return [
        (base - timedelta(days=HISTORY_SERIES_SPACING_DAYS * index)).isoformat()
        for index in range(HISTORY_SERIES_COUNT)
    ]


def _slug(parts: List[str]) -> str:
    text = "-".join(part.strip().lower().replace("_", "-") for part in parts if part)
    allowed = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in text)
    cleaned = "-".join(filter(None, allowed.split("-")))
    return cleaned.rstrip("-")


def _trim_identifier(text: str, max_length: int) -> str:
    return text[:max_length].rstrip("-")


def is_graviton(instance_type: str) -> bool:
    family = str(instance_type or "").split(".", 1)[0].lower()
    return "g" in family[1:]


def parse_generation(instance_type: str) -> Optional[int]:
    family = str(instance_type or "").split(".", 1)[0].lower()
    digits = "".join(ch for ch in family[1:] if ch.isdigit())
    return int(digits) if digits else None


def build_metric_history(anchor: datetime, profile: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
    timestamps = list(reversed(_history_timestamps(anchor)))
    cpu_avg = float(profile["avg_cpu_utilization"])
    memory_avg = float(profile.get("avg_memory_utilization") or min(92.0, max(8.0, cpu_avg * 1.2 + 10.0)))
    network_gb = float(profile["network_gb_total"])
    desired = int(profile["desired_capacity"])
    warm = int(profile["warm_pool_instances"])
    scheduled = int(profile["max_scheduled_desired_capacity"])

    cpu_series = [round(max(0.1, cpu_avg + rng.uniform(-3.5, 3.5)), 2) for _ in timestamps]
    memory_series = [round(max(0.1, min(100.0, memory_avg + rng.uniform(-4.0, 4.0))), 2) for _ in timestamps]
    desired_series = [desired for _ in timestamps]
    traffic_series = [round(max(0.0, network_gb + rng.uniform(-2.5, 2.5)), 3) for _ in timestamps]
    warm_series = [warm for _ in timestamps]
    scheduled_series = [scheduled for _ in timestamps]

    return {
        "cpuutilization": {
            "timestamps": timestamps,
            "average": cpu_series,
            "maximum": [round(value * 1.35, 2) for value in cpu_series],
            "p95": [round(value * 1.22, 2) for value in cpu_series],
            "p99": [round(value * 1.3, 2) for value in cpu_series],
        },
        "memoryutilization": {
            "timestamps": timestamps,
            "average": memory_series,
            "maximum": [round(min(100.0, value * 1.24), 2) for value in memory_series],
            "p95": [round(min(100.0, value * 1.14), 2) for value in memory_series],
            "p99": [round(min(100.0, value * 1.2), 2) for value in memory_series],
        },
        "cpu_utilization": {"timestamps": timestamps, "average": cpu_series},
        "memory_utilization": {"timestamps": timestamps, "average": memory_series},
        "network_gb_total": {"timestamps": timestamps, "sum": traffic_series},
        "desired_capacity": {"timestamps": timestamps, "maximum": desired_series},
        "warm_pool_instances": {"timestamps": timestamps, "maximum": warm_series},
        "scheduled_desired_capacity": {"timestamps": timestamps, "maximum": scheduled_series},
    }


def build_requirement(value: int, binding: str = "memory") -> Dict[str, Any]:
    return {
        "timestamp": "2026-07-01T00:00:00+00:00",
        "required": value,
        "binding_dimension": binding,
        "cpu_used_instances": round(max(0.1, value * 0.55), 3),
        "memory_used_instances": round(max(0.1, value * 0.6), 3),
    }


def build_rightsizing_metrics(profile: Dict[str, Any]) -> Dict[str, Any]:
    desired = int(profile["desired_capacity"])
    target_desired = int(profile["target_desired_capacity"])
    target_min = max(1, min(int(profile["min_size"]), target_desired))
    if target_desired >= desired:
        target_desired = desired
        target_min = min(int(profile["min_size"]), target_desired)

    windows: Dict[str, Any] = {}
    for days in (14, 30, 60):
        required_capacity = {}
        for tier, multiplier in (
            ("conservative", 1.15),
            ("balanced", 1.0),
            ("aggressive", 0.85),
        ):
            tier_desired = max(1, round(target_desired * multiplier))
            if target_desired >= desired:
                tier_desired = desired
            tier_min = max(1, min(target_min, tier_desired))
            desired_record = build_requirement(tier_desired)
            min_record = build_requirement(tier_min)
            required_capacity[tier] = {
                "p50": tier_min,
                "p99": tier_desired,
                "maximum": tier_desired,
                "sample_count": days * 288,
                "p50_record": min_record,
                "p99_record": desired_record,
            }
        windows[f"{days}d"] = {
            "lookback_days": days,
            "period_seconds": 300,
            "normalized": {
                "required_capacity": required_capacity,
            },
            "coverage": {
                "cpu_in_service_pairing_ratio": 1.0,
                "memory_in_service_pairing_ratio": 1.0,
            },
            "signals": {},
            "normalization_version": "asg-v1-synthetic",
        }
    return windows


def build_telemetry_summary(profile_name: str) -> Dict[str, Any]:
    telemetry = {
        "cpu_percent": {
            "present": True,
            "observed_days": 60.0,
            "thin_data": False,
            "pairing_ratio": 1.0,
        },
        "memory_percent": {
            "present": True,
            "observed_days": 60.0,
            "thin_data": False,
            "pairing_ratio": 1.0,
            "source": {
                "kind": "group",
                "namespace": "CWAgent",
                "metric_name": "mem_used_percent",
            },
            "status": "usable",
        },
        "desired_capacity": {
            "present": True,
            "observed_days": 60.0,
            "thin_data": False,
        },
        "in_service_instances": {
            "present": True,
            "observed_days": 60.0,
            "thin_data": False,
        },
    }
    if profile_name == "low_traffic_with_running_instances":
        telemetry["memory_percent"].update(
            present=False,
            observed_days=0.0,
            thin_data=True,
            pairing_ratio=None,
            source=None,
            status="unavailable",
        )
    elif profile_name == "on_demand_heavy_mix":
        telemetry["cpu_percent"].update(
            present=False,
            observed_days=0.0,
            thin_data=True,
            pairing_ratio=None,
        )
    return telemetry


def build_rightsizer_scope_metadata(
    *,
    profile_name: str,
    profile: Dict[str, Any],
    region_cfg: Dict[str, Any],
    desired: int,
) -> Dict[str, Any]:
    instance_type = profile["default_instance_type"]
    in_service = int(profile["inservice_instances"])
    availability_zones = region_cfg["azs"][: max(1, min(2, len(region_cfg["azs"]), desired))]
    metadata = {
        "availability_zones": availability_zones,
        "instance_ids": [f"i-syn-{profile_name}-{index:03d}" for index in range(desired)],
        "in_service_instance_ids": [
            f"i-syn-{profile_name}-{index:03d}" for index in range(in_service)
        ],
        "effective_instance_types": [instance_type],
        "instance_lifecycles": ["on-demand"],
        "mixed_instances_policy_present": False,
        "weighted_capacity_present": False,
        "warm_pool_present": False,
        "scheduled_actions_present": False,
        "predictive_scaling_present": False,
        "instance_refresh_in_progress": False,
        "scale_in_protection_present": False,
        "suspended_processes": [],
        "capacity_rebalance": False,
        "dynamic_policy_kinds": ["TargetTrackingScaling"],
        "dynamic_policy_metrics": ["ASGAverageCPUUtilization"],
        "operational_signals": {
            "scaling_failure_seen": False,
            "capacity_shortage_seen": False,
            "desired_in_service_mismatch_seen": False,
        },
    }
    if profile_name == "idle_capacity_high":
        metadata["operational_signals"]["desired_in_service_mismatch_seen"] = True
    elif profile_name == "low_traffic_with_running_instances":
        metadata["availability_zones"] = region_cfg["azs"][:1]
    elif profile_name == "scale_in_never_triggered":
        metadata["dynamic_policy_kinds"] = []
        metadata["dynamic_policy_metrics"] = []
    elif profile_name == "scheduled_scaling_mismatch":
        metadata["scheduled_actions_present"] = True
    elif profile_name == "warm_pool_oversized":
        metadata["warm_pool_present"] = True
    elif profile_name == "non_graviton_candidates":
        metadata["effective_instance_types"] = [instance_type, "m5.xlarge"]
    return metadata


def build_target_config(
    profile_name: str,
    profile: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    current_type = profile["default_instance_type"]
    targets = config["instance_targets"]
    desired_target = int(profile["target_desired_capacity"])
    target: Dict[str, Any] = {"desired_capacity": desired_target}
    if profile_name == "non_graviton_candidates":
        target["instance_type"] = targets["graviton_equivalents"].get(current_type, current_type)
    elif profile_name == "launch_template_old_generation":
        target["instance_type"] = targets["next_generation"].get(current_type, current_type)
    elif profile_name in {"low_cpu_overprovisioned", "idle_capacity_high", "low_traffic_with_running_instances"}:
        target["instance_type"] = targets["smaller_sizes"].get(current_type, current_type)
    elif profile_name == "on_demand_heavy_mix":
        target["spot_ratio"] = 0.4
    elif profile_name == "scale_in_never_triggered":
        target["scale_in_policy"] = "aggressive-after-cooldown"
    elif profile_name == "scheduled_scaling_mismatch":
        target["scheduled_desired_capacity"] = desired_target
    elif profile_name == "warm_pool_oversized":
        target["warm_pool_size"] = max(0, int(profile["warm_pool_instances"]) - 2)
    else:
        target["instance_type"] = current_type
    return target


def build_available_actions(
    action_catalog: Dict[str, Dict[str, Any]],
    finding_profile: Dict[str, Any],
    monthly_savings: float,
    yearly_savings: float,
    target_config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    actions = [finding_profile["recommended_action"], *finding_profile["alternative_actions"]]
    rendered: List[Dict[str, Any]] = []
    for index, action_key in enumerate(actions):
        details = deepcopy(action_catalog[action_key])
        parameters = deepcopy(details.get("parameters", {}))
        if action_key == "migrate_to_graviton_candidates" and target_config.get("instance_type"):
            parameters["target_instance_type"] = target_config["instance_type"]
        if action_key == "upgrade_instance_generation" and target_config.get("instance_type"):
            parameters["target_instance_type"] = target_config["instance_type"]
        if action_key in {"reduce_desired_or_instance_size", "reduce_idle_capacity"}:
            parameters["target_desired_capacity"] = target_config.get("desired_capacity")
        if action_key == "adjust_scheduled_scaling":
            parameters["target_scheduled_desired_capacity"] = target_config.get("scheduled_desired_capacity")
        if action_key == "reduce_warm_pool_size":
            parameters["target_warm_pool_size"] = target_config.get("warm_pool_size")
        if action_key == "increase_spot_mix_safely":
            parameters["target_spot_ratio"] = target_config.get("spot_ratio")
        details.update(
            {
                "action_key": action_key,
                "estimated_monthly_savings": monthly_savings,
                "estimated_annual_savings": yearly_savings,
                "is_recommended": index == 0,
                "parameters": parameters,
            }
        )
        rendered.append(details)
    return rendered


def build_asg_payload(
    *,
    inventory_id: int,
    resource_id: str,
    profile: Dict[str, Any],
    region_cfg: Dict[str, Any],
    created_at: datetime,
) -> Dict[str, Any]:
    azs = region_cfg["azs"]
    desired = int(profile["desired_capacity"])
    warm = int(profile["warm_pool_instances"])
    on_demand = int(profile["on_demand_instances"])
    spot = int(profile["spot_instances"])
    instance_type = profile["default_instance_type"]
    instances = []
    for idx in range(desired):
        lifecycle = "spot" if idx >= on_demand and spot > 0 else None
        instances.append(
            {
                "InstanceId": f"i-{inventory_id:08x}{idx:02d}",
                "InstanceType": instance_type,
                "AvailabilityZone": azs[idx % len(azs)],
                "LifecycleState": "InService",
                "HealthStatus": "Healthy",
                "ProtectedFromScaleIn": False,
                **({"InstanceLifecycle": "spot"} if lifecycle == "spot" else {}),
            }
        )
    warm_pool = [
        {
            "InstanceId": f"i-{inventory_id:08x}wp{idx:02d}",
            "InstanceType": instance_type,
            "LifecycleState": "Warmed:Stopped",
            "HealthStatus": "Healthy",
        }
        for idx in range(warm)
    ]
    scheduled_actions = [
        {
            "ScheduledActionName": f"{resource_id}-peak-{idx + 1}",
            "DesiredCapacity": int(profile["max_scheduled_desired_capacity"]),
        }
        for idx in range(int(profile["scheduled_actions_count"]))
    ]
    return {
        "InventoryId": inventory_id,
        "AutoScalingGroupName": resource_id,
        "AutoScalingGroupARN": f"arn:aws:autoscaling:{region_cfg['name']}:999999999999:autoScalingGroup:*:autoScalingGroupName/{resource_id}",
        "CreatedTime": created_at.isoformat(),
        "MinSize": int(profile["min_size"]),
        "DesiredCapacity": desired,
        "MaxSize": int(profile["max_size"]),
        "DefaultCooldown": 300,
        "AvailabilityZones": azs[: min(len(azs), max(1, desired))],
        "HealthCheckType": "EC2",
        "HealthCheckGracePeriod": 300,
        "Instances": instances,
        "LaunchTemplate": {
            "LaunchTemplateName": f"{resource_id}-lt",
            "Version": "12",
        },
        "Tags": [
            {"Key": "Name", "Value": resource_id, "PropagateAtLaunch": True},
        ],
        "WarmPoolConfiguration": {
            "MinSize": warm,
            "PoolState": "Stopped",
        },
        "WarmPoolInstances": warm_pool,
        "ScheduledUpdateGroupActions": scheduled_actions,
    }


def build_row(
    *,
    inventory_id: int,
    config: Dict[str, Any],
    region_cfg: Dict[str, Any],
    profile_name: str,
    profile: Dict[str, Any],
    rng: random.Random,
    generated_at: datetime,
) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    naming = config["naming"]
    app = choice(rng, naming["apps"])
    role = choice(rng, naming["roles"])
    env = weighted_choice(rng, config["distributions"]["environments"])
    team = weighted_choice(rng, config["distributions"]["teams"])
    owner = weighted_choice(rng, config["distributions"]["owners"])
    cost_center = weighted_choice(rng, config["distributions"]["cost_centers"])
    business_unit = weighted_choice(rng, config["distributions"]["business_units"])
    resource_id = _trim_identifier(
        _slug([naming["org_prefix"], env, app, role, "asg", f"{inventory_id:03d}"]),
        255,
    )
    resource_name = resource_id
    created_at = generated_at - timedelta(days=30 + inventory_id * 2)
    target_config = build_target_config(profile_name, profile, config)

    desired = int(profile["desired_capacity"])
    warm = int(profile["warm_pool_instances"])
    monthly_cost = round(
        estimate_ec2_monthly_cost(profile["default_instance_type"]) * max(1, desired + warm),
        2,
    )
    metric_history = build_metric_history(generated_at, profile, rng)
    rightsizer_scope = build_rightsizer_scope_metadata(
        profile_name=profile_name,
        profile=profile,
        region_cfg=region_cfg,
        desired=desired,
    )
    tags = {
        "env": env,
        "team": team,
        "owner": owner,
        "cost_center": cost_center,
        "business_unit": business_unit,
        "criticality": choice(rng, config["tag_dimensions"]["criticality"]),
        "compliance": choice(rng, config["tag_dimensions"]["compliance"]),
        "uptime_pattern": choice(rng, config["tag_dimensions"]["uptime_pattern"]),
        "patch_group": choice(rng, config["tag_dimensions"]["patch_group"]),
    }

    metadata = {
        "usage_profile": profile_name,
        "environment": env,
        "team": team,
        "owner": owner,
        "cost_center": cost_center,
        "business_unit": business_unit,
        "criticality": tags["criticality"],
        "min_size": int(profile["min_size"]),
        "desired_capacity": desired,
        "max_size": int(profile["max_size"]),
        "instance_type": profile["default_instance_type"],
        "launch_template_instance_type": profile["default_instance_type"],
        "platform_normalized": profile["platform_normalized"],
        "avg_cpu_utilization": float(profile["avg_cpu_utilization"]),
        "network_gb_total": float(profile["network_gb_total"]),
        "on_demand_instances": int(profile["on_demand_instances"]),
        "spot_instances": int(profile["spot_instances"]),
        "on_demand_ratio": round(
            int(profile["on_demand_instances"]) / max(1, int(profile["on_demand_instances"]) + int(profile["spot_instances"])),
            3,
        ),
        "scheduled_actions_count": int(profile["scheduled_actions_count"]),
        "high_desired_schedules_count": int(profile["high_desired_schedules_count"]),
        "max_scheduled_desired_capacity": int(profile["max_scheduled_desired_capacity"]),
        "scale_out_activities": int(profile["scale_out_activities"]),
        "scale_in_activities": int(profile["scale_in_activities"]),
        "warm_pool_instances": warm,
        "inservice_instances": int(profile["inservice_instances"]),
        "instance_types_detected": list(profile["instance_types_detected"]),
        "old_generation_instance_types": list(profile.get("old_generation_instance_types") or []),
        "allowed_families": list(profile["allowed_families"]),
        "monthly_cost_estimate": monthly_cost,
        "metric_history": metric_history,
        "rightsizing_metrics": build_rightsizing_metrics(profile),
        "telemetry_summary": build_telemetry_summary(profile_name),
        "resource_kind": "asg",
        "current_config_snapshot": {
            "instance_type": profile["default_instance_type"],
            "min_size": int(profile["min_size"]),
            "desired_capacity": desired,
            "max_size": int(profile["max_size"]),
            "warm_pool_size": warm,
        },
        "synthetic_rightsizer_case": profile_name,
        **rightsizer_scope,
    }

    if profile_name == "low_cpu_overprovisioned":
        metadata["cpu_threshold"] = 20.0
        metadata["extra_capacity_above_min"] = desired - int(profile["min_size"])
    elif profile_name == "idle_capacity_high":
        metadata["target_cpu_utilization"] = 50.0
        metadata["estimated_needed_capacity"] = 2
        metadata["estimated_idle_instances"] = desired - 2
    elif profile_name == "low_traffic_with_running_instances":
        metadata["running_instances"] = desired
        metadata["max_avg_cpu"] = 10.0
        metadata["max_network_gb_total"] = 1.0
    elif profile_name == "on_demand_heavy_mix":
        metadata["min_on_demand_ratio"] = 0.8
    elif profile_name == "launch_template_old_generation":
        metadata["min_generation"] = 6
    elif profile_name == "scale_in_never_triggered":
        metadata["min_scale_out_activities"] = 3
    elif profile_name == "scheduled_scaling_mismatch":
        metadata["lookback_days"] = 28
        metadata["max_cpu_during_schedule"] = 20.0
    elif profile_name == "warm_pool_oversized":
        metadata["warm_to_inservice_ratio"] = round(warm / max(1, int(profile["inservice_instances"])), 3)
        metadata["max_warm_to_inservice_ratio"] = 1.0

    inventory_row = {
        "inventory_id": inventory_id,
        "resource_id": resource_id,
        "resource_name": resource_name,
        "resource_type": "asg",
        "account_id": config["generator"]["account_id"],
        "region": region_cfg["name"],
        "state": "active",
        "min_size": int(profile["min_size"]),
        "desired_capacity": desired,
        "max_size": int(profile["max_size"]),
        "instance_type": profile["default_instance_type"],
        "platform_normalized": profile["platform_normalized"],
        "tags": tags,
        "metadata": metadata,
    }

    aws_payload = build_asg_payload(
        inventory_id=inventory_id,
        resource_id=resource_id,
        profile=profile,
        region_cfg=region_cfg,
        created_at=created_at,
    )
    return inventory_row, aws_payload, target_config


def build_maxops_row(
    *,
    inventory_row: Dict[str, Any],
    target_config: Dict[str, Any],
    config: Dict[str, Any],
    profile_name: str,
    profile: Dict[str, Any],
    rng: random.Random,
) -> Dict[str, Any]:
    check_id = profile.get("check_id")
    metadata = inventory_row["metadata"]
    monthly_cost = float(metadata["monthly_cost_estimate"])

    if not check_id:
        return {
            "inventory_id": inventory_row["inventory_id"],
            "check_id": None,
            "finding_type": None,
            "title": None,
            "description": None,
            "severity": None,
            "confidence_score": None,
            "risk_score": None,
            "recommended_action": None,
            "recommended_actions": [],
            "available_actions": [],
            "potential_savings_monthly": 0.0,
            "potential_savings_yearly": 0.0,
            "evidence": {
                "avg_cpu_utilization": metadata["avg_cpu_utilization"],
                "network_gb_total": metadata["network_gb_total"],
                "instance_type": metadata["instance_type"],
            },
            "current_config": metadata["current_config_snapshot"],
            "target_config": metadata["current_config_snapshot"],
            "metadata": {
                "status": "healthy",
                "usage_profile": profile_name,
                "monthly_cost_estimate": monthly_cost,
                "resource_id": inventory_row["resource_id"],
                "resource_name": inventory_row["resource_name"],
                "environment": metadata["environment"],
                "team": metadata["team"],
                "owner": metadata["owner"],
            },
        }

    finding_profile = config["finding_profiles"][check_id]
    confidence = round(rand_range(rng, *finding_profile["confidence_range"], digits=3), 3)
    risk_score = round(rand_range(rng, *finding_profile["risk_score_range"], digits=3), 3)
    monthly_savings = round(monthly_cost * float(finding_profile["savings_factor"]), 2)
    yearly_savings = round(monthly_savings * 12, 2)
    description = finding_profile["reason_template"].format(**metadata)
    available_actions = build_available_actions(
        config["action_catalog"],
        finding_profile,
        monthly_savings,
        yearly_savings,
        target_config,
    )

    return {
        "inventory_id": inventory_row["inventory_id"],
        "check_id": check_id,
        "finding_type": finding_profile["finding_type"],
        "title": finding_profile["title"],
        "description": description,
        "severity": profile["severity"],
        "confidence_score": confidence,
        "risk_score": risk_score,
        "recommended_action": finding_profile["recommended_action"],
        "recommended_actions": [finding_profile["recommended_action"], *finding_profile["alternative_actions"]],
        "available_actions": available_actions,
        "potential_savings_monthly": monthly_savings,
        "potential_savings_yearly": yearly_savings,
        "evidence": {
            "avg_cpu_utilization": metadata["avg_cpu_utilization"],
            "network_gb_total": metadata["network_gb_total"],
            "instance_type": metadata["instance_type"],
            "desired_capacity": metadata["desired_capacity"],
            "check_id": check_id,
        },
        "current_config": metadata["current_config_snapshot"],
        "target_config": target_config,
        "metadata": {
            "status": "actionable",
            "usage_profile": profile_name,
            "monthly_cost_estimate": monthly_cost,
            "resource_id": inventory_row["resource_id"],
            "resource_name": inventory_row["resource_name"],
            "environment": metadata["environment"],
            "team": metadata["team"],
            "owner": metadata["owner"],
            "recommended_action": finding_profile["recommended_action"],
        },
    }


def generate_dataset(
    config: Dict[str, Any],
    count_override: Optional[int] = None,
    seed_override: Optional[int] = None,
) -> GeneratedArtifacts:
    validate_config(config)
    count = int(count_override or config["generator"]["resource_count"])
    seed = int(seed_override if seed_override is not None else config["generator"]["seed"])
    rng = random.Random(seed)
    generated_at = deterministic_generated_at(seed)

    inventories: List[Dict[str, Any]] = []
    maxops: List[Dict[str, Any]] = []
    aws_groups: List[Dict[str, Any]] = []

    required_profiles = list(config["required_profiles"])
    weighted_profiles = config["distributions"]["usage_profiles"]
    profile_order = required_profiles[:]
    while len(profile_order) < count:
        profile_order.append(weighted_choice(rng, weighted_profiles))

    for inventory_id in range(1, count + 1):
        profile_name = profile_order[inventory_id - 1]
        profile = config["usage_profiles"][profile_name]
        region_name = weighted_choice(
            rng,
            {region["name"]: region["weight"] for region in config["regions"]},
        )
        region_cfg = next(region for region in config["regions"] if region["name"] == region_name)
        inventory_row, aws_payload, target_config = build_row(
            inventory_id=inventory_id,
            config=config,
            region_cfg=region_cfg,
            profile_name=profile_name,
            profile=profile,
            rng=rng,
            generated_at=generated_at,
        )
        maxops_row = build_maxops_row(
            inventory_row=inventory_row,
            target_config=target_config,
            config=config,
            profile_name=profile_name,
            profile=profile,
            rng=rng,
        )
        inventories.append(inventory_row)
        aws_groups.append(aws_payload)
        maxops.append(maxops_row)

    inventory_doc = {
        "generated_at": generated_at.isoformat(),
        "account_id": config["generator"]["account_id"],
        "inventory_count": len(inventories),
        "aws_payload": {
            "AutoScalingGroups": aws_groups,
            "MetricDetails": {
                str(row["inventory_id"]): row["metadata"]["metric_history"] for row in inventories
            },
        },
        "resources": inventories,
    }
    return GeneratedArtifacts(inventory=inventory_doc, maxops=maxops)


def write_artifacts(artifacts: GeneratedArtifacts, output_dir: Path) -> None:
    dump_json(output_dir / "inventory.json", artifacts.inventory)
    dump_json(output_dir / "asg_maxops.json", artifacts.maxops)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate deterministic synthetic ASG DB import data.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to ASG synthetic config JSON.")
    parser.add_argument("--count", type=int, default=None, help="Override resource count.")
    parser.add_argument("--seed", type=int, default=None, help="Override RNG seed.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Override output directory.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_json(args.config)
    artifacts = generate_dataset(config, count_override=args.count, seed_override=args.seed)
    output_dir = resolve_output_dir(config, args.output_dir)
    write_artifacts(artifacts, output_dir)
    print(f"Wrote ASG synthetic artifacts to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
