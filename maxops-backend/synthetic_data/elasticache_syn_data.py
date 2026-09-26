"""Generate deterministic, DB-import-friendly ElastiCache synthetic data from JSON config."""

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


CONFIG_PATH = CURRENT_DIR / "elasticache_syn_config.json"
PAYLOAD_ROOT = REPO_ROOT / "tests" / "payloads" / "elasticache"
METRIC_FILES = {
    "curritems": PAYLOAD_ROOT
    / "elasticache_low_item_count"
    / "flag_low_item_cluster"
    / "get_metric_statistics__low_items_graviton_cluster__curritems.json",
    "keycount": PAYLOAD_ROOT
    / "elasticache_low_item_count"
    / "flag_low_item_cluster"
    / "get_metric_statistics__low_items_graviton_cluster__keycount.json",
}
HISTORY_SERIES_COUNT = 15
HISTORY_SERIES_SPACING_DAYS = 30
ELIGIBLE_MIN_REDIS_VERSION = (6, 0)


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
        "node_type_profiles",
        "usage_profiles",
        "seed_profiles",
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

    for name in [
        "environments",
        "teams",
        "owners",
        "cost_centers",
        "business_units",
        "resource_types",
        "engines",
        "node_type_families",
        "usage_profiles",
    ]:
        validate_weights(f"distributions.{name}", config["distributions"][name])

    for name, profile in config["node_type_profiles"].items():
        validate_weights(f"node_type_profiles.{name}.classes", profile["classes"])
        finding_profile = profile.get("finding_profile")
        if finding_profile and finding_profile not in config["finding_profiles"]:
            raise ValueError(f"node_type_profiles.{name}.finding_profile references unknown finding profile")

    for name, profile in config["usage_profiles"].items():
        for key in ["curritems_range", "keycount_range", "metric_history_pattern"]:
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
    base = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
    return base + timedelta(seconds=seed % 86400)


def _history_timestamps(anchor: datetime) -> List[str]:
    base = anchor.astimezone(timezone.utc).replace(hour=14, minute=0, second=0, microsecond=0)
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
    return cleaned.rstrip("-")


def _trim_identifier(text: str, max_length: int) -> str:
    return text[:max_length].rstrip("-")


def parse_version(value: str) -> Optional[tuple[int, ...]]:
    if not value:
        return None
    parts = []
    for token in value.split("."):
        token = token.strip()
        if not token:
            parts.append(0)
            continue
        if not token.isdigit():
            return None
        parts.append(int(token))
    return tuple(parts)


def version_gte(value: str, minimum: tuple[int, ...]) -> bool:
    parsed = parse_version(value)
    if parsed is None:
        return False
    n = max(len(parsed), len(minimum))
    return parsed + (0,) * (n - len(parsed)) >= minimum + (0,) * (n - len(minimum))


def is_graviton(node_type: str) -> bool:
    parts = node_type.strip().lower().split(".")
    if len(parts) < 3:
        return False
    return "g" in parts[1]


def get_graviton_equivalent(node_type: str) -> Optional[str]:
    mapping = {
        "t3": "t4g",
        "m5": "m6g",
        "r5": "r6g",
    }
    parts = node_type.strip().lower().split(".")
    if len(parts) < 3:
        return None
    family = mapping.get(parts[1])
    if not family:
        return None
    return f"cache.{family}.{'.'.join(parts[2:])}"


def estimate_monthly_cost(node_type: str, resource_type: str, nodes: int) -> float:
    family_rates = {
        "t4g": 12.0,
        "t3": 14.0,
        "m5": 72.0,
        "m6g": 59.0,
        "r5": 96.0,
        "r6g": 78.0,
    }
    size_multipliers = {
        "micro": 1.0,
        "small": 1.55,
        "medium": 2.4,
        "large": 6.0,
        "xlarge": 11.5,
    }
    parts = node_type.lower().split(".")
    family = parts[1] if len(parts) > 1 else "t4g"
    size = parts[2] if len(parts) > 2 else "micro"
    per_node = family_rates.get(family, 22.0) * size_multipliers.get(size, 1.0)
    monthly = per_node * max(nodes, 1)
    if resource_type == "elasticache_replication_group":
        monthly *= 1.08
    return round(monthly, 2)


