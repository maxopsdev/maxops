"""Generate deterministic, DB-import-friendly EBS synthetic data from JSON config."""

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


CONFIG_PATH = CURRENT_DIR / "ebs_syn_config.json"
PAYLOAD_ROOT = REPO_ROOT / "tests" / "payloads" / "ebs"
METRIC_KEYS = [
    "volumereadops",
    "volumewriteops",
    "volumereadbytes",
    "volumewritebytes",
]
HISTORY_SERIES_COUNT = 15
HISTORY_SERIES_SPACING_DAYS = 30


def load_estimate_dynamodb_costs():
    module_path = REPO_ROOT / "app" / "checks" / "base.py"
    spec = importlib.util.spec_from_file_location("maxops_check_base", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


BASE_MODULE = load_estimate_dynamodb_costs()


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
    if sum(weights.values()) <= 0:
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
        "usage_profiles",
        "finding_profiles",
        "action_catalog",
        "seed_profiles",
    ]
    for section in required:
        if section not in config:
            raise ValueError(f"Missing required config section: {section}")
    for key in ["seed", "volume_count", "account_id", "default_region", "output_dir"]:
        if key not in config["generator"]:
            raise ValueError(f"Missing generator.{key}")
    if config["generator"]["volume_count"] <= 0:
        raise ValueError("generator.volume_count must be > 0")


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
    base = datetime(2026, 4, 23, 18, 0, tzinfo=timezone.utc)
    return base + timedelta(seconds=seed % 86400)


def _history_timestamps(anchor: datetime) -> List[str]:
    base = anchor.astimezone(timezone.utc).replace(hour=18, minute=0, second=0, microsecond=0)
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


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in text.lower()).strip("-")


def _seed_bundle(config: Dict[str, Any], name: str) -> Dict[str, Any]:
    profile = config["seed_profiles"][name]
    scenario_dir = PAYLOAD_ROOT / profile["seed_check_id"] / profile["seed_scenario"]
    payloads = {path.name: load_json(path) for path in scenario_dir.glob("*.json")}
    volume = deepcopy(payloads["describe_volumes.json"]["Volumes"][0])
    metrics = {
        metric_key: deepcopy(payloads[filename])
        for metric_key in METRIC_KEYS
        for filename in [next((item for item in payloads if item.endswith(f"__{metric_key}.json")), "")]
        if filename
    }
    return {
        "volume": volume,
        "metrics": metrics,
    }


