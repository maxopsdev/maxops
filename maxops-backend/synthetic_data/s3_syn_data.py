"""Generate deterministic, DB-import-friendly S3 synthetic data from JSON config."""

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


CONFIG_PATH = CURRENT_DIR / "s3_syn_config.json"
PAYLOAD_ROOT = REPO_ROOT / "tests" / "payloads" / "s3"
RESOURCE_CONFIG_PATH = REPO_ROOT / "tests_generator" / "s3" / "resource_config.json"
PRICE_PER_GB = {
    "STANDARD": 0.023,
    "GLACIER": 0.004,
    "DEEP_ARCHIVE": 0.00099,
}
SEED_DETAIL_OPERATIONS = (
    "get_bucket_location",
    "get_bucket_tagging",
    "get_bucket_lifecycle_configuration",
    "get_bucket_versioning",
    "get_bucket_logging",
    "get_bucket_replication",
    "list_bucket_inventory_configurations",
)


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
        "bucket_profiles",
        "finding_profiles",
        "action_catalog",
    ]
    for section in required:
        if section not in config:
            raise ValueError(f"Missing required config section: {section}")

    generator = config["generator"]
    for key in ["seed", "bucket_count", "account_id", "default_region", "output_dir"]:
        if key not in generator:
            raise ValueError(f"Missing generator.{key}")

    if generator["bucket_count"] <= 0:
        raise ValueError("generator.bucket_count must be > 0")

    region_names = {region["name"] for region in config["regions"]}
    if generator["default_region"] not in region_names:
        raise ValueError("generator.default_region must be present in regions")

    distributions = config["distributions"]
    for name in ["environments", "teams", "owners", "cost_centers", "business_units", "bucket_profiles", "finding_profiles"]:
        if name not in distributions:
            raise ValueError(f"Missing distributions.{name}")
        validate_weights(f"distributions.{name}", distributions[name])

    for profile_name, profile in config["bucket_profiles"].items():
        for key in [
            "seed_check_id",
            "seed_scenario",
            "seed_bucket_alias",
            "placeholder_bindings",
            "bucket_kind",
            "storage_size_gb_range",
            "object_count_range",
            "storage_class_mix",
        ]:
            if key not in profile:
                raise ValueError(f"bucket_profiles.{profile_name}.{key} is required")
        if len(profile["storage_size_gb_range"]) != 2:
            raise ValueError(f"bucket_profiles.{profile_name}.storage_size_gb_range must be a 2-item range")
        if len(profile["object_count_range"]) != 2:
            raise ValueError(f"bucket_profiles.{profile_name}.object_count_range must be a 2-item range")
        validate_weights(f"bucket_profiles.{profile_name}.storage_class_mix", profile["storage_class_mix"])
        finding_profile = profile.get("finding_profile")
        if finding_profile and finding_profile not in config["finding_profiles"]:
            raise ValueError(f"bucket_profiles.{profile_name}.finding_profile references unknown finding profile")

    for finding_name, finding in config["finding_profiles"].items():
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
                raise ValueError(f"finding_profiles.{finding_name}.{key} is required")
        actions = [finding["recommended_action"], *finding["alternative_actions"]]
        for action_key in actions:
            if action_key not in config["action_catalog"]:
                raise ValueError(f"finding_profiles.{finding_name} references unknown action {action_key}")


def weighted_choice(rng: random.Random, weights: Dict[str, float], allowed: Optional[Iterable[str]] = None) -> str:
    allowed_set = set(allowed) if allowed is not None else None
    filtered = {key: value for key, value in weights.items() if allowed_set is None or key in allowed_set}
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
    base = datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)
    return base + timedelta(seconds=seed % 86400)


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


def _fit_bucket_name(base: str, suffix: str) -> str:
    joined = f"{base}-{suffix}".strip("-")
    if len(joined) <= 63:
        return joined
    overflow = len(joined) - 63
    trimmed = base[:-overflow].rstrip("-")
    return f"{trimmed}-{suffix}"[:63].rstrip("-")


def _role_name(bucket_name: str, inventory_id: int) -> str:
    base = bucket_name.replace(".", "-")
    prefix = f"{base}-replication-role-{inventory_id:03d}"
    return prefix[:64]


