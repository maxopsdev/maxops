"""Generate deterministic, DB-import-friendly RDS synthetic data from JSON config."""

from __future__ import annotations

import argparse
import importlib.util
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


CONFIG_PATH = CURRENT_DIR / "rds_syn_config.json"
PAYLOAD_ROOT = REPO_ROOT / "tests" / "payloads" / "rds"
METRIC_ALIASES = {
    "cpuutilization": "CPUUtilization",
    "databaseconnections": "DatabaseConnections",
    "readiops": "ReadIOPS",
    "writeiops": "WriteIOPS",
}
METRIC_FILE_KEYS = {name.lower(): alias for alias, name in METRIC_ALIASES.items()}
HISTORY_SERIES_COUNT = 15
HISTORY_SERIES_SPACING_DAYS = 30


def load_estimate_rds_monthly_cost():
    module_path = REPO_ROOT / "app" / "checks" / "base.py"
    spec = importlib.util.spec_from_file_location("maxops_check_base", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.estimate_rds_monthly_cost


estimate_rds_monthly_cost = load_estimate_rds_monthly_cost()


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
        "tag_dimensions",
        "naming",
        "engine_profiles",
        "instance_class_profiles",
        "usage_profiles",
        "finding_profiles",
        "action_catalog",
        "seed_profiles",
    ]
    for section in required:
        if section not in config:
            raise ValueError(f"Missing required config section: {section}")

    generator = config["generator"]
    for key in ["seed", "instance_count", "account_id", "default_region", "output_dir"]:
        if key not in generator:
            raise ValueError(f"Missing generator.{key}")
    if generator["instance_count"] <= 0:
        raise ValueError("generator.instance_count must be > 0")

    region_names = {region["name"] for region in config["regions"]}
    if generator["default_region"] not in region_names:
        raise ValueError("generator.default_region must be present in regions")

    for name in [
        "environments",
        "teams",
        "owners",
        "cost_centers",
        "business_units",
        "engine_families",
        "instance_class_families",
        "usage_profiles",
    ]:
        validate_weights(f"distributions.{name}", config["distributions"][name])

    for name, profile in config["instance_class_profiles"].items():
        validate_weights(f"instance_class_profiles.{name}.classes", profile["classes"])
        finding_profile = profile.get("finding_profile")
        if finding_profile and finding_profile not in config["finding_profiles"]:
            raise ValueError(f"instance_class_profiles.{name}.finding_profile references unknown finding profile")

    for name, profile in config["usage_profiles"].items():
        for key in [
            "cpu_range",
            "memory_range",
            "connections_range",
            "read_iops_range",
            "write_iops_range",
            "metric_history_pattern",
        ]:
            if key not in profile:
                raise ValueError(f"usage_profiles.{name}.{key} is required")
        finding_profile = profile.get("finding_profile")
        if finding_profile and finding_profile not in config["finding_profiles"]:
            raise ValueError(f"usage_profiles.{name}.finding_profile references unknown finding profile")

    for name, finding in config["finding_profiles"].items():
        for key in [
            "check_id",
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


def weighted_choice(rng: random.Random, weights: Dict[str, float], allowed: Optional[Iterable[str]] = None) -> str:
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


def rand_int(rng: random.Random, low: int, high: int) -> int:
    if low == high:
        return low
    return rng.randint(low, high)


def deterministic_generated_at(seed: int) -> datetime:
    base = datetime(2026, 4, 19, 15, 0, tzinfo=timezone.utc)
    return base + timedelta(seconds=seed % 86400)


def _history_timestamps(anchor: datetime) -> List[str]:
    base = anchor.astimezone(timezone.utc).replace(hour=15, minute=0, second=0, microsecond=0)
    return [
        (base - timedelta(days=HISTORY_SERIES_SPACING_DAYS * index)).isoformat()
        for index in range(HISTORY_SERIES_COUNT)
    ]


def _replace_strings(value: Any, replacements: Dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _replace_strings(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_strings(item, replacements) for item in value]
    if isinstance(value, str):
        updated = value
        for actual, replacement in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
            updated = updated.replace(actual, replacement)
        return updated
    return value


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except TypeError:
            return value
    return value


def _slug(parts: List[str]) -> str:
    text = "-".join(part.strip().lower().replace("_", "-") for part in parts if part)
    allowed = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in text)
    cleaned = "-".join(filter(None, allowed.split("-")))
    return cleaned[:63].rstrip("-")


def is_graviton(instance_class: str) -> bool:
    parts = instance_class.strip().lower().split(".")
    if len(parts) < 3:
        return False
    return "g" in parts[1]


def get_graviton_equivalent(instance_class: str) -> Optional[str]:
    parts = instance_class.strip().lower().split(".")
    if len(parts) < 3:
        return None
    family = parts[1]
    size = ".".join(parts[2:])
    mapping = {
        "m5": "m6g",
        "m4": "m6g",
        "t3": "t4g",
        "t2": "t4g",
        "r5": "r6g",
        "r4": "r6g",
        "x1": "x2gd",
    }
    graviton_family = mapping.get(family)
    if not graviton_family:
        return None
    return f"db.{graviton_family}.{size}"


def _load_seed_payloads(profile: Dict[str, Any]) -> Dict[str, Any]:
    scenario_dir = PAYLOAD_ROOT / profile["seed_check_id"] / profile["seed_scenario"]
    payloads: Dict[str, Any] = {}
    for path in scenario_dir.glob("*.json"):
        payloads[path.name] = load_json(path)
    return payloads


def _seed_bundle(config: Dict[str, Any], name: str) -> Dict[str, Any]:
    profile = config["seed_profiles"][name]
    payloads = _load_seed_payloads(profile)
    describe_payload = deepcopy(payloads["describe_db_instances.json"])
    db_instance = deepcopy(describe_payload["DBInstances"][0])
    alias = profile["seed_db_alias"]
    tags_payload = deepcopy(payloads[f"list_tags_for_resource__{alias}.json"])
    metrics = {}
    for metric_key in METRIC_ALIASES:
        filename = f"get_metric_statistics__{alias}__{metric_key}.json"
        if filename in payloads:
            metrics[metric_key] = deepcopy(payloads[filename])
    return {
        "profile": profile,
        "describe_payload": describe_payload,
        "db_instance": db_instance,
        "tags_payload": tags_payload,
        "metric_payloads": metrics,
    }


def load_seed_bundles(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {name: _seed_bundle(config, name) for name in config["seed_profiles"]}


def _profile_sequence(config: Dict[str, Any], count: int, rng: random.Random) -> List[str]:
    sequence: List[str] = []
    if count >= 6:
        sequence.extend([
            "idle_candidate",
            "busy_healthy",
            "non_graviton_candidate",
            "steady_healthy",
            "idle_missing_metrics",
            "non_graviton_candidate",
        ])
    while len(sequence) < count:
        sequence.append(weighted_choice(rng, config["distributions"]["usage_profiles"]))
    rng.shuffle(sequence)
    return sequence[:count]


def _engine_sequence(config: Dict[str, Any], count: int, rng: random.Random) -> List[str]:
    sequence = list(config["engine_profiles"].keys()) if count >= len(config["engine_profiles"]) else []
    while len(sequence) < count:
        sequence.append(weighted_choice(rng, config["distributions"]["engine_families"]))
    rng.shuffle(sequence)
    return sequence[:count]


def _instance_family_for_usage(config: Dict[str, Any], usage_profile_name: str, rng: random.Random) -> str:
    weights = config["distributions"]["instance_class_families"]
    if usage_profile_name == "non_graviton_candidate":
        allowed = [name for name in weights if name != "graviton_burstable"]
        return weighted_choice(rng, weights, allowed=allowed)
    if usage_profile_name in {"idle_candidate", "idle_missing_metrics"}:
        return "graviton_burstable" if rng.random() < 0.7 else weighted_choice(rng, weights)
    return weighted_choice(rng, weights)


def _instance_class_for_family(config: Dict[str, Any], family_name: str, rng: random.Random) -> str:
    return weighted_choice(rng, config["instance_class_profiles"][family_name]["classes"])


def _seed_name_for_usage(usage_profile_name: str, instance_class: str) -> str:
    if usage_profile_name == "idle_candidate":
        return "idle_graviton_capture"
    if usage_profile_name == "busy_healthy":
        return "busy_graviton_capture"
    if usage_profile_name == "idle_missing_metrics":
        return "idle_no_metrics_capture"
    if usage_profile_name == "non_graviton_candidate" and not is_graviton(instance_class):
        return "non_graviton_capture"
    if is_graviton(instance_class):
        return "graviton_compliant_capture"
    return "non_graviton_capture"


def _db_identifier(inventory_id: int, rng: random.Random, config: Dict[str, Any], env: str, role: str, engine: str) -> str:
    app = choice(rng, config["naming"]["apps"])
    suffix = f"{engine[:3]}-{inventory_id:03d}"
    return _slug([config["naming"]["org_prefix"], env, app, role, suffix])[:63]


def _metric_history(
    rng: random.Random,
    generated_at: datetime,
    usage_profile: Dict[str, Any],
    averages: Dict[str, float],
) -> Dict[str, Dict[str, List[float] | List[str]]]:
    timestamps = _history_timestamps(generated_at)
    history: Dict[str, Dict[str, List[float] | List[str]]] = {}
    pattern = usage_profile["metric_history_pattern"]
    for metric_key, average_value in averages.items():
        series: Dict[str, List[float] | List[str]] = {"timestamps": list(timestamps)}
        if pattern == "empty":
            series["average"] = []
            history[metric_key] = series
            continue

        if metric_key == "databaseconnections":
            digits = 0
            volatility = 0.18 if pattern == "bursty" else 0.08
        else:
            digits = 2
            volatility = 0.22 if pattern == "bursty" else 0.1

        values = []
        for index in range(HISTORY_SERIES_COUNT):
            trend = 1 + rng.uniform(-volatility, volatility)
            if pattern == "idle":
                trend *= rng.uniform(0.85, 1.1)
            elif pattern == "bursty":
                trend *= 1 + (0.15 if index % 4 == 0 else -0.05)
            value = max(0.0, average_value * trend)
            if digits == 0:
                values.append(int(round(value)))
            else:
                values.append(round(value, digits))
        series["average"] = values
        maximum_multiplier = 1.85 if pattern == "bursty" else 1.35
        p95_multiplier = 1.55 if pattern == "bursty" else 1.18
        p99_multiplier = 1.68 if pattern == "bursty" else 1.26
        if metric_key in {"cpuutilization", "memoryutilization"}:
            cap = 100.0
        else:
            cap = None
        maximums = []
        p95s = []
        p99s = []
        for value in values:
            numeric_value = float(value)
            maximum = numeric_value * maximum_multiplier
            p95 = numeric_value * p95_multiplier
            p99 = numeric_value * p99_multiplier
            if cap is not None:
                maximum = min(cap, maximum)
                p95 = min(cap, p95)
                p99 = min(cap, p99)
            if digits == 0:
                maximums.append(int(round(maximum)))
                p95s.append(int(round(p95)))
                p99s.append(int(round(p99)))
            else:
                maximums.append(round(maximum, digits))
                p95s.append(round(p95, digits))
                p99s.append(round(p99, digits))
        series["maximum"] = maximums
        series["p95"] = p95s
        series["p99"] = p99s
        history[metric_key] = series
    return history


def _datapoints(metric_key: str, average_value: float, generated_at: datetime, usage_profile_name: str) -> List[Dict[str, Any]]:
    if usage_profile_name == "idle_missing_metrics":
        return []
    digits = 0 if metric_key == "databaseconnections" else 2
    value: Any = int(round(average_value)) if digits == 0 else round(average_value, digits)
    return [
        {
            "Average": value,
            "Timestamp": generated_at.isoformat(),
            "Unit": "Count",
        }
    ]


def _apply_engine_profile(db_instance: Dict[str, Any], engine_name: str, engine_profile: Dict[str, Any], rng: random.Random) -> None:
    db_instance["Engine"] = engine_name
    db_instance["EngineVersion"] = engine_profile["engine_version"]
    db_instance["LicenseModel"] = engine_profile["license_model"]
    db_instance["StorageType"] = engine_profile["storage_type"]
    db_instance["AllocatedStorage"] = rand_int(
        rng,
        engine_profile["allocated_storage_range"][0],
        engine_profile["allocated_storage_range"][1],
    )
    db_instance["Iops"] = rand_int(rng, engine_profile["iops_range"][0], engine_profile["iops_range"][1])
    throughput = rand_int(rng, engine_profile["throughput_range"][0], engine_profile["throughput_range"][1])
    db_instance["StorageThroughput"] = throughput
    db_instance["Endpoint"]["Port"] = engine_profile["port"]
    db_instance["MasterUsername"] = "app_admin"
    db_instance["DBParameterGroups"] = [
        {
            "DBParameterGroupName": engine_profile["parameter_group"],
            "ParameterApplyStatus": "in-sync",
        }
    ]
    db_instance["OptionGroupMemberships"] = [
        {
            "OptionGroupName": engine_profile["option_group"],
            "Status": "in-sync",
        }
    ]


def _build_action_option(
    action_key: str,
    config: Dict[str, Any],
    resource: Dict[str, Any],
    finding_rule: Dict[str, Any],
) -> Dict[str, Any]:
    catalog = deepcopy(config["action_catalog"][action_key])
    option = {
        "action_key": action_key,
        "label": catalog["label"],
        "description": catalog["description"],
        "parameters": deepcopy(catalog.get("parameters", {})),
        "is_recommended": action_key == finding_rule["recommended_action"],
    }
    if action_key == "migrate_to_graviton_instance_class":
        target = get_graviton_equivalent(resource["db_instance_class"])
        if target:
            option["parameters"]["target_db_instance_class"] = target
    if action_key == "downsize_instance":
        option["parameters"]["target_db_instance_class"] = "db.t4g.micro"
    return option


def build_neutral_maxops_row(resource: Dict[str, Any]) -> Dict[str, Any]:
    metadata = resource["metadata"]
    return {
        "inventory_id": resource["inventory_id"],
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
            "avg_memory_utilization": metadata["avg_memory_utilization"],
            "avg_connections": metadata["avg_connections"],
            "engine": metadata["engine"],
            "db_instance_class": metadata["instance_class"],
        },
        "current_config": {
            "db_instance_class": metadata["instance_class"],
            "engine": metadata["engine"],
            "region": resource["region"],
            "storage_type": metadata["storage_type"],
        },
        "target_config": {},
        "metadata": {
            "status": "healthy",
            "resource_name": resource["resource_name"],
            "engine": metadata["engine"],
            "db_instance_class": metadata["instance_class"],
            "monthly_cost_estimate": metadata["monthly_cost_estimate"],
        },
    }


def derive_maxops_row(resource: Dict[str, Any], config: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
    usage_profile_name = resource["metadata"]["usage_profile"]
    instance_family = resource["metadata"]["instance_family"]
    usage_profile = config["usage_profiles"][usage_profile_name]
    family_profile = config["instance_class_profiles"][instance_family]
    finding_profile_name = usage_profile.get("finding_profile") or family_profile.get("finding_profile")
    if not finding_profile_name:
        return build_neutral_maxops_row(resource)

    finding_rule = deepcopy(config["finding_profiles"][finding_profile_name])
    metadata = resource["metadata"]
    current_monthly = metadata["monthly_cost_estimate"]
    savings_monthly = round(current_monthly * finding_rule["savings_factor"], 2)
    savings_yearly = round(savings_monthly * 12, 2)
    confidence = rand_range(rng, finding_rule["confidence_range"][0], finding_rule["confidence_range"][1], 3)
    risk_score = rand_range(rng, finding_rule["risk_score_range"][0], finding_rule["risk_score_range"][1], 3)

    action_keys = [finding_rule["recommended_action"], *finding_rule["alternative_actions"]]
    actions = [_build_action_option(action_key, config, resource, finding_rule) for action_key in action_keys]
    for action in actions:
        action["estimated_monthly_savings"] = savings_monthly
        action["estimated_annual_savings"] = savings_yearly

    target_config: Dict[str, Any] = {}
    if finding_rule["recommended_action"] == "migrate_to_graviton_instance_class":
        target = get_graviton_equivalent(resource["db_instance_class"])
        if target:
            target_config["db_instance_class"] = target
    if finding_rule["recommended_action"] == "downsize_instance":
        target_config["db_instance_class"] = "db.t4g.micro"
    if finding_rule["recommended_action"] == "stop":
        target_config["db_instance_status"] = "stopped"

    context = {
        "avg_cpu_utilization": metadata["avg_cpu_utilization"],
        "avg_connections": metadata["avg_connections"],
        "db_instance_class": metadata["instance_class"],
    }
    return {
        "inventory_id": resource["inventory_id"],
        "check_id": finding_rule["check_id"],
        "finding_type": finding_rule["finding_type"],
        "title": finding_rule["title"],
        "description": finding_rule["reason_template"].format(**context),
        "severity": finding_rule["severity"],
        "confidence_score": confidence,
        "risk_score": risk_score,
        "recommended_action": finding_rule["recommended_action"],
        "recommended_actions": action_keys,
        "available_actions": actions,
        "potential_savings_monthly": savings_monthly,
        "potential_savings_yearly": savings_yearly,
        "evidence": {
            "avg_cpu_utilization": metadata["avg_cpu_utilization"],
            "avg_memory_utilization": metadata["avg_memory_utilization"],
            "avg_connections": metadata["avg_connections"],
            "avg_read_iops": metadata["avg_read_iops"],
            "avg_write_iops": metadata["avg_write_iops"],
            "engine": metadata["engine"],
            "db_instance_class": metadata["instance_class"],
        },
        "current_config": {
            "db_instance_class": metadata["instance_class"],
            "engine": metadata["engine"],
            "region": resource["region"],
            "storage_type": metadata["storage_type"],
        },
        "target_config": target_config,
        "metadata": {
            "status": "actionable",
            "resource_name": resource["resource_name"],
            "resource_id": resource["resource_id"],
            "engine": metadata["engine"],
            "db_instance_class": metadata["instance_class"],
            "monthly_cost_estimate": metadata["monthly_cost_estimate"],
            "owner": metadata["owner"],
            "team": metadata["team"],
        },
    }


def build_resource(
    inventory_id: int,
    usage_profile_name: str,
    engine_name: str,
    rng: random.Random,
    config: Dict[str, Any],
    generated_at: datetime,
    seed_bundles: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    env = weighted_choice(rng, config["distributions"]["environments"])
    team = weighted_choice(rng, config["distributions"]["teams"])
    owner = weighted_choice(rng, config["distributions"]["owners"])
    cost_center = weighted_choice(rng, config["distributions"]["cost_centers"])
    business_unit = weighted_choice(rng, config["distributions"]["business_units"])
    selected_region = weighted_choice(
        rng,
        {region["name"]: region["weight"] for region in config["regions"]},
    )
    region_cfg = next(region for region in config["regions"] if region["name"] == selected_region)
    az = choice(rng, region_cfg["azs"])
    role = choice(rng, config["naming"]["roles"])

    instance_family = _instance_family_for_usage(config, usage_profile_name, rng)
    instance_class = _instance_class_for_family(config, instance_family, rng)
    usage_profile = config["usage_profiles"][usage_profile_name]
    seed_name = _seed_name_for_usage(usage_profile_name, instance_class)
    seed_bundle = seed_bundles[seed_name]
    db_instance = _replace_strings(deepcopy(seed_bundle["db_instance"]), {"us-east-1": selected_region})
    tags_payload = deepcopy(seed_bundle["tags_payload"])

    db_identifier = _db_identifier(inventory_id, rng, config, env, role, engine_name)
    account_id = config["generator"]["account_id"]
    subnet_group_name = f"maxops-demo-rds-subnet-{selected_region}-{inventory_id:03d}"
    endpoint = f"{db_identifier}.{inventory_id:06d}.{selected_region}.rds.amazonaws.com"
    resource_arn = f"arn:aws:rds:{selected_region}:{account_id}:db:{db_identifier}"
    dbi_resource_id = f"db-{''.join(rng.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(26))}"
    vpc_id = f"vpc-{inventory_id:08x}"
    subnet_ids = [f"subnet-{inventory_id:08x}{index}" for index in (1, 2)]
    security_group_id = f"sg-{inventory_id:08x}"
    create_time = generated_at - timedelta(days=rand_int(rng, 7, 780), hours=rand_int(rng, 0, 23))

    replacements = {
        seed_bundle["db_instance"]["DBInstanceIdentifier"]: db_identifier,
        seed_bundle["db_instance"]["DBInstanceArn"]: resource_arn,
        seed_bundle["db_instance"]["Endpoint"]["Address"]: endpoint,
        seed_bundle["db_instance"]["DBSubnetGroup"]["DBSubnetGroupName"]: subnet_group_name,
        seed_bundle["db_instance"]["DBSubnetGroup"]["VpcId"]: vpc_id,
        seed_bundle["db_instance"]["VpcSecurityGroups"][0]["VpcSecurityGroupId"]: security_group_id,
        seed_bundle["db_instance"]["DBSubnetGroup"]["Subnets"][0]["SubnetIdentifier"]: subnet_ids[0],
        seed_bundle["db_instance"]["DBSubnetGroup"]["Subnets"][1]["SubnetIdentifier"]: subnet_ids[1],
        "123456789012": account_id,
        "us-east-1": selected_region,
    }
    db_instance = _replace_strings(db_instance, replacements)
    tags_payload = _replace_strings(tags_payload, replacements)

    db_instance["DBInstanceClass"] = instance_class
    db_instance["AvailabilityZone"] = az
    db_instance["DBInstanceStatus"] = "available"
    db_instance["DbiResourceId"] = dbi_resource_id
    db_instance["Endpoint"]["HostedZoneId"] = f"Z{''.join(rng.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(13))}"
    db_instance["InstanceCreateTime"] = create_time.isoformat()
    db_instance["PreferredBackupWindow"] = f"{rand_int(rng, 0, 23):02d}:{rand_int(rng, 0, 59):02d}-{rand_int(rng, 0, 23):02d}:{rand_int(rng, 0, 59):02d}"
    db_instance["PreferredMaintenanceWindow"] = f"sun:{rand_int(rng, 0, 23):02d}:{rand_int(rng, 0, 59):02d}-sun:{rand_int(rng, 0, 23):02d}:{rand_int(rng, 0, 59):02d}"

    engine_profile = config["engine_profiles"][engine_name]
    _apply_engine_profile(db_instance, engine_name, engine_profile, rng)

    avg_cpu = rand_range(rng, usage_profile["cpu_range"][0], usage_profile["cpu_range"][1])
    avg_memory = rand_range(rng, usage_profile["memory_range"][0], usage_profile["memory_range"][1])
    avg_connections = float(rand_int(rng, usage_profile["connections_range"][0], usage_profile["connections_range"][1]))
    avg_read_iops = float(rand_int(rng, usage_profile["read_iops_range"][0], usage_profile["read_iops_range"][1]))
    avg_write_iops = float(rand_int(rng, usage_profile["write_iops_range"][0], usage_profile["write_iops_range"][1]))
    monthly_cost = round(estimate_rds_monthly_cost(instance_class), 2)
    criticality = choice(rng, config["tag_dimensions"]["criticality"])
    compliance = choice(rng, config["tag_dimensions"]["compliance"])
    backup_tier = choice(rng, config["tag_dimensions"]["backup_tiers"])
    data_classification = choice(rng, config["tag_dimensions"]["data_classification"])

    tags = {
        "Name": db_identifier,
        "env": env,
        "team": team,
        "owner": owner,
        "cost_center": cost_center,
        "business_unit": business_unit,
        "criticality": criticality,
        "compliance": compliance,
        "backup_tier": backup_tier,
        "data_classification": data_classification,
        "managed_by": "terraform",
        "engine": engine_name,
    }
    tag_list = [{"Key": key, "Value": value} for key, value in tags.items()]
    db_instance["TagList"] = tag_list
    tags_payload["TagList"] = tag_list

    metric_history = _metric_history(
        rng,
        generated_at,
        usage_profile,
        {
            "cpuutilization": avg_cpu,
            "memoryutilization": avg_memory,
            "databaseconnections": avg_connections,
            "readiops": avg_read_iops,
            "writeiops": avg_write_iops,
        },
    )
    cloudwatch_payloads: Dict[str, Any] = {}
    for metric_key, seed_payload in seed_bundle["metric_payloads"].items():
        payload = deepcopy(seed_payload)
        payload["Datapoints"] = _datapoints(metric_key, {
            "cpuutilization": avg_cpu,
            "databaseconnections": avg_connections,
            "readiops": avg_read_iops,
            "writeiops": avg_write_iops,
        }[metric_key], generated_at, usage_profile_name)
        payload["Label"] = METRIC_ALIASES[metric_key]
        cloudwatch_payloads[metric_key] = payload

    metadata = {
        "engine": engine_name,
        "engine_version": db_instance["EngineVersion"],
        "instance_class": instance_class,
        "instance_family": instance_family,
        "usage_profile": usage_profile_name,
        "monthly_cost_estimate": monthly_cost,
        "owner": owner,
        "team": team,
        "cost_center": cost_center,
        "business_unit": business_unit,
        "criticality": criticality,
        "compliance": compliance,
        "backup_tier": backup_tier,
        "data_classification": data_classification,
        "vpc_id": vpc_id,
        "subnet_group_name": subnet_group_name,
        "subnet_ids": subnet_ids,
        "security_group_ids": [security_group_id],
        "endpoint_address": endpoint,
        "port": db_instance["Endpoint"]["Port"],
        "allocated_storage_gb": db_instance["AllocatedStorage"],
        "storage_type": db_instance["StorageType"],
        "avg_cpu_utilization": avg_cpu,
        "avg_memory_utilization": avg_memory,
        "avg_connections": avg_connections,
        "avg_read_iops": avg_read_iops,
        "avg_write_iops": avg_write_iops,
        "metric_history": metric_history,
        "created_at": create_time.isoformat(),
        "seed_profile": seed_name,
        "resource_arn": resource_arn,
    }
    return {
        "inventory_id": inventory_id,
        "resource_id": db_identifier,
        "resource_type": "rds",
        "resource_name": db_identifier,
        "account_id": account_id,
        "region": selected_region,
        "availability_zone": az,
        "engine": engine_name,
        "db_instance_class": instance_class,
        "state": "available",
        "created_at": create_time.isoformat(),
        "tags": tags,
        "metadata": metadata,
        "aws_instance": db_instance,
        "aws_tags_payload": tags_payload,
        "cloudwatch_payloads": cloudwatch_payloads,
    }


def build_inventory_payload(account_id: str, generated_at: datetime, resources: List[Dict[str, Any]]) -> Dict[str, Any]:
    db_instances = []
    tag_details = {}
    metric_details = {}
    for resource in resources:
        db_instance = deepcopy(resource["aws_instance"])
        db_instance["InventoryId"] = resource["inventory_id"]
        db_instances.append(db_instance)
        tag_details[resource["resource_id"]] = deepcopy(resource["aws_tags_payload"])
        metric_details[resource["resource_id"]] = {
            metric_key: _json_safe(payload)
            for metric_key, payload in resource["cloudwatch_payloads"].items()
        }
    return {
        "DBInstances": db_instances,
        "TagDetails": tag_details,
        "MetricDetails": metric_details,
        "ResponseMetadata": {
            "HTTPHeaders": {
                "content-type": "text/xml",
                "date": generated_at.strftime("%a, %d %b %Y %H:%M:%S GMT"),
            },
            "HTTPStatusCode": 200,
            "RequestId": f"rds-syn-{generated_at.strftime('%Y%m%d%H%M%S')}",
            "RetryAttempts": 0,
        },
    }


def build_inventory_document(
    resources: List[Dict[str, Any]],
    aws_payload: Dict[str, Any],
    generated_at: datetime,
    account_id: str,
) -> Dict[str, Any]:
    instances = []
    for resource in resources:
        instances.append(
            {
                key: deepcopy(value)
                for key, value in resource.items()
                if key not in {"aws_instance", "aws_tags_payload", "cloudwatch_payloads"}
            }
        )
    return {
        "generated_at": generated_at.isoformat(),
        "account_id": account_id,
        "inventory_count": len(resources),
        "aws_payload": aws_payload,
        "instances": instances,
    }


def generate_dataset(
    config: Dict[str, Any],
    count_override: Optional[int] = None,
    seed_override: Optional[int] = None,
) -> GeneratedArtifacts:
    validate_config(config)
    seed = seed_override if seed_override is not None else config["generator"]["seed"]
    instance_count = count_override if count_override is not None else config["generator"]["instance_count"]
    rng = random.Random(seed)
    generated_at = deterministic_generated_at(seed)
    seed_bundles = load_seed_bundles(config)

    usage_sequence = _profile_sequence(config, instance_count, rng)
    engine_sequence = _engine_sequence(config, instance_count, rng)
    resources: List[Dict[str, Any]] = []
    maxops_rows: List[Dict[str, Any]] = []

    for inventory_id, (usage_profile_name, engine_name) in enumerate(zip(usage_sequence, engine_sequence), start=1):
        resource = build_resource(
            inventory_id,
            usage_profile_name,
            engine_name,
            rng,
            config,
            generated_at,
            seed_bundles,
        )
        resources.append(resource)
        maxops_rows.append(derive_maxops_row(resource, config, rng))

    aws_payload = build_inventory_payload(config["generator"]["account_id"], generated_at, resources)
    inventory = build_inventory_document(resources, aws_payload, generated_at, config["generator"]["account_id"])
    return GeneratedArtifacts(inventory=inventory, maxops=maxops_rows)


def write_artifacts(artifacts: GeneratedArtifacts, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for existing in output_dir.glob("*.json"):
        existing.unlink()
    dump_json(output_dir / "inventory.json", artifacts.inventory)
    dump_json(output_dir / "rds_maxops.json", artifacts.maxops)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate deterministic synthetic RDS DB import data.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to RDS synthetic config JSON.")
    parser.add_argument("--count", type=int, default=None, help="Override RDS instance count.")
    parser.add_argument("--seed", type=int, default=None, help="Override random seed.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Override output directory.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_json(args.config)
    artifacts = generate_dataset(config, count_override=args.count, seed_override=args.seed)
    output_dir = resolve_output_dir(config, args.output_dir)
    write_artifacts(artifacts, output_dir)
    print(
        json.dumps(
            {
                "status": "generated",
                "inventory_total": artifacts.inventory["inventory_count"],
                "maxops_total": len(artifacts.maxops),
                "output_dir": str(output_dir),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