def load_seed_bundles(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {name: _seed_bundle(config, name) for name in config["seed_profiles"]}


def estimate_ebs_monthly_cost(volume_type: str, size_gb: float, iops: int, throughput: int) -> float:
    per_gb = {
        "gp2": 0.10,
        "gp3": 0.08,
        "io1": 0.125,
        "io2": 0.138,
    }.get(volume_type, 0.09)
    base = size_gb * per_gb
    if volume_type == "gp3":
        base += max(0, iops - 3000) * 0.005
        base += max(0, throughput - 125) * 0.04
    if volume_type in {"io1", "io2"}:
        base += iops * (0.065 if volume_type == "io1" else 0.06)
    return round(base, 2)


def build_metric_history(
    rng: random.Random,
    generated_at: datetime,
    avg_iops: float,
    avg_throughput_mb: float,
    pattern: str,
) -> Dict[str, Dict[str, List[float] | List[str]]]:
    timestamps = _history_timestamps(generated_at)
    history: Dict[str, Dict[str, List[float] | List[str]]] = {}
    metric_bases = {
        "volumereadops": avg_iops * rng.uniform(0.35, 0.55),
        "volumewriteops": avg_iops * rng.uniform(0.45, 0.65),
        "volumereadbytes": avg_throughput_mb * 1024 * 1024 * rng.uniform(0.35, 0.55),
        "volumewritebytes": avg_throughput_mb * 1024 * 1024 * rng.uniform(0.45, 0.65),
    }
    for metric_key, base in metric_bases.items():
        series = {"timestamps": list(timestamps)}
        if pattern == "empty":
            series["average"] = []
            history[metric_key] = series
            continue
        volatility = 0.24 if pattern == "bursty" else 0.12 if pattern == "steady" else 0.05
        averages = []
        maximum = []
        for index in range(HISTORY_SERIES_COUNT):
            multiplier = 1 + rng.uniform(-volatility, volatility)
            if pattern == "bursty" and index % 4 == 0:
                multiplier *= 1.6
            value = max(0.0, base * multiplier)
            averages.append(round(value, 2))
            maximum.append(round(value * rng.uniform(1.1, 1.6), 2))
        series["average"] = averages
        series["maximum"] = maximum
        history[metric_key] = series
    return history


def _metric_payload(seed_payload: Dict[str, Any], label: str, stat_key: str, value: float, generated_at: datetime) -> Dict[str, Any]:
    payload = deepcopy(seed_payload)
    datapoints = payload.get("Datapoints", [])
    datapoints[:] = [{
        stat_key: round(value, 2),
        "Timestamp": generated_at.isoformat(),
        "Unit": "Count",
    }]
    payload["Label"] = label
    return payload


def _scenario_sequence(config: Dict[str, Any], count: int, rng: random.Random) -> List[str]:
    sequence = []
    if count >= 7:
        sequence.extend(
            [
                "healthy_gp2",
                "healthy_gp3",
                "underutilized_volume",
                "large_low_utilization",
                "iops_overprovisioned",
                "underutilized_provisioned_iops",
                "unattached_idle",
                "healthy_io",
            ]
        )
    while len(sequence) < count:
        sequence.append(weighted_choice(rng, config["distributions"]["usage_profiles"]))
    rng.shuffle(sequence)
    return sequence[:count]


def build_tags(env: str, app: str, role: str, owner: str, team: str, cost_center: str, business_unit: str, criticality: str) -> Dict[str, str]:
    return {
        "Name": f"{app}-{role}-{env}",
        "env": env,
        "owner": owner,
        "team": team,
        "cost_center": cost_center,
        "business_unit": business_unit,
        "criticality": criticality,
    }


def build_action_option(action_key: str, config: Dict[str, Any], target_config: Dict[str, Any], finding_rule: Dict[str, Any]) -> Dict[str, Any]:
    action = deepcopy(config["action_catalog"][action_key])
    return {
        "action_key": action_key,
        "label": action["label"],
        "description": action["description"],
        "parameters": deepcopy(target_config),
        "is_recommended": action_key == finding_rule["recommended_action"],
    }


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
            "avg_iops": metadata["avg_iops"],
            "avg_throughput_mb": metadata["avg_throughput_mb"],
            "volume_type": metadata["volume_type"],
            "size_gb": metadata["size"],
        },
        "current_config": {
            "volume_type": metadata["volume_type"],
            "size_gb": metadata["size"],
            "iops": metadata["iops"],
            "throughput": metadata["throughput"],
        },
        "target_config": {},
        "metadata": {
            "status": "healthy",
            "resource_name": resource["resource_name"],
            "volume_type": metadata["volume_type"],
            "monthly_cost_estimate": metadata["monthly_cost_estimate"],
        },
    }