def _metric_history(
    rng: random.Random,
    generated_at: datetime,
    usage_profile: Dict[str, Any],
    averages: Dict[str, float],
) -> Dict[str, Dict[str, List[Any]]]:
    timestamps = _history_timestamps(generated_at)
    history: Dict[str, Dict[str, List[Any]]] = {}
    pattern = usage_profile["metric_history_pattern"]
    for metric_key, average_value in averages.items():
        series: Dict[str, List[Any]] = {"timestamps": list(timestamps)}
        if pattern == "empty":
            series["average"] = []
            history[metric_key] = series
            continue

        volatility = 0.08 if pattern == "steady" else 0.22
        if pattern == "idle":
            volatility = 0.12
        values: List[int] = []
        for index in range(HISTORY_SERIES_COUNT):
            trend = 1 + rng.uniform(-volatility, volatility)
            if pattern == "bursty" and index % 4 == 0:
                trend *= rng.uniform(1.15, 1.4)
            if pattern == "idle":
                trend *= rng.uniform(0.82, 1.08)
            value = max(0.0, average_value * trend)
            values.append(int(round(value)))
        series["average"] = values
        history[metric_key] = series
    return history


def _datapoints(metric_name: str, average_value: float, generated_at: datetime, usage_profile_name: str) -> List[Dict[str, Any]]:
    if usage_profile_name == "missing_metrics":
        return []
    return [
        {
            "Average": float(int(round(average_value))),
            "Timestamp": generated_at.isoformat(),
            "Unit": "Count",
        }
    ]


def _load_seed_payloads(profile: Dict[str, Any]) -> Dict[str, Any]:
    scenario_dir = PAYLOAD_ROOT / profile["seed_check_id"] / profile["seed_scenario"]
    payloads: Dict[str, Any] = {}
    for path in scenario_dir.glob("*.json"):
        payloads[path.name] = load_json(path)
    return payloads


def _seed_bundle(config: Dict[str, Any], name: str) -> Dict[str, Any]:
    profile = config["seed_profiles"][name]
    payloads = _load_seed_payloads(profile)
    bundle = {
        "profile": profile,
        "metric_payloads": {metric_name: deepcopy(load_json(path)) for metric_name, path in METRIC_FILES.items()},
    }
    if profile["resource_type"] == "elasticache_cluster":
        bundle["resource"] = deepcopy(payloads["describe_cache_clusters.json"]["CacheClusters"][0])
    else:
        bundle["resource"] = deepcopy(payloads["describe_replication_groups.json"]["ReplicationGroups"][0])
    return bundle