def _logging_target_bucket(bucket_name: str) -> str:
    return _fit_bucket_name(bucket_name, "access-logs")


def _inventory_target_bucket(bucket_name: str) -> str:
    return _fit_bucket_name(bucket_name, "inventory")


def _replication_target_bucket(bucket_name: str) -> str:
    return _fit_bucket_name(bucket_name, "replica")


def _weighted_storage_mix(rng: random.Random, weights: Dict[str, float]) -> Dict[str, float]:
    keys = list(weights.keys())
    remaining = 1.0
    result: Dict[str, float] = {}
    for index, key in enumerate(keys):
        if index == len(keys) - 1:
            result[key] = round(max(0.0, remaining), 3)
            break
        base = weights[key]
        jitter = rng.uniform(0.9, 1.1)
        share = min(remaining, round(base * jitter, 3))
        result[key] = share
        remaining = round(max(0.0, remaining - share), 3)
    total = sum(result.values()) or 1.0
    return {key: round(value / total, 3) for key, value in result.items()}


def _estimate_monthly_cost(bucket_size_gb: float, storage_class_mix: Dict[str, float]) -> float:
    rate = 0.0
    for storage_class, share in storage_class_mix.items():
        rate += PRICE_PER_GB.get(storage_class, PRICE_PER_GB["STANDARD"]) * share
    return round(bucket_size_gb * rate, 2)


def _inventory_target_buckets(payload: Dict[str, Any]) -> List[str]:
    buckets: List[str] = []
    for cfg in payload.get("InventoryConfigurationList", []):
        bucket_arn = cfg.get("Destination", {}).get("S3BucketDestination", {}).get("Bucket")
        if isinstance(bucket_arn, str):
            buckets.append(bucket_arn.split(":::", 1)[1] if bucket_arn.startswith("arn:aws:s3:::") else bucket_arn)
    return sorted(set(buckets))


def _replication_target_buckets(payload: Dict[str, Any]) -> List[str]:
    cfg = payload.get("ReplicationConfiguration", payload)
    buckets: List[str] = []
    for rule in cfg.get("Rules", []):
        bucket_arn = rule.get("Destination", {}).get("Bucket")
        if isinstance(bucket_arn, str):
            buckets.append(bucket_arn.split(":::", 1)[1] if bucket_arn.startswith("arn:aws:s3:::") else bucket_arn)
    return sorted(set(buckets))


def _load_resource_placeholders() -> Dict[str, str]:
    return load_json(RESOURCE_CONFIG_PATH).get("placeholder_values", {})


def _load_seed_payloads(profile: Dict[str, Any]) -> Dict[str, Any]:
    scenario_dir = PAYLOAD_ROOT / profile["seed_check_id"] / profile["seed_scenario"]
    payloads = {}
    for path in scenario_dir.glob("*.json"):
        payloads[path.name] = load_json(path)
    return payloads


def _build_seed_replacements(
    bindings: Dict[str, str],
    current_bucket_name: str,
    generated: Dict[str, str],
    account_id: str,
    placeholder_values: Dict[str, str],
) -> Dict[str, str]:
    replacements: Dict[str, str] = {}
    for placeholder_key, token in bindings.items():
        source = placeholder_values.get(placeholder_key)
        if not source:
            continue
        if token == "self":
            replacements[source] = current_bucket_name
        elif token == "logging_target":
            replacements[source] = generated["logging_target_bucket"]
        elif token == "inventory_target":
            replacements[source] = generated["inventory_target_bucket"]
        elif token == "replication_target":
            replacements[source] = generated["replication_target_bucket"]
        elif token == "replication_role":
            replacements[source] = generated["replication_role_name"]
        elif token == "account_id":
            replacements[source] = account_id
        else:
            replacements[source] = token
    account_placeholder = placeholder_values.get("ACCOUNT_ID")
    if account_placeholder:
        replacements[account_placeholder] = account_id
    return replacements