def derive_maxops_row(resource: Dict[str, Any], config: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
    profile_name = resource["metadata"]["usage_profile"]
    profile = config["usage_profiles"][profile_name]
    finding_name = profile.get("finding_profile")
    if not finding_name:
        return build_neutral_maxops_row(resource)

    finding_rule = config["finding_profiles"][finding_name]
    metadata = resource["metadata"]
    confidence = rand_range(rng, finding_rule["confidence_range"][0], finding_rule["confidence_range"][1], 3)
    risk_score = rand_range(rng, finding_rule["risk_score_range"][0], finding_rule["risk_score_range"][1], 3)
    savings_monthly = round(metadata["monthly_cost_estimate"] * finding_rule["savings_factor"], 2)
    target_config: Dict[str, Any]
    if finding_name == "ebs_large_volumes_low_utilization":
        target_config = {
            "target_size_gb": max(100, int(metadata["size"] * 0.65)),
            "target_volume_type": metadata["volume_type"],
        }
    elif finding_name == "ebs_iops_overprovisioned_volume":
        target_config = {
            "target_iops": max(100, int(metadata["avg_iops"] * 1.5)),
            "target_volume_type": metadata["volume_type"],
        }
    elif finding_name == "ebs_underutilized_provisioned_iops":
        target_config = {
            "target_iops": max(100, int(metadata["avg_iops"] * 1.6)),
            "target_volume_type": "gp3",
        }
    else:
        target_config = {
            "target_volume_type": "gp3" if metadata["volume_type"] == "gp2" else metadata["volume_type"]
        }

    action_keys = [finding_rule["recommended_action"], *finding_rule["alternative_actions"]]
    actions = [build_action_option(action_key, config, target_config, finding_rule) for action_key in action_keys]
    return {
        "inventory_id": resource["inventory_id"],
        "check_id": finding_rule["check_id"],
        "finding_type": finding_rule["finding_type"],
        "title": finding_rule["title"],
        "description": f"{resource['resource_name']} shows low storage utilization relative to its current EBS configuration.",
        "severity": finding_rule["severity"],
        "confidence_score": confidence,
        "risk_score": risk_score,
        "recommended_action": finding_rule["recommended_action"],
        "recommended_actions": [finding_rule["recommended_action"], *finding_rule["alternative_actions"]],
        "available_actions": actions,
        "potential_savings_monthly": savings_monthly,
        "potential_savings_yearly": round(savings_monthly * 12, 2),
        "evidence": {
            "avg_iops": metadata["avg_iops"],
            "avg_throughput_mb": metadata["avg_throughput_mb"],
            "size_gb": metadata["size"],
            "provisioned_iops": metadata["iops"],
        },
        "current_config": {
            "volume_type": metadata["volume_type"],
            "size_gb": metadata["size"],
            "iops": metadata["iops"],
            "throughput": metadata["throughput"],
        },
        "target_config": target_config,
        "metadata": {
            "status": "actionable",
            "resource_name": resource["resource_name"],
            "usage_profile": profile_name,
            "volume_type": metadata["volume_type"],
            "size_gb": metadata["size"],
            "monthly_cost_estimate": metadata["monthly_cost_estimate"],
        },
    }


def build_resource(
    inventory_id: int,
    profile_name: str,
    rng: random.Random,
    config: Dict[str, Any],
    generated_at: datetime,
    seed_bundles: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    generator_cfg = config["generator"]
    usage_profile = config["usage_profiles"][profile_name]
    seed_bundle = seed_bundles[usage_profile["seed_profile"]]
    if inventory_id == 1:
        region_cfg = config["regions"][0]
    else:
        selected_region = weighted_choice(rng, {item["name"]: item["weight"] for item in config["regions"]})
        region_cfg = next(region for region in config["regions"] if region["name"] == selected_region)
    region = region_cfg["name"]
    az = choice(rng, region_cfg["azs"])
    env = weighted_choice(rng, config["distributions"]["environments"])
    team = weighted_choice(rng, config["distributions"]["teams"])
    owner = weighted_choice(rng, config["distributions"]["owners"])
    cost_center = weighted_choice(rng, config["distributions"]["cost_centers"])
    business_unit = weighted_choice(rng, config["distributions"]["business_units"])
    criticality = choice(rng, config["tag_dimensions"]["criticality"])
    compliance = choice(rng, config["tag_dimensions"]["compliance"])
    backup_tier = choice(rng, config["tag_dimensions"]["backup_tier"])
    data_classification = choice(rng, config["tag_dimensions"]["data_classification"])
    app = choice(rng, config["naming"]["apps"])
    role = choice(rng, config["naming"]["roles"])

    resource_id = f"vol-syn{inventory_id:013d}"
    resource_name = f"{app}-{role}-{env}-{inventory_id:03d}"
    state = "in-use" if usage_profile["attached"] else "available"
    size_gb = rand_int(rng, usage_profile["size_range_gb"][0], usage_profile["size_range_gb"][1])
    iops = rand_int(rng, usage_profile["iops_range"][0], usage_profile["iops_range"][1])
    throughput = rand_int(rng, usage_profile["throughput_range"][0], usage_profile["throughput_range"][1])
    avg_iops = rand_range(rng, usage_profile["avg_iops_range"][0], usage_profile["avg_iops_range"][1], 2)
    avg_throughput_mb = rand_range(
        rng,
        usage_profile["avg_throughput_mb_range"][0],
        usage_profile["avg_throughput_mb_range"][1],
        2,
    )
    create_time = (generated_at - timedelta(days=rng.randint(5, 420), hours=rng.randint(0, 23))).isoformat()
    metric_history = build_metric_history(rng, generated_at, avg_iops, avg_throughput_mb, usage_profile["metric_pattern"])
    tags = build_tags(env, app, role, owner, team, cost_center, business_unit, criticality)
    volume_type = usage_profile["volume_type"]
    monthly_cost = estimate_ebs_monthly_cost(volume_type, size_gb, iops, throughput)
    attachments = []
    instance_id = None
    if usage_profile["attached"]:
        instance_id = f"i-syn{inventory_id:014d}"
        attachments.append(
            {
                "AttachTime": create_time,
                "DeleteOnTermination": False,
                "Device": "/dev/sdf",
                "InstanceId": instance_id,
                "State": "attached",
                "VolumeId": resource_id,
            }
        )

    seed_volume = deepcopy(seed_bundle["volume"])
    old_volume_id = seed_volume.get("VolumeId", "")
    old_instance_id = ""
    if seed_volume.get("Attachments"):
        old_instance_id = seed_volume["Attachments"][0].get("InstanceId", "")
    replacements = {
        old_volume_id: resource_id,
        old_instance_id: instance_id or "",
        "123456789012": generator_cfg["account_id"],
        "maxops-payload-ebs-attached-gp2": resource_name,
        "maxops-payload-ebs-unattached": resource_name,
        "maxops-payload-ebs-provisioned-io1": resource_name,
    }
    aws_volume = _replace_strings(seed_volume, replacements)
    aws_volume["InventoryId"] = inventory_id
    aws_volume["VolumeId"] = resource_id
    aws_volume["VolumeType"] = volume_type
    aws_volume["Size"] = size_gb
    aws_volume["Iops"] = iops
    aws_volume["AvailabilityZone"] = az
    aws_volume["State"] = state
    aws_volume["CreateTime"] = create_time
    aws_volume["Encrypted"] = criticality == "tier1"
    aws_volume["MultiAttachEnabled"] = False
    aws_volume["Attachments"] = attachments
    aws_volume["Tags"] = [{"Key": key, "Value": value} for key, value in tags.items()]
    if throughput:
        aws_volume["Throughput"] = throughput

    cloudwatch_payloads = {
        "volumereadops": _metric_payload(
            seed_bundle["metrics"].get("volumereadops", {"Datapoints": []}),
            "VolumeReadOps",
            "Average",
            avg_iops * 0.45,
            generated_at,
        ),
        "volumewriteops": _metric_payload(
            seed_bundle["metrics"].get("volumewriteops", {"Datapoints": []}),
            "VolumeWriteOps",
            "Average",
            avg_iops * 0.55,
            generated_at,
        ),
        "volumereadbytes": _metric_payload(
            seed_bundle["metrics"].get("volumereadbytes", {"Datapoints": []}),
            "VolumeReadBytes",
            "Average",
            avg_throughput_mb * 1024 * 1024 * 0.45,
            generated_at,
        ),
        "volumewritebytes": _metric_payload(
            seed_bundle["metrics"].get("volumewritebytes", {"Datapoints": []}),
            "VolumeWriteBytes",
            "Average",
            avg_throughput_mb * 1024 * 1024 * 0.55,
            generated_at,
        ),
    }
    metadata = {
        "size": size_gb,
        "volume_type": volume_type,
        "iops": iops,
        "throughput": throughput,
        "encrypted": aws_volume["Encrypted"],
        "multi_attach_enabled": False,
        "usage_profile": profile_name,
        "monthly_cost_estimate": monthly_cost,
        "owner": owner,
        "team": team,
        "cost_center": cost_center,
        "business_unit": business_unit,
        "criticality": criticality,
        "compliance": compliance,
        "backup_tier": backup_tier,
        "data_classification": data_classification,
        "avg_iops": round(avg_iops, 2),
        "avg_throughput_mb": round(avg_throughput_mb, 2),
        "metric_history": metric_history,
        "create_time": create_time,
        "instance_id": instance_id,
    }
    return {
        "inventory_id": inventory_id,
        "resource_id": resource_id,
        "resource_type": "ebs",
        "resource_name": resource_name,
        "account_id": generator_cfg["account_id"],
        "region": region,
        "availability_zone": az,
        "state": state,
        "attached": usage_profile["attached"],
        "tags": tags,
        "metadata": metadata,
        "aws_volume": aws_volume,
        "cloudwatch_payloads": cloudwatch_payloads,
    }


def build_inventory_payload(account_id: str, generated_at: datetime, resources: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "Volumes": [deepcopy(resource["aws_volume"]) for resource in resources],
        "MetricDetails": {
            resource["resource_id"]: {
                key: _json_safe(payload)
                for key, payload in resource["cloudwatch_payloads"].items()
            }
            for resource in resources
        },
        "ResponseMetadata": {
            "HTTPHeaders": {
                "content-type": "text/xml;charset=UTF-8",
                "date": generated_at.strftime("%a, %d %b %Y %H:%M:%S GMT"),
                "server": "AmazonEC2",
            },
            "HTTPStatusCode": 200,
            "RequestId": f"ebs-syn-{generated_at.strftime('%Y%m%d%H%M%S')}",
            "RetryAttempts": 0,
        },
    }


def build_inventory_document(
    resources: List[Dict[str, Any]],
    aws_payload: Dict[str, Any],
    generated_at: datetime,
    account_id: str,
) -> Dict[str, Any]:
    return {
        "generated_at": generated_at.isoformat(),
        "account_id": account_id,
        "inventory_count": len(resources),
        "aws_payload": aws_payload,
        "volumes": [
            {
                key: deepcopy(value)
                for key, value in resource.items()
                if key not in {"aws_volume", "cloudwatch_payloads"}
            }
            for resource in resources
        ],
    }


def generate_dataset(
    config: Dict[str, Any],
    count_override: Optional[int] = None,
    seed_override: Optional[int] = None,
) -> GeneratedArtifacts:
    validate_config(config)
    seed = seed_override if seed_override is not None else config["generator"]["seed"]
    volume_count = count_override if count_override is not None else config["generator"]["volume_count"]
    rng = random.Random(seed)
    generated_at = deterministic_generated_at(seed)
    seed_bundles = load_seed_bundles(config)
    scenario_sequence = _scenario_sequence(config, volume_count, rng)

    resources = []
    maxops_rows = []
    for inventory_id, profile_name in enumerate(scenario_sequence, start=1):
        resource = build_resource(inventory_id, profile_name, rng, config, generated_at, seed_bundles)
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
    dump_json(output_dir / "ebs_maxops.json", artifacts.maxops)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate deterministic synthetic EBS DB import data.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to EBS synthetic config JSON.")
    parser.add_argument("--count", type=int, default=None, help="Override volume count.")
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