def load_seed_bundles(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {name: _seed_bundle(config, name) for name in config["seed_profiles"]}


def _scenario_sequence(config: Dict[str, Any], count: int, rng: random.Random) -> List[Dict[str, Any]]:
    sequence: List[Dict[str, Any]] = []
    if count >= 8:
        sequence.extend(
            [
                {
                    "usage_profile": "low_item_candidate",
                    "resource_type": "elasticache_cluster",
                    "seed_name": "low_items_cluster_capture",
                    "node_family": "graviton_burstable",
                    "engine_profile": "redis_eligible",
                },
                {
                    "usage_profile": "high_item_healthy",
                    "resource_type": "elasticache_cluster",
                    "seed_name": "graviton_cluster_capture",
                    "node_family": "graviton_general",
                    "engine_profile": "valkey",
                },
                {
                    "usage_profile": "non_graviton_candidate",
                    "resource_type": "elasticache_cluster",
                    "seed_name": "non_graviton_cluster_capture",
                    "node_family": "non_graviton_burstable",
                    "engine_profile": "redis_eligible",
                },
                {
                    "usage_profile": "steady_healthy",
                    "resource_type": "elasticache_replication_group",
                    "seed_name": "ineligible_replication_group_capture",
                    "node_family": "graviton_burstable",
                    "engine_profile": "redis_ineligible",
                },
                {
                    "usage_profile": "valkey_candidate",
                    "resource_type": "elasticache_replication_group",
                    "seed_name": "eligible_replication_group_capture",
                    "node_family": "graviton_burstable",
                    "engine_profile": "redis_eligible",
                },
                {
                    "usage_profile": "missing_metrics",
                    "resource_type": "elasticache_cluster",
                    "seed_name": "graviton_cluster_capture",
                    "node_family": "graviton_burstable",
                    "engine_profile": "redis_eligible",
                },
                {
                    "usage_profile": "steady_healthy",
                    "resource_type": "elasticache_cluster",
                    "seed_name": "graviton_cluster_capture",
                    "node_family": "graviton_general",
                    "engine_profile": "valkey",
                },
                {
                    "usage_profile": "non_graviton_candidate",
                    "resource_type": "elasticache_replication_group",
                    "seed_name": "ineligible_replication_group_capture",
                    "node_family": "non_graviton_general",
                    "engine_profile": "redis_ineligible",
                },
            ]
        )

    while len(sequence) < count:
        usage_profile = weighted_choice(rng, config["distributions"]["usage_profiles"])
        if usage_profile in {"low_item_candidate", "missing_metrics"}:
            resource_type = "elasticache_cluster"
        elif usage_profile == "valkey_candidate":
            resource_type = "elasticache_replication_group"
        else:
            resource_type = weighted_choice(rng, config["distributions"]["resource_types"])

        if usage_profile == "non_graviton_candidate":
            node_family = weighted_choice(
                rng,
                config["distributions"]["node_type_families"],
                allowed={"non_graviton_burstable", "non_graviton_general", "non_graviton_memory"},
            )
        elif usage_profile in {"low_item_candidate", "missing_metrics"}:
            node_family = weighted_choice(
                rng,
                config["distributions"]["node_type_families"],
                allowed={"graviton_burstable", "graviton_general"},
            )
        else:
            node_family = weighted_choice(rng, config["distributions"]["node_type_families"])

        if usage_profile == "valkey_candidate":
            engine_profile = "redis_eligible"
        elif resource_type == "elasticache_replication_group" and rng.random() < 0.35:
            engine_profile = "redis_ineligible"
        else:
            engine_profile = weighted_choice(rng, {"redis_eligible": 0.47, "redis_ineligible": 0.18, "valkey": 0.35})

        if resource_type == "elasticache_cluster":
            seed_name = "non_graviton_cluster_capture" if "non_graviton" in node_family else "graviton_cluster_capture"
        else:
            seed_name = (
                "eligible_replication_group_capture"
                if engine_profile == "redis_eligible"
                else "ineligible_replication_group_capture"
            )

        sequence.append(
            {
                "usage_profile": usage_profile,
                "resource_type": resource_type,
                "seed_name": seed_name,
                "node_family": node_family,
                "engine_profile": engine_profile,
            }
        )

    rng.shuffle(sequence)
    return sequence[:count]


def _cluster_identifier(inventory_id: int, rng: random.Random, config: Dict[str, Any], env: str, role: str) -> str:
    app = choice(rng, config["naming"]["apps"])
    base = _slug([config["naming"]["org_prefix"], env, app, role, "cache", f"{inventory_id:03d}"])
    return _trim_identifier(base, 50)


def _replication_group_identifier(
    inventory_id: int, rng: random.Random, config: Dict[str, Any], env: str, role: str
) -> str:
    app = choice(rng, config["naming"]["apps"])
    base = _slug([config["naming"]["org_prefix"], env, app, role, "rg", f"{inventory_id:03d}"])
    return _trim_identifier(base, 40)


def _cluster_endpoint(resource_id: str, region: str, inventory_id: int) -> str:
    return f"{resource_id}.{inventory_id:06d}.{region}.cache.amazonaws.com"


def _replication_group_endpoint(resource_id: str, region: str, inventory_id: int, kind: str) -> str:
    suffix = "ro" if kind == "reader" else "primary"
    return f"{resource_id}-{suffix}.{inventory_id:06d}.{region}.cache.amazonaws.com"


def _member_cluster_id(resource_id: str, index: int) -> str:
    return _trim_identifier(f"{resource_id}-{index:03d}", 50)


def _member_cluster_endpoint(member_cluster_id: str, region: str, inventory_id: int) -> str:
    return f"{member_cluster_id}.{inventory_id:06d}.{region}.cache.amazonaws.com"


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
        target = get_graviton_equivalent(resource["metadata"]["cache_node_type"])
        if target:
            option["parameters"]["target_cache_node_type"] = target
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
            "engine": metadata["engine"],
            "engine_version": metadata["engine_version"],
            "cache_node_type": metadata["cache_node_type"],
            "avg_curritems": metadata["avg_curritems"],
            "avg_keycount": metadata["avg_keycount"],
        },
        "current_config": {
            "cache_node_type": metadata["cache_node_type"],
            "engine": metadata["engine"],
            "engine_version": metadata["engine_version"],
            "region": resource["region"],
            "resource_type": resource["resource_type"],
        },
        "target_config": {},
        "metadata": {
            "status": "healthy",
            "resource_name": resource["resource_name"],
            "resource_type": resource["resource_type"],
            "engine": metadata["engine"],
            "engine_version": metadata["engine_version"],
            "cache_node_type": metadata["cache_node_type"],
            "monthly_cost_estimate": metadata["monthly_cost_estimate"],
        },
    }