def _seed_details_for_bucket(
    profile: Dict[str, Any],
    bucket_name: str,
    region: str,
    account_id: str,
    generated: Dict[str, str],
    placeholder_values: Dict[str, str],
) -> Dict[str, Any]:
    payloads = _load_seed_payloads(profile)
    alias = profile["seed_bucket_alias"]
    replacements = _build_seed_replacements(
        profile["placeholder_bindings"],
        bucket_name,
        generated,
        account_id,
        placeholder_values,
    )

    details: Dict[str, Any] = {}
    for operation in SEED_DETAIL_OPERATIONS:
        filename = f"{operation}__{alias}.json"
        details[operation] = _replace_strings(deepcopy(payloads.get(filename, {})), replacements)

    location = details["get_bucket_location"] or {}
    location["LocationConstraint"] = None if region == "us-east-1" else region
    details["get_bucket_location"] = location
    return details


def _profile_sequence(config: Dict[str, Any], count: int, rng: random.Random) -> List[str]:
    flagged = [name for name, profile in config["bucket_profiles"].items() if profile.get("finding_profile")]
    healthy = [name for name, profile in config["bucket_profiles"].items() if not profile.get("finding_profile")]
    sequence: List[str] = []

    if count >= len(flagged) * 2 + max(4, len(healthy)):
        for name in flagged:
            sequence.extend([name, name])
    elif count >= len(flagged) + max(2, len(healthy)):
        sequence.extend(flagged)

    remaining = count - len(sequence)
    if remaining > 0 and healthy:
        healthy_weights = {
            key: value
            for key, value in config["distributions"]["bucket_profiles"].items()
            if key in healthy
        }
        for _ in range(remaining):
            sequence.append(weighted_choice(rng, healthy_weights))

    while len(sequence) < count:
        sequence.append(weighted_choice(rng, config["distributions"]["bucket_profiles"]))

    rng.shuffle(sequence)
    return sequence[:count]


def _build_bucket_name(
    profile_name: str,
    inventory_id: int,
    rng: random.Random,
    config: Dict[str, Any],
    env: str,
    app: str,
) -> str:
    naming = config["naming"]
    if profile_name == "log_bucket_without_expiration":
        log_type = choice(rng, config["tag_dimensions"]["log_types"])
        return _slug([naming["org_prefix"], env, app, log_type, "logs", f"{inventory_id:03d}"])
    if profile_name == "logging_enabled":
        return _slug([naming["org_prefix"], env, app, "assets", f"{inventory_id:03d}"])
    if profile_name == "inventory_enabled":
        return _slug([naming["org_prefix"], env, app, "inventory-src", f"{inventory_id:03d}"])
    if profile_name == "replication_enabled":
        return _slug([naming["org_prefix"], env, app, "primary", f"{inventory_id:03d}"])
    suffix = choice(rng, naming["data_suffixes"])
    return _slug([naming["org_prefix"], env, app, suffix, f"{inventory_id:03d}"])


def _bucket_context(
    inventory_id: int,
    profile_name: str,
    rng: random.Random,
    config: Dict[str, Any],
    generated_at: datetime,
) -> Dict[str, Any]:
    env = weighted_choice(rng, config["distributions"]["environments"])
    team = weighted_choice(rng, config["distributions"]["teams"])
    owner = weighted_choice(rng, config["distributions"]["owners"])
    cost_center = weighted_choice(rng, config["distributions"]["cost_centers"])
    business_unit = weighted_choice(rng, config["distributions"]["business_units"])
    region = weighted_choice(rng, {item["name"]: item["weight"] for item in config["regions"]})
    app = choice(rng, config["naming"]["apps"])
    bucket_name = _build_bucket_name(profile_name, inventory_id, rng, config, env, app)
    creation_date = (generated_at - timedelta(days=rand_int(rng, 4, 820), hours=rand_int(rng, 0, 23))).isoformat()
    criticality = choice(rng, config["tag_dimensions"]["criticality"])
    compliance = choice(rng, config["tag_dimensions"]["compliance"])
    data_classification = choice(rng, config["tag_dimensions"]["data_classification"])
    lifecycle_owner = choice(rng, config["tag_dimensions"]["lifecycle_owners"])
    log_type = choice(rng, config["tag_dimensions"]["log_types"])
    return {
        "env": env,
        "team": team,
        "owner": owner,
        "cost_center": cost_center,
        "business_unit": business_unit,
        "region": region,
        "app": app,
        "bucket_name": bucket_name,
        "creation_date": creation_date,
        "criticality": criticality,
        "compliance": compliance,
        "data_classification": data_classification,
        "lifecycle_owner": lifecycle_owner,
        "log_type": log_type,
    }


def build_action_option(
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

    metadata = resource["metadata"]
    if action_key == "disable_inventory" and metadata.get("inventory_target_buckets"):
        option["parameters"]["target_buckets"] = metadata["inventory_target_buckets"]
    if action_key == "disable_logging" and metadata.get("logging_target_bucket"):
        option["parameters"]["target_bucket"] = metadata["logging_target_bucket"]
    if action_key == "disable_replication" and metadata.get("replication_target_buckets"):
        option["parameters"]["target_buckets"] = metadata["replication_target_buckets"]
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
            "bucket_size_gb": metadata["bucket_size_gb"],
            "object_count": metadata["object_count"],
            "versioning_status": metadata["versioning_status"],
        },
        "current_config": {
            "region": resource["region"],
            "versioning_status": metadata["versioning_status"],
            "lifecycle_rule_count": len(metadata["lifecycle_rules"]),
        },
        "target_config": {},
        "metadata": {
            "status": "healthy",
            "resource_name": resource["resource_name"],
            "estimated_monthly_cost": metadata["estimated_monthly_cost"],
            "criticality": metadata["criticality"],
            "owner": metadata["owner"],
        },
    }


def derive_target_config(action_key: str, resource: Dict[str, Any]) -> Dict[str, Any]:
    metadata = resource["metadata"]
    if action_key == "add_lifecycle":
        return {"lifecycle_rules": "present"}
    if action_key == "add_expiration":
        return {"expiration_days": 90}
    if action_key == "add_archival_transition":
        return {"transition_days": 30, "storage_class": "GLACIER"}
    if action_key == "enable_abort_mpu":
        return {"abort_incomplete_mpu_days": 7}
    if action_key == "disable_inventory":
        return {"inventory_configuration_count": 0}
    if action_key == "disable_logging":
        return {"logging_enabled": False}
    if action_key == "add_noncurrent_expiration":
        return {"noncurrent_expiration_days": 60}
    if action_key == "add_noncurrent_transition":
        return {"noncurrent_transition_days": 30, "storage_class": "GLACIER"}
    if action_key == "add_delete_marker_cleanup":
        return {"delete_marker_cleanup": True}
    if action_key == "disable_replication":
        return {"replication_rule_count": 0}
    if action_key == "review_inventory_destination":
        return {"inventory_target_buckets": metadata.get("inventory_target_buckets", [])}
    if action_key == "move_logs_to_archive":
        return {"logging_target_bucket": metadata.get("logging_target_bucket"), "expiration_days": 180}
    if action_key == "review_replication_scope":
        return {"replication_target_buckets": metadata.get("replication_target_buckets", [])}
    return {}


def derive_maxops_row(
    resource: Dict[str, Any],
    config: Dict[str, Any],
    profile_name: str,
    rng: random.Random,
) -> Dict[str, Any]:
    profile = config["bucket_profiles"][profile_name]
    finding_profile_name = profile.get("finding_profile")
    if not finding_profile_name:
        return build_neutral_maxops_row(resource)

    finding_rule = deepcopy(config["finding_profiles"][finding_profile_name])
    metadata = resource["metadata"]
    savings_monthly = round(metadata["estimated_monthly_cost"] * finding_rule["savings_factor"], 2)
    savings_yearly = round(savings_monthly * 12, 2)
    confidence = rand_range(rng, finding_rule["confidence_range"][0], finding_rule["confidence_range"][1], 3)
    risk_score = rand_range(rng, finding_rule["risk_score_range"][0], finding_rule["risk_score_range"][1], 3)

    context = {
        "bucket_name": resource["resource_name"],
        "bucket_size_gb": metadata["bucket_size_gb"],
        "object_count": metadata["object_count"],
        "logging_target_bucket": metadata.get("logging_target_bucket") or "n/a",
        "inventory_target_bucket": ", ".join(metadata.get("inventory_target_buckets", [])) or "n/a",
        "replication_target_bucket": ", ".join(metadata.get("replication_target_buckets", [])) or "n/a",
    }

    action_keys = [finding_rule["recommended_action"], *finding_rule["alternative_actions"]]
    actions = [build_action_option(action_key, config, resource, finding_rule) for action_key in action_keys]
    for action in actions:
        action["estimated_monthly_savings"] = savings_monthly
        action["estimated_annual_savings"] = savings_yearly

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
            "bucket_size_gb": metadata["bucket_size_gb"],
            "object_count": metadata["object_count"],
            "lifecycle_rule_count": len(metadata["lifecycle_rules"]),
            "versioning_status": metadata["versioning_status"],
            "logging_enabled": metadata["logging_enabled"],
            "inventory_configuration_count": metadata["inventory_configuration_count"],
            "replication_rule_count": metadata["replication_rule_count"],
        },
        "current_config": {
            "region": resource["region"],
            "versioning_status": metadata["versioning_status"],
            "lifecycle_rule_count": len(metadata["lifecycle_rules"]),
            "logging_enabled": metadata["logging_enabled"],
            "inventory_configuration_count": metadata["inventory_configuration_count"],
            "replication_rule_count": metadata["replication_rule_count"],
        },
        "target_config": derive_target_config(finding_rule["recommended_action"], resource),
        "metadata": {
            "status": "actionable",
            "resource_name": resource["resource_name"],
            "resource_id": resource["resource_id"],
            "estimated_monthly_cost": metadata["estimated_monthly_cost"],
            "owner": metadata["owner"],
            "team": metadata["team"],
            "criticality": metadata["criticality"],
        },
    }