def derive_maxops_row(resource: Dict[str, Any], config: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
    metadata = resource["metadata"]
    usage_profile_name = metadata["usage_profile"]
    node_family = metadata["node_family"]
    usage_profile = config["usage_profiles"][usage_profile_name]
    node_profile = config["node_type_profiles"][node_family]
    finding_profile_name = usage_profile.get("finding_profile") or node_profile.get("finding_profile")
    if not finding_profile_name:
        return build_neutral_maxops_row(resource)

    if finding_profile_name == "elasticache_low_item_count":
        history = metadata["metric_history"]["curritems"]["average"]
        if not history or max(history) > 1000 or metadata["avg_curritems"] > 1000:
            return build_neutral_maxops_row(resource)
    if finding_profile_name == "elasticache_non_graviton_instance_class":
        if is_graviton(metadata["cache_node_type"]):
            return build_neutral_maxops_row(resource)
    if finding_profile_name == "elasticache_redis_convertible_to_valkey":
        if metadata["engine"] != "redis" or not version_gte(metadata["engine_version"], ELIGIBLE_MIN_REDIS_VERSION):
            return build_neutral_maxops_row(resource)

    finding_rule = deepcopy(config["finding_profiles"][finding_profile_name])
    monthly = metadata["monthly_cost_estimate"]
    savings_monthly = round(monthly * finding_rule["savings_factor"], 2)
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
        target = get_graviton_equivalent(metadata["cache_node_type"])
        if target:
            target_config["cache_node_type"] = target
    if finding_rule["recommended_action"] == "upgrade_engine":
        target_config["engine"] = "valkey"

    context = {
        "avg_curritems": metadata["avg_curritems"],
        "avg_keycount": metadata["avg_keycount"],
        "cache_node_type": metadata["cache_node_type"],
        "engine": metadata["engine"],
        "engine_version": metadata["engine_version"],
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
            "avg_curritems": metadata["avg_curritems"],
            "avg_keycount": metadata["avg_keycount"],
            "engine": metadata["engine"],
            "engine_version": metadata["engine_version"],
            "cache_node_type": metadata["cache_node_type"],
        },
        "current_config": {
            "cache_node_type": metadata["cache_node_type"],
            "engine": metadata["engine"],
            "engine_version": metadata["engine_version"],
            "resource_type": resource["resource_type"],
            "region": resource["region"],
        },
        "target_config": target_config,
        "metadata": {
            "status": "actionable",
            "resource_name": resource["resource_name"],
            "resource_id": resource["resource_id"],
            "resource_type": resource["resource_type"],
            "engine": metadata["engine"],
            "engine_version": metadata["engine_version"],
            "cache_node_type": metadata["cache_node_type"],
            "monthly_cost_estimate": metadata["monthly_cost_estimate"],
            "owner": metadata["owner"],
            "team": metadata["team"],
        },
    }