def build_resource(
    inventory_id: int,
    profile_name: str,
    rng: random.Random,
    config: Dict[str, Any],
    generated_at: datetime,
    placeholder_values: Dict[str, str],
) -> Dict[str, Any]:
    generator_cfg = config["generator"]
    profile = config["bucket_profiles"][profile_name]
    context = _bucket_context(inventory_id, profile_name, rng, config, generated_at)
    bucket_name = context["bucket_name"]
    storage_mix = _weighted_storage_mix(rng, profile["storage_class_mix"])
    bucket_size_gb = rand_range(rng, profile["storage_size_gb_range"][0], profile["storage_size_gb_range"][1], 2)
    object_count = rand_int(rng, profile["object_count_range"][0], profile["object_count_range"][1])
    estimated_monthly_cost = _estimate_monthly_cost(bucket_size_gb, storage_mix)

    generated_names = {
        "logging_target_bucket": _logging_target_bucket(bucket_name),
        "inventory_target_bucket": _inventory_target_bucket(bucket_name),
        "replication_target_bucket": _replication_target_bucket(bucket_name),
        "replication_role_name": _role_name(bucket_name, inventory_id),
    }
    seed_details = _seed_details_for_bucket(
        profile,
        bucket_name,
        context["region"],
        generator_cfg["account_id"],
        generated_names,
        placeholder_values,
    )

    lifecycle_rules = deepcopy(seed_details["get_bucket_lifecycle_configuration"].get("Rules", []))
    versioning_status = seed_details["get_bucket_versioning"].get("Status") or "Disabled"
    logging_enabled = bool(seed_details["get_bucket_logging"].get("LoggingEnabled"))
    logging_target_bucket = seed_details["get_bucket_logging"].get("LoggingEnabled", {}).get("TargetBucket")
    inventory_count = len(seed_details["list_bucket_inventory_configurations"].get("InventoryConfigurationList", []))
    inventory_targets = _inventory_target_buckets(seed_details["list_bucket_inventory_configurations"])
    replication_rule_count = len(
        seed_details["get_bucket_replication"].get("ReplicationConfiguration", {}).get("Rules", [])
    )
    replication_targets = _replication_target_buckets(seed_details["get_bucket_replication"])

    tags = {
        "Name": bucket_name,
        "env": context["env"],
        "team": context["team"],
        "owner": context["owner"],
        "app": context["app"],
        "cost_center": context["cost_center"],
        "business_unit": context["business_unit"],
        "criticality": context["criticality"],
        "compliance": context["compliance"],
        "data_classification": context["data_classification"],
        "lifecycle_owner": context["lifecycle_owner"],
        "managed_by": "terraform",
    }
    if profile["bucket_kind"] == "log_sink":
        tags["log_type"] = context["log_type"]
    if logging_enabled:
        tags["access_logging"] = "enabled"
    if inventory_count > 0:
        tags["inventory_enabled"] = "true"
    if replication_rule_count > 0:
        tags["replication_enabled"] = "true"

    metadata = {
        "creation_date": context["creation_date"],
        "versioning_status": versioning_status,
        "lifecycle_rules": lifecycle_rules,
        "logging_enabled": logging_enabled,
        "logging_target_bucket": logging_target_bucket,
        "inventory_configuration_count": inventory_count,
        "inventory_target_buckets": inventory_targets,
        "replication_rule_count": replication_rule_count,
        "replication_target_buckets": replication_targets,
        "bucket_size_gb": bucket_size_gb,
        "object_count": object_count,
        "storage_class_mix": storage_mix,
        "estimated_monthly_cost": estimated_monthly_cost,
        "environment": context["env"],
        "owner": context["owner"],
        "team": context["team"],
        "cost_center": context["cost_center"],
        "business_unit": context["business_unit"],
        "criticality": context["criticality"],
        "bucket_kind": profile["bucket_kind"],
        "seed_profile": profile_name,
        "compliance": context["compliance"],
        "data_classification": context["data_classification"],
    }

    aws_details = {
        operation: _json_safe(seed_details[operation])
        for operation in SEED_DETAIL_OPERATIONS
    }

    return {
        "inventory_id": inventory_id,
        "resource_id": bucket_name,
        "resource_type": "s3",
        "resource_name": bucket_name,
        "account_id": generator_cfg["account_id"],
        "region": context["region"],
        "state": "active",
        "creation_date": context["creation_date"],
        "tags": tags,
        "metadata": metadata,
        "aws_details": aws_details,
    }