def _build_cluster_resource(
    inventory_id: int,
    spec: Dict[str, Any],
    rng: random.Random,
    config: Dict[str, Any],
    generated_at: datetime,
    seed_bundle: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    account_id = config["generator"]["account_id"]
    region = context["region"]
    az = context["availability_zone"]
    usage_profile_name = spec["usage_profile"]
    usage_profile = config["usage_profiles"][usage_profile_name]
    engine_profile = config["engine_profiles"][spec["engine_profile"]]
    node_type = weighted_choice(rng, config["node_type_profiles"][spec["node_family"]]["classes"])
    cluster_id = _cluster_identifier(inventory_id, rng, config, context["env"], context["role"])
    endpoint = _cluster_endpoint(cluster_id, region, inventory_id)
    arn = f"arn:aws:elasticache:{region}:{account_id}:cluster:{cluster_id}"
    created_at = generated_at - timedelta(days=rand_int(rng, 5, 540), hours=rand_int(rng, 0, 23))

    seed_cluster = seed_bundle["resource"]
    replacements = {
        seed_cluster["CacheClusterId"]: cluster_id,
        seed_cluster["ARN"]: arn,
        seed_cluster["CacheNodes"][0]["Endpoint"]["Address"]: endpoint,
        "123456789012": account_id,
        "us-east-1": region,
    }
    cluster = _replace_strings(deepcopy(seed_cluster), replacements)
    cluster["CacheNodeType"] = node_type
    cluster["Engine"] = engine_profile["engine"]
    cluster["EngineVersion"] = engine_profile["engine_version"]
    cluster["CacheClusterCreateTime"] = created_at.isoformat()
    cluster["PreferredAvailabilityZone"] = az
    cluster["NumCacheNodes"] = 1
    cluster["CacheNodes"][0]["CacheNodeCreateTime"] = created_at.isoformat()
    cluster["CacheNodes"][0]["CustomerAvailabilityZone"] = az
    cluster["CacheNodes"][0]["Endpoint"]["Port"] = engine_profile["port"]
    cluster["CacheParameterGroup"]["CacheParameterGroupName"] = engine_profile["parameter_group_name"]
    cluster["SnapshotRetentionLimit"] = engine_profile["snapshot_retention_limit"]
    cluster["PreferredMaintenanceWindow"] = f"sun:{rand_int(rng, 0, 23):02d}:00-sun:{rand_int(rng, 0, 23):02d}:59"

    avg_curritems = float(rand_int(rng, usage_profile["curritems_range"][0], usage_profile["curritems_range"][1]))
    avg_keycount = float(rand_int(rng, usage_profile["keycount_range"][0], usage_profile["keycount_range"][1]))
    metric_history = _metric_history(
        rng,
        generated_at,
        usage_profile,
        {"curritems": avg_curritems, "keycount": avg_keycount},
    )
    cloudwatch_payloads: Dict[str, Any] = {}
    for metric_key, seed_payload in seed_bundle["metric_payloads"].items():
        payload = deepcopy(seed_payload)
        payload["Datapoints"] = _datapoints(metric_key, {"curritems": avg_curritems, "keycount": avg_keycount}[metric_key], generated_at, usage_profile_name)
        payload["Label"] = "CurrItems" if metric_key == "curritems" else "KeyCount"
        cloudwatch_payloads[metric_key] = payload

    tags = {
        "Name": cluster_id,
        "env": context["env"],
        "team": context["team"],
        "owner": context["owner"],
        "cost_center": context["cost_center"],
        "business_unit": context["business_unit"],
        "criticality": context["criticality"],
        "compliance": context["compliance"],
        "cache_pattern": context["cache_pattern"],
        "data_classification": context["data_classification"],
        "managed_by": "terraform",
        "engine": engine_profile["engine"],
    }
    metadata = {
        "engine": engine_profile["engine"],
        "engine_version": engine_profile["engine_version"],
        "cache_node_type": node_type,
        "resource_kind": "cluster",
        "node_family": spec["node_family"],
        "usage_profile": usage_profile_name,
        "monthly_cost_estimate": estimate_monthly_cost(node_type, "elasticache_cluster", 1),
        "owner": context["owner"],
        "team": context["team"],
        "cost_center": context["cost_center"],
        "business_unit": context["business_unit"],
        "criticality": context["criticality"],
        "compliance": context["compliance"],
        "cache_pattern": context["cache_pattern"],
        "data_classification": context["data_classification"],
        "num_cache_nodes": 1,
        "member_clusters": [],
        "num_node_groups": None,
        "replicas_per_node_group": None,
        "avg_curritems": avg_curritems,
        "avg_keycount": avg_keycount,
        "metric_history": metric_history,
        "created_at": created_at.isoformat(),
        "seed_profile": spec["seed_name"],
        "endpoint_address": endpoint,
    }
    return {
        "inventory_id": inventory_id,
        "resource_id": cluster_id,
        "resource_name": cluster_id,
        "resource_type": "elasticache_cluster",
        "account_id": account_id,
        "region": region,
        "availability_zone": az,
        "state": "available",
        "tags": tags,
        "metadata": metadata,
        "aws_resource": cluster,
        "cloudwatch_payloads": cloudwatch_payloads,
    }


def _build_replication_group_resource(
    inventory_id: int,
    spec: Dict[str, Any],
    rng: random.Random,
    config: Dict[str, Any],
    generated_at: datetime,
    seed_bundle: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    account_id = config["generator"]["account_id"]
    region = context["region"]
    usage_profile_name = spec["usage_profile"]
    usage_profile = config["usage_profiles"][usage_profile_name]
    engine_profile = config["engine_profiles"][spec["engine_profile"]]
    node_type = weighted_choice(rng, config["node_type_profiles"][spec["node_family"]]["classes"])
    replication_group_id = _replication_group_identifier(inventory_id, rng, config, context["env"], context["role"])
    member_clusters = [_member_cluster_id(replication_group_id, index) for index in (1, 2)]
    member_azs = [choice(rng, next(region_cfg for region_cfg in config["regions"] if region_cfg["name"] == region)["azs"]) for _ in member_clusters]
    primary_endpoint = _replication_group_endpoint(replication_group_id, region, inventory_id, "primary")
    reader_endpoint = _replication_group_endpoint(replication_group_id, region, inventory_id, "reader")
    member_endpoints = [_member_cluster_endpoint(cluster_id, region, inventory_id) for cluster_id in member_clusters]
    arn = f"arn:aws:elasticache:{region}:{account_id}:replicationgroup:{replication_group_id}"
    created_at = generated_at - timedelta(days=rand_int(rng, 7, 620), hours=rand_int(rng, 0, 23))

    seed_rg = seed_bundle["resource"]
    replacements = {
        seed_rg["ReplicationGroupId"]: replication_group_id,
        seed_rg["ARN"]: arn,
        seed_rg["MemberClusters"][0]: member_clusters[0],
        seed_rg["MemberClusters"][1]: member_clusters[1],
        seed_rg["NodeGroups"][0]["NodeGroupMembers"][0]["CacheClusterId"]: member_clusters[0],
        seed_rg["NodeGroups"][0]["NodeGroupMembers"][1]["CacheClusterId"]: member_clusters[1],
        seed_rg["NodeGroups"][0]["NodeGroupMembers"][0]["ReadEndpoint"]["Address"]: member_endpoints[0],
        seed_rg["NodeGroups"][0]["NodeGroupMembers"][1]["ReadEndpoint"]["Address"]: member_endpoints[1],
        seed_rg["NodeGroups"][0]["PrimaryEndpoint"]["Address"]: primary_endpoint,
        seed_rg["NodeGroups"][0]["ReaderEndpoint"]["Address"]: reader_endpoint,
        "123456789012": account_id,
        "us-east-1": region,
    }
    rg = _replace_strings(deepcopy(seed_rg), replacements)
    rg["CacheNodeType"] = node_type
    rg["Engine"] = engine_profile["engine"]
    rg["EngineVersion"] = engine_profile["engine_version"]
    rg["ReplicationGroupCreateTime"] = created_at.isoformat()
    rg["SnapshotRetentionLimit"] = engine_profile["snapshot_retention_limit"]
    rg["MemberClusters"] = list(member_clusters)
    for index, member in enumerate(rg["NodeGroups"][0]["NodeGroupMembers"]):
        member["PreferredAvailabilityZone"] = member_azs[index]
        member["ReadEndpoint"]["Port"] = engine_profile["port"]
    rg["NodeGroups"][0]["PrimaryEndpoint"]["Port"] = engine_profile["port"]
    rg["NodeGroups"][0]["ReaderEndpoint"]["Port"] = engine_profile["port"]

    avg_curritems = float(rand_int(rng, usage_profile["curritems_range"][0], usage_profile["curritems_range"][1]))
    avg_keycount = float(rand_int(rng, usage_profile["keycount_range"][0], usage_profile["keycount_range"][1]))
    metric_history = _metric_history(
        rng,
        generated_at,
        usage_profile,
        {"curritems": avg_curritems, "keycount": avg_keycount},
    )
    cloudwatch_payloads: Dict[str, Any] = {}
    for metric_key, seed_payload in seed_bundle["metric_payloads"].items():
        payload = deepcopy(seed_payload)
        payload["Datapoints"] = _datapoints(metric_key, {"curritems": avg_curritems, "keycount": avg_keycount}[metric_key], generated_at, usage_profile_name)
        payload["Label"] = "CurrItems" if metric_key == "curritems" else "KeyCount"
        cloudwatch_payloads[metric_key] = payload

    tags = {
        "Name": replication_group_id,
        "env": context["env"],
        "team": context["team"],
        "owner": context["owner"],
        "cost_center": context["cost_center"],
        "business_unit": context["business_unit"],
        "criticality": context["criticality"],
        "compliance": context["compliance"],
        "cache_pattern": context["cache_pattern"],
        "data_classification": context["data_classification"],
        "managed_by": "terraform",
        "engine": engine_profile["engine"],
    }
    metadata = {
        "engine": engine_profile["engine"],
        "engine_version": engine_profile["engine_version"],
        "cache_node_type": node_type,
        "resource_kind": "replication_group",
        "node_family": spec["node_family"],
        "usage_profile": usage_profile_name,
        "monthly_cost_estimate": estimate_monthly_cost(node_type, "elasticache_replication_group", len(member_clusters)),
        "owner": context["owner"],
        "team": context["team"],
        "cost_center": context["cost_center"],
        "business_unit": context["business_unit"],
        "criticality": context["criticality"],
        "compliance": context["compliance"],
        "cache_pattern": context["cache_pattern"],
        "data_classification": context["data_classification"],
        "num_cache_nodes": len(member_clusters),
        "member_clusters": list(member_clusters),
        "num_node_groups": len(rg.get("NodeGroups", [])),
        "replicas_per_node_group": max(0, len(member_clusters) - 1),
        "avg_curritems": avg_curritems,
        "avg_keycount": avg_keycount,
        "metric_history": metric_history,
        "created_at": created_at.isoformat(),
        "seed_profile": spec["seed_name"],
        "primary_endpoint_address": primary_endpoint,
        "reader_endpoint_address": reader_endpoint,
    }
    return {
        "inventory_id": inventory_id,
        "resource_id": replication_group_id,
        "resource_name": replication_group_id,
        "resource_type": "elasticache_replication_group",
        "account_id": account_id,
        "region": region,
        "availability_zone": None,
        "state": "available",
        "tags": tags,
        "metadata": metadata,
        "aws_resource": rg,
        "cloudwatch_payloads": cloudwatch_payloads,
    }


def build_resource(
    inventory_id: int,
    spec: Dict[str, Any],
    rng: random.Random,
    config: Dict[str, Any],
    generated_at: datetime,
    seed_bundles: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    region = weighted_choice(rng, {region_cfg["name"]: region_cfg["weight"] for region_cfg in config["regions"]})
    region_cfg = next(item for item in config["regions"] if item["name"] == region)
    context = {
        "env": weighted_choice(rng, config["distributions"]["environments"]),
        "team": weighted_choice(rng, config["distributions"]["teams"]),
        "owner": weighted_choice(rng, config["distributions"]["owners"]),
        "cost_center": weighted_choice(rng, config["distributions"]["cost_centers"]),
        "business_unit": weighted_choice(rng, config["distributions"]["business_units"]),
        "criticality": choice(rng, config["tag_dimensions"]["criticality"]),
        "compliance": choice(rng, config["tag_dimensions"]["compliance"]),
        "cache_pattern": choice(rng, config["tag_dimensions"]["cache_patterns"]),
        "data_classification": choice(rng, config["tag_dimensions"]["data_classification"]),
        "role": choice(rng, config["naming"]["roles"]),
        "region": region,
        "availability_zone": choice(rng, region_cfg["azs"]),
    }
    seed_bundle = seed_bundles[spec["seed_name"]]
    if spec["resource_type"] == "elasticache_cluster":
        return _build_cluster_resource(inventory_id, spec, rng, config, generated_at, seed_bundle, context)
    return _build_replication_group_resource(inventory_id, spec, rng, config, generated_at, seed_bundle, context)


def build_inventory_payload(account_id: str, generated_at: datetime, resources: List[Dict[str, Any]]) -> Dict[str, Any]:
    cache_clusters = []
    replication_groups = []
    metric_details = {}
    for resource in resources:
        aws_resource = deepcopy(resource["aws_resource"])
        aws_resource["InventoryId"] = resource["inventory_id"]
        if resource["resource_type"] == "elasticache_cluster":
            cache_clusters.append(aws_resource)
        else:
            replication_groups.append(aws_resource)
        metric_details[resource["resource_id"]] = {
            metric_key: _json_safe(payload)
            for metric_key, payload in resource["cloudwatch_payloads"].items()
        }
    return {
        "CacheClusters": cache_clusters,
        "ReplicationGroups": replication_groups,
        "MetricDetails": metric_details,
        "ResponseMetadata": {
            "HTTPHeaders": {
                "content-type": "text/xml",
                "date": generated_at.strftime("%a, %d %b %Y %H:%M:%S GMT"),
            },
            "HTTPStatusCode": 200,
            "RequestId": f"elasticache-syn-{generated_at.strftime('%Y%m%d%H%M%S')}",
            "RetryAttempts": 0,
        },
    }


def build_inventory_document(
    resources: List[Dict[str, Any]],
    aws_payload: Dict[str, Any],
    generated_at: datetime,
    account_id: str,
) -> Dict[str, Any]:
    flat_resources = []
    for resource in resources:
        flat_resources.append(
            {
                key: deepcopy(value)
                for key, value in resource.items()
                if key not in {"aws_resource", "cloudwatch_payloads"}
            }
        )
    return {
        "generated_at": generated_at.isoformat(),
        "account_id": account_id,
        "inventory_count": len(resources),
        "aws_payload": aws_payload,
        "resources": flat_resources,
    }


def generate_dataset(
    config: Dict[str, Any],
    count_override: Optional[int] = None,
    seed_override: Optional[int] = None,
) -> GeneratedArtifacts:
    validate_config(config)
    seed = seed_override if seed_override is not None else config["generator"]["seed"]
    resource_count = count_override if count_override is not None else config["generator"]["resource_count"]
    rng = random.Random(seed)
    generated_at = deterministic_generated_at(seed)
    seed_bundles = load_seed_bundles(config)
    scenario_sequence = _scenario_sequence(config, resource_count, rng)

    resources: List[Dict[str, Any]] = []
    maxops_rows: List[Dict[str, Any]] = []
    for inventory_id, spec in enumerate(scenario_sequence, start=1):
        resource = build_resource(inventory_id, spec, rng, config, generated_at, seed_bundles)
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
    dump_json(output_dir / "elasticache_maxops.json", artifacts.maxops)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate deterministic synthetic ElastiCache DB import data.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to ElastiCache synthetic config JSON.")
    parser.add_argument("--count", type=int, default=None, help="Override ElastiCache resource count.")
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