def build_inventory_payload(account_id: str, resources: List[Dict[str, Any]]) -> Dict[str, Any]:
    buckets = [
        {
            "Name": resource["resource_name"],
            "CreationDate": resource["creation_date"],
        }
        for resource in resources
    ]
    bucket_details = {
        resource["resource_name"]: deepcopy(resource["aws_details"])
        for resource in resources
    }
    return {
        "Buckets": buckets,
        "Owner": {
            "DisplayName": "maxops-demo",
            "ID": account_id,
        },
        "BucketDetails": bucket_details,
    }


def build_inventory_document(
    resources: List[Dict[str, Any]],
    aws_payload: Dict[str, Any],
    generated_at: datetime,
    account_id: str,
) -> Dict[str, Any]:
    buckets = []
    for resource in resources:
        record = {
            key: deepcopy(value)
            for key, value in resource.items()
            if key != "aws_details"
        }
        buckets.append(record)
    return {
        "generated_at": generated_at.isoformat(),
        "account_id": account_id,
        "inventory_count": len(resources),
        "aws_payload": aws_payload,
        "buckets": buckets,
    }


def generate_dataset(
    config: Dict[str, Any],
    count_override: Optional[int] = None,
    seed_override: Optional[int] = None,
) -> GeneratedArtifacts:
    validate_config(config)
    seed = seed_override if seed_override is not None else config["generator"]["seed"]
    bucket_count = count_override if count_override is not None else config["generator"]["bucket_count"]
    rng = random.Random(seed)
    generated_at = deterministic_generated_at(seed)
    placeholder_values = _load_resource_placeholders()

    profile_sequence = _profile_sequence(config, bucket_count, rng)
    resources: List[Dict[str, Any]] = []
    maxops_rows: List[Dict[str, Any]] = []

    for inventory_id, profile_name in enumerate(profile_sequence, start=1):
        resource = build_resource(
            inventory_id,
            profile_name,
            rng,
            config,
            generated_at,
            placeholder_values,
        )
        maxops_rows.append(derive_maxops_row(resource, config, profile_name, rng))
        resources.append(resource)

    aws_payload = build_inventory_payload(config["generator"]["account_id"], resources)
    inventory = build_inventory_document(resources, aws_payload, generated_at, config["generator"]["account_id"])
    return GeneratedArtifacts(inventory=inventory, maxops=maxops_rows)


def write_artifacts(artifacts: GeneratedArtifacts, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for existing in output_dir.glob("*.json"):
        existing.unlink()
    dump_json(output_dir / "inventory.json", artifacts.inventory)
    dump_json(output_dir / "s3_maxops.json", artifacts.maxops)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate deterministic synthetic S3 DB import data.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to S3 synthetic config JSON.")
    parser.add_argument("--count", type=int, default=None, help="Override bucket count.")
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
