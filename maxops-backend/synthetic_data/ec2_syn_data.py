"""Generate deterministic, DB-import-friendly EC2 synthetic data from JSON config."""

from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

CURRENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parents[0]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


CONFIG_PATH = CURRENT_DIR / "ec2_syn_config.json"
PAYLOAD_SEED_DIR = REPO_ROOT / "tests" / "payloads" / "ec2" / "ec2_idle_instances" / "pass_idle_instance"
HISTORY_SERIES_COUNT = 15
HISTORY_SERIES_SPACING_DAYS = 30
METRIC_SEED_FILES = {
    "cpuutilization": PAYLOAD_SEED_DIR / "get_metric_data_ec2.json",
    "networkin": PAYLOAD_SEED_DIR / "get_metric_data_ec2.json",
    "networkout": PAYLOAD_SEED_DIR / "get_metric_data_ec2.json",
    "memoryutilization": PAYLOAD_SEED_DIR / "get_metric_data_memory.json",
}
QUERY_ID_TO_ALIAS = {
    "cpu": "cpuutilization",
    "network_in": "networkin",
    "network_out": "networkout",
    "memory": "memoryutilization",
}


def load_estimate_ec2_monthly_cost():
    module_path = REPO_ROOT / "app" / "checks" / "base.py"
    spec = importlib.util.spec_from_file_location("maxops_check_base", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.estimate_ec2_monthly_cost


estimate_ec2_monthly_cost = load_estimate_ec2_monthly_cost()


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
        "usage_profiles",
        "finding_profiles",
        "action_catalog",
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

    distributions = config["distributions"]
    for name in [
        "states",
        "environments",
        "teams",
        "owners",
        "cost_centers",
        "business_units",
        "instance_types",
        "usage_profiles",
    ]:
        if name not in distributions:
            raise ValueError(f"Missing distributions.{name}")
        validate_weights(f"distributions.{name}", distributions[name])

    for profile_name, profile in config["usage_profiles"].items():
        if "allowed_states" not in profile:
            raise ValueError(f"usage_profiles.{profile_name}.allowed_states is required")
        if not set(profile["allowed_states"]).issubset(set(distributions["states"].keys())):
            raise ValueError(f"usage_profiles.{profile_name}.allowed_states references unknown state")
        for metric_name in ["cpu_range", "memory_range", "network_in_range", "network_out_range"]:
            if metric_name not in profile or len(profile[metric_name]) != 2:
                raise ValueError(f"usage_profiles.{profile_name}.{metric_name} must be a 2-item range")
        finding_profile = profile.get("finding_profile")
        if finding_profile and finding_profile not in config["finding_profiles"]:
            raise ValueError(f"usage_profiles.{profile_name}.finding_profile references unknown finding profile")

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
    filtered = {
        key: value for key, value in weights.items() if allowed_set is None or key in allowed_set
    }
    validate_weights("weighted_choice", filtered)
    keys = list(filtered.keys())
    choices = [filtered[key] for key in keys]
    return rng.choices(keys, weights=choices, k=1)[0]


def choice(rng: random.Random, values: List[str]) -> str:
    return values[rng.randrange(0, len(values))]


def rand_range(rng: random.Random, low: float, high: float, digits: int = 2) -> float:
    if low == high:
        return round(low, digits)
    return round(rng.uniform(low, high), digits)


def make_hex_id(rng: random.Random, prefix: str, length: int) -> str:
    alphabet = "0123456789abcdef"
    return f"{prefix}{''.join(rng.choice(alphabet) for _ in range(length))}"


def deterministic_generated_at(seed: int) -> datetime:
    base = datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc)
    return base + timedelta(seconds=seed % 86400)


def generate_launch_time(rng: random.Random, anchor: datetime, env: str, state: str) -> str:
    if state == "stopped":
        age_days = rng.randint(20, 380)
    elif env == "prod":
        age_days = rng.randint(45, 540)
    else:
        age_days = rng.randint(5, 220)
    age_hours = rng.randint(0, 23)
    launch_time = anchor - timedelta(days=age_days, hours=age_hours)
    return launch_time.isoformat()


def _history_timestamps(anchor: datetime) -> List[str]:
    base = anchor.astimezone(timezone.utc).replace(hour=13, minute=55, second=0, microsecond=0)
    return [
        (base - timedelta(days=HISTORY_SERIES_SPACING_DAYS * index)).isoformat()
        for index in range(HISTORY_SERIES_COUNT)
    ]


def _parse_seed_query_id(query_id: str) -> Tuple[Optional[str], Optional[str]]:
    for prefix, alias in QUERY_ID_TO_ALIAS.items():
        token = f"{prefix}_"
        if query_id.startswith(token):
            return alias, query_id[len(token):]
    return None, None


def load_metric_history_seed() -> Dict[str, Dict[str, Any]]:
    seed: Dict[str, Dict[str, Any]] = {
        "cpuutilization": {"stats": []},
        "networkin": {"stats": []},
        "networkout": {"stats": []},
        "memoryutilization": {"stats": []},
    }
    for path in sorted(set(METRIC_SEED_FILES.values())):
        payload = load_json(path)
        for result in payload.get("MetricDataResults", []):
            alias, stat = _parse_seed_query_id(result.get("Id", ""))
            if alias is None or stat is None:
                continue
            if stat not in seed[alias]["stats"]:
                seed[alias]["stats"].append(stat)
            seed[alias]["label_prefix"] = result.get("Label", "").rsplit(" ", 1)[0]
    return seed


def build_metric_history(
    rng: random.Random,
    generated_at: datetime,
    usage_profile: Dict[str, Any],
    metadata: Dict[str, Any],
    metric_seed: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, List[float] | List[str]]]:
    timestamps = _history_timestamps(generated_at)
    base_values = {
        "cpuutilization": metadata["avg_cpu_utilization"],
        "networkin": metadata["avg_network_in"],
        "networkout": metadata["avg_network_out"],
        "memoryutilization": metadata["avg_memory_utilization"],
    }
    range_keys = {
        "cpuutilization": "cpu_range",
        "networkin": "network_in_range",
        "networkout": "network_out_range",
        "memoryutilization": "memory_range",
    }
    history: Dict[str, Dict[str, List[float] | List[str]]] = {}

    for alias, seed_details in metric_seed.items():
        low, high = usage_profile[range_keys[alias]]
        base = base_values[alias]
        if low == 0.0 and high == 0.0:
            averages = [0.0] * HISTORY_SERIES_COUNT
        else:
            trend = rng.uniform(-0.12, 0.12)
            volatility = rng.uniform(0.05, 0.18)
            averages = []
            for index in range(HISTORY_SERIES_COUNT):
                month_factor = 1 + trend * (index / max(HISTORY_SERIES_COUNT - 1, 1))
                jitter = rng.uniform(1 - volatility, 1 + volatility)
                value = max(low, min(high, base * month_factor * jitter))
                averages.append(round(value, 2))

        # Keep the newest historical average aligned with the snapshot aggregate
        # so the details KPI cards and the trend chart describe the same point-in-time value.
        if averages:
            averages[0] = round(base, 2)

        max_multiplier = rng.uniform(1.45, 2.2)
        p90_multiplier = rng.uniform(1.12, 1.3)
        p95_multiplier = rng.uniform(1.18, 1.4)
        p99_multiplier = rng.uniform(1.24, 1.55)

        maximum = [round(max(value, value * max_multiplier), 2) for value in averages]
        p90 = [round(max(value, value * p90_multiplier), 2) for value in averages]
        p95 = [round(max(p90[index], averages[index] * p95_multiplier), 2) for index in range(HISTORY_SERIES_COUNT)]
        p99 = [round(max(p95[index], averages[index] * p99_multiplier), 2) for index in range(HISTORY_SERIES_COUNT)]

        metric_history = {"timestamps": list(timestamps)}
        available_stats = set(seed_details.get("stats", []))
        if "average" in available_stats:
            metric_history["average"] = averages
        if "maximum" in available_stats:
            metric_history["maximum"] = maximum
        if "p90" in available_stats:
            metric_history["p90"] = p90
        if "p95" in available_stats:
            metric_history["p95"] = p95
        if "p99" in available_stats:
            metric_history["p99"] = p99
        history[alias] = metric_history

    return history


def _series_stat(series: Dict[str, Any], stat: str) -> float | None:
    values = [float(value) for value in series.get(stat, []) if value is not None]
    return round(max(values), 2) if values else None


def build_rightsizing_metrics(metric_history: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    aliases = {
        "cpuutilization": "cpu_percent",
        "memoryutilization": "memory_percent",
        "networkin": "network_in_mbps",
        "networkout": "network_out_mbps",
    }
    signals = {
        "bw_in_allowance_exceeded": False,
        "bw_out_allowance_exceeded": False,
        "pps_allowance_exceeded": False,
        "conntrack_allowance_exceeded": False,
        "instance_ebs_iops_exceeded": False,
        "instance_ebs_throughput_exceeded": False,
        "high_ebs_queue": False,
        "high_ebs_latency": False,
        "ebs_burst_balance_depleted": False,
        "ebs_directional_metrics_incomplete": False,
    }
    windows: Dict[str, Any] = {}
    for label, days in (("14d", 14), ("30d", 30), ("60d", 60)):
        normalized: Dict[str, Any] = {}
        for alias, metric_name in aliases.items():
            series = metric_history.get(alias) or {}
            timestamps = series.get("timestamps") or []
            cap = 100.0 if alias in {"cpuutilization", "memoryutilization"} else None
            average = _series_stat(series, "average")
            maximum = _series_stat(series, "maximum")
            p95 = _series_stat(series, "p95")
            p99 = _series_stat(series, "p99")
            if cap is not None:
                average = min(average, cap) if average is not None else None
                maximum = min(maximum, cap) if maximum is not None else None
                p95 = min(p95, cap) if p95 is not None else None
                p99 = min(p99, cap) if p99 is not None else None
            normalized[metric_name] = {
                "average": average,
                "maximum": maximum,
                "p95": p95,
                "p99": p99,
                "sample_count": len(timestamps),
            }
        normalized["ebs_combined_iops"] = {
            "average": 0.0,
            "maximum": 0.0,
            "p95": 0.0,
            "p99": 0.0,
            "sample_count": len((metric_history.get("cpuutilization") or {}).get("timestamps") or []),
        }
        normalized["ebs_combined_mibps"] = {
            "average": 0.0,
            "maximum": 0.0,
            "p95": 0.0,
            "p99": 0.0,
            "sample_count": len((metric_history.get("cpuutilization") or {}).get("timestamps") or []),
        }
        windows[label] = {
            "lookback_days": days,
            "normalized": normalized,
            "signals": dict(signals),
        }
    return windows


def is_graviton(instance_type: str) -> bool:
    family = instance_type.split(".", 1)[0]
    return family.endswith("g") or family.startswith("t4g") or family.startswith("m6g") or family.startswith("c6g") or family.startswith("r6g")


def get_graviton_equivalent(instance_type: str) -> Optional[str]:
    mapping = {
        "t3.": "t4g.",
        "m5.": "m6g.",
        "c5.": "c6g.",
        "r5.": "r6g.",
    }
    for source, target in mapping.items():
        if instance_type.startswith(source):
            return instance_type.replace(source, target, 1)
    return None


def get_rightsize_target(instance_type: str) -> str:
    family, _, size = instance_type.partition(".")
    size_order = ["nano", "micro", "small", "medium", "large", "xlarge", "2xlarge"]
    if size not in size_order:
        return instance_type
    idx = size_order.index(size)
    if idx == 0:
        return instance_type
    return f"{family}.{size_order[idx - 1]}"


def build_name(rng: random.Random, config: Dict[str, Any], env: str, index: int) -> Tuple[str, str, str]:
    naming = config["naming"]
    app = choice(rng, naming["apps"])
    role = choice(rng, naming["roles"])
    resource_name = naming["template"].format(app=app, role=role, env=env, index=index)
    return resource_name, app, role


def sample_usage_profile(rng: random.Random, config: Dict[str, Any], state: str) -> str:
    distributions = config["distributions"]["usage_profiles"]
    allowed = [name for name, details in config["usage_profiles"].items() if state in details["allowed_states"]]
    return weighted_choice(rng, distributions, allowed=allowed)


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
    if action_key == "migrate_to_graviton":
        target = get_graviton_equivalent(resource["instance_type"])
        if target:
            option["parameters"]["target_instance_type"] = target
    if action_key == "rightsize":
        option["parameters"]["target_instance_type"] = get_rightsize_target(resource["instance_type"])
    return option


def format_reason(template: str, resource: Dict[str, Any]) -> str:
    metadata = resource["metadata"]
    context = {
        "avg_cpu_utilization": metadata["avg_cpu_utilization"],
        "avg_network_in": metadata["avg_network_in"],
        "avg_network_out": metadata["avg_network_out"],
        "instance_type": resource["instance_type"],
        "resource_name": resource["resource_name"],
        "env": resource["tags"]["env"],
    }
    return template.format(**context)


def build_private_ip(index: int) -> str:
    third = 10 + ((index - 1) // 200)
    fourth = 10 + ((index - 1) % 200)
    return f"10.0.{third}.{fourth}"


def build_public_ip(index: int) -> str:
    second = 20 + ((index - 1) // 250)
    third = 10 + (((index - 1) // 40) % 200)
    fourth = 20 + ((index - 1) % 200)
    return f"52.{second}.{third}.{fourth}"


def normalize_tag_list(tags: Dict[str, str]) -> List[Dict[str, str]]:
    return [{"Key": key, "Value": value} for key, value in tags.items()]


def build_aws_instance(
    inventory_id: int,
    resource: Dict[str, Any],
    rng: random.Random,
) -> Dict[str, Any]:
    metadata = resource["metadata"]
    resource_id = resource["resource_id"]
    az = resource["availability_zone"]
    private_ip = metadata["private_ip_address"]
    public_ip = metadata.get("public_ip_address")
    subnet_suffix = metadata["subnet_id"].split("-", 1)[1]
    network_interface_id = f"eni-{resource_id.split('-', 1)[1]}"
    volume_id = metadata["volume_ids"][0]
    launch_time = resource["launch_time"]
    state_name = resource["state"]
    state_code = 16 if state_name == "running" else 80

    instance = {
        "AmiLaunchIndex": 0,
        "Architecture": metadata["architecture"],
        "BlockDeviceMappings": [
            {
                "DeviceName": "/dev/xvda",
                "Ebs": {
                    "AttachTime": launch_time,
                    "DeleteOnTermination": True,
                    "Status": "attached" if state_name == "running" else "detached",
                    "VolumeId": volume_id,
                },
            }
        ],
        "CpuOptions": {
            "CoreCount": metadata["cpu_options"]["core_count"],
            "ThreadsPerCore": metadata["cpu_options"]["threads_per_core"],
        },
        "EbsOptimized": resource["instance_type"].startswith(("m", "c", "r")),
        "EnaSupport": True,
        "Hypervisor": "xen",
        "ImageId": metadata["image_id"],
        "InstanceId": resource_id,
        "InstanceType": resource["instance_type"],
        "InventoryId": inventory_id,
        "LaunchTime": launch_time,
        "Monitoring": {
            "State": "enabled" if metadata["cloudwatch_agent_installed"] else "disabled"
        },
        "NetworkInterfaces": [
            {
                "Attachment": {
                    "AttachTime": launch_time,
                    "AttachmentId": make_hex_id(rng, "eni-attach-", 17),
                    "DeleteOnTermination": True,
                    "DeviceIndex": 0,
                    "NetworkCardIndex": 0,
                    "Status": "attached" if state_name == "running" else "detached",
                },
                "Description": "",
                "Groups": [
                    {
                        "GroupId": metadata["security_groups"][0],
                        "GroupName": "default",
                    }
                ],
                "InterfaceType": "interface",
                "Ipv6Addresses": [],
                "MacAddress": f"0e:{subnet_suffix[:2]}:{subnet_suffix[2:4]}:{subnet_suffix[4:6]}:{subnet_suffix[6:8]}:{subnet_suffix[8:10]}",
                "NetworkInterfaceId": network_interface_id,
                "OwnerId": resource["account_id"],
                "PrivateDnsName": f"ip-{private_ip.replace('.', '-')}.{resource['region']}.compute.internal",
                "PrivateIpAddress": private_ip,
                "PrivateIpAddresses": [
                    {
                        "Primary": True,
                        "PrivateDnsName": f"ip-{private_ip.replace('.', '-')}.{resource['region']}.compute.internal",
                        "PrivateIpAddress": private_ip,
                    }
                ],
                "SourceDestCheck": True,
                "Status": "in-use" if state_name == "running" else "available",
                "SubnetId": metadata["subnet_id"],
                "VpcId": metadata["vpc_id"],
            }
        ],
        "Placement": {
            "AvailabilityZone": az,
            "GroupName": "",
            "Tenancy": "default",
        },
        "PlatformDetails": "Linux/UNIX",
        "PrivateDnsName": f"ip-{private_ip.replace('.', '-')}.{resource['region']}.compute.internal",
        "PrivateIpAddress": private_ip,
        "RootDeviceName": "/dev/xvda",
        "RootDeviceType": "ebs",
        "SecurityGroups": [
            {
                "GroupId": metadata["security_groups"][0],
                "GroupName": "default",
            }
        ],
        "SourceDestCheck": True,
        "State": {
            "Code": state_code,
            "Name": state_name,
        },
        "StateTransitionReason": "" if state_name == "running" else "User initiated (2026-03-31 17:14:12 GMT)",
        "SubnetId": metadata["subnet_id"],
        "Tags": normalize_tag_list(resource["tags"]),
        "VirtualizationType": "hvm",
        "VpcId": metadata["vpc_id"],
    }
    if public_ip:
        instance["PublicIpAddress"] = public_ip
        instance["PublicDnsName"] = f"ec2-{public_ip.replace('.', '-')}.compute-1.amazonaws.com"
        instance["NetworkInterfaces"][0]["Association"] = {
            "IpOwnerId": "amazon",
            "PublicDnsName": instance["PublicDnsName"],
            "PublicIp": public_ip,
        }
        instance["NetworkInterfaces"][0]["PrivateIpAddresses"][0]["Association"] = {
            "IpOwnerId": "amazon",
            "PublicDnsName": instance["PublicDnsName"],
            "PublicIp": public_ip,
        }
    return instance


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
            "avg_network_in": metadata["avg_network_in"],
            "avg_network_out": metadata["avg_network_out"],
            "usage_profile": metadata["usage_profile"],
        },
        "current_config": {
            "instance_type": resource["instance_type"],
            "state": resource["state"],
            "region": resource["region"],
        },
        "target_config": {},
        "metadata": {
            "status": "healthy",
            "resource_name": resource["resource_name"],
            "usage_profile": metadata["usage_profile"],
            "monthly_cost_estimate": metadata["monthly_cost_estimate"],
        },
    }


def derive_maxops_row(
    resource: Dict[str, Any],
    config: Dict[str, Any],
    profile_name: str,
    rng: random.Random,
) -> Dict[str, Any]:
    usage_profile = config["usage_profiles"][profile_name]
    finding_profile_name = usage_profile.get("finding_profile")
    if not finding_profile_name:
        return build_neutral_maxops_row(resource)

    finding_rule = deepcopy(config["finding_profiles"][finding_profile_name])
    if finding_profile_name == "graviton_candidate" and is_graviton(resource["instance_type"]):
        return build_neutral_maxops_row(resource)
    if finding_profile_name == "unused_terminate_candidate" and resource["state"] != "stopped":
        return build_neutral_maxops_row(resource)
    if finding_profile_name in {"idle_stop_candidate", "idle_review_candidate"} and resource["state"] != "running":
        return build_neutral_maxops_row(resource)

    current_monthly = resource["metadata"]["monthly_cost_estimate"]
    savings_monthly = round(current_monthly * finding_rule["savings_factor"], 2)
    savings_yearly = round(savings_monthly * 12, 2)
    confidence = rand_range(rng, finding_rule["confidence_range"][0], finding_rule["confidence_range"][1], 3)
    risk_score = rand_range(rng, finding_rule["risk_score_range"][0], finding_rule["risk_score_range"][1], 3)

    target_config: Dict[str, Any] = {}
    if finding_rule["recommended_action"] == "migrate_to_graviton":
        target = get_graviton_equivalent(resource["instance_type"])
        if target:
            target_config["instance_type"] = target
    elif finding_rule["recommended_action"] == "rightsize":
        target_config["instance_type"] = get_rightsize_target(resource["instance_type"])

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
        "description": format_reason(finding_rule["reason_template"], resource),
        "severity": finding_rule["severity"],
        "confidence_score": confidence,
        "risk_score": risk_score,
        "recommended_action": finding_rule["recommended_action"],
        "recommended_actions": action_keys,
        "available_actions": actions,
        "potential_savings_monthly": savings_monthly,
        "potential_savings_yearly": savings_yearly,
        "evidence": {
            "avg_cpu_utilization": resource["metadata"]["avg_cpu_utilization"],
            "avg_network_in": resource["metadata"]["avg_network_in"],
            "avg_network_out": resource["metadata"]["avg_network_out"],
            "usage_profile": resource["metadata"]["usage_profile"],
        },
        "current_config": {
            "instance_type": resource["instance_type"],
            "state": resource["state"],
            "region": resource["region"],
        },
        "target_config": target_config,
        "metadata": {
            "status": "actionable",
            "resource_name": resource["resource_name"],
            "resource_id": resource["resource_id"],
            "monthly_cost_estimate": resource["metadata"]["monthly_cost_estimate"],
            "business_service": resource["metadata"]["business_service"],
            "criticality": resource["metadata"]["criticality"],
            "owner": resource["metadata"]["owner"],
        },
    }


def build_resource(
    inventory_id: int,
    rng: random.Random,
    config: Dict[str, Any],
    generated_at: datetime,
    metric_seed: Dict[str, Dict[str, Any]],
    required_example: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    distributions = config["distributions"]
    generator_cfg = config["generator"]

    state = weighted_choice(rng, distributions["states"])
    env = weighted_choice(rng, distributions["environments"])
    team = weighted_choice(rng, distributions["teams"])
    owner = weighted_choice(rng, distributions["owners"])
    cost_center = weighted_choice(rng, distributions["cost_centers"])
    business_unit = weighted_choice(rng, distributions["business_units"])
    selected_region = weighted_choice(
        rng,
        {region["name"]: region["weight"] for region in config["regions"]},
    )
    instance_type = weighted_choice(rng, distributions["instance_types"])
    usage_profile_name = sample_usage_profile(rng, config, state)

    example = required_example or {}
    state = example.get("state", state)
    env = example.get("environment", env)
    selected_region = example.get("region", selected_region)
    instance_type = example.get("instance_type", instance_type)
    usage_profile_name = example.get("usage_profile", usage_profile_name)

    region_cfg = next(region for region in config["regions"] if region["name"] == selected_region)
    region = region_cfg["name"]
    az = example.get("availability_zone") or choice(rng, region_cfg["azs"])
    usage_profile = config["usage_profiles"][usage_profile_name]

    if usage_profile_name == "migration_candidate" and is_graviton(instance_type):
        alternatives = [
            instance for instance in distributions["instance_types"] if not is_graviton(instance)
        ]
        instance_type = choice(rng, alternatives)

    resource_name, app_name, role = build_name(rng, config, env, inventory_id)
    resource_id = make_hex_id(rng, "i-", 17)
    vpc_id = make_hex_id(rng, "vpc-", 17)
    subnet_id = make_hex_id(rng, "subnet-", 17)
    security_group_id = make_hex_id(rng, "sg-", 17)
    image_id = make_hex_id(rng, "ami-", 17)
    cloudwatch_agent = rng.random() <= usage_profile["cloudwatch_agent_probability"]
    launch_time = generate_launch_time(rng, generated_at, env, state)
    monthly_cost = round(estimate_ec2_monthly_cost(instance_type), 2)
    cpu = float(example.get("cpu_utilization", rand_range(rng, usage_profile["cpu_range"][0], usage_profile["cpu_range"][1])))
    memory = float(example.get("memory_utilization", rand_range(rng, usage_profile["memory_range"][0], usage_profile["memory_range"][1])))
    net_in = float(example.get("network_in", rand_range(rng, usage_profile["network_in_range"][0], usage_profile["network_in_range"][1])))
    net_out = float(example.get("network_out", rand_range(rng, usage_profile["network_out_range"][0], usage_profile["network_out_range"][1])))

    criticality = usage_profile.get("criticality_bias") or choice(rng, config["tag_dimensions"]["criticality"])
    uptime_pattern = choice(rng, config["tag_dimensions"]["uptime_pattern"])
    compliance = choice(rng, config["tag_dimensions"]["compliance"])
    patch_group = choice(rng, config["tag_dimensions"]["patch_group"])

    private_ip = build_private_ip(inventory_id)
    public_ip = None if state == "stopped" else build_public_ip(inventory_id)
    core_count = 1 if any(size in instance_type for size in ["micro", "small", "medium", "large"]) else 2

    tags = {
        "Name": resource_name,
        "env": env,
        "team": team,
        "owner": owner,
        "app": app_name,
        "role": role,
        "cost_center": cost_center,
        "business_unit": business_unit,
        "patch_group": patch_group,
        "compliance": compliance,
        "criticality": criticality,
        "managed_by": "terraform",
    }

    metadata = {
        "vpc_id": vpc_id,
        "subnet_id": subnet_id,
        "security_groups": [security_group_id],
        "cloudwatch_agent_installed": cloudwatch_agent,
        "avg_cpu_utilization": cpu,
        "avg_memory_utilization": memory,
        "avg_network_in": net_in,
        "avg_network_out": net_out,
        "usage_profile": usage_profile_name,
        "monthly_cost_estimate": monthly_cost,
        "owner": owner,
        "criticality": criticality,
        "business_service": app_name,
        "uptime_pattern": uptime_pattern,
        "last_deployment_at": (generated_at - timedelta(days=rng.randint(1, 90))).isoformat(),
        "is_production": env == "prod",
        "image_id": image_id,
        "architecture": "arm64" if is_graviton(instance_type) else "x86_64",
        "cpu_options": {
            "core_count": core_count,
            "threads_per_core": 2,
        },
        "private_ip_address": private_ip,
        "public_ip_address": public_ip,
        "volume_ids": [make_hex_id(rng, "vol-", 17)],
    }
    metadata["metric_history"] = build_metric_history(rng, generated_at, usage_profile, metadata, metric_seed)
    metadata["rightsizing_metrics"] = build_rightsizing_metrics(metadata["metric_history"])
    metadata["memory_metric_source"] = "synthetic_cloudwatch_agent"
    metadata["memory_metric_status"] = "usable"
    metadata["instance_store_present"] = False
    metadata["attached_eni_count"] = 1
    metadata["attached_volume_count"] = 0

    return {
        "inventory_id": inventory_id,
        "resource_id": resource_id,
        "resource_type": "ec2",
        "resource_name": resource_name,
        "account_id": generator_cfg["account_id"],
        "region": region,
        "availability_zone": az,
        "state": state,
        "instance_type": instance_type,
        "launch_time": launch_time,
        "tags": tags,
        "metadata": metadata,
    }


def build_inventory_payload(
    account_id: str,
    generated_at: datetime,
    resources: List[Dict[str, Any]],
    rng: random.Random,
) -> Dict[str, Any]:
    reservations = []
    for resource in resources:
        reservations.append(
            {
                "Groups": [],
                "Instances": [build_aws_instance(resource["inventory_id"], resource, rng)],
                "OwnerId": account_id,
                "ReservationId": make_hex_id(rng, "r-", 17),
            }
        )
    return {
        "Reservations": reservations,
        "ResponseMetadata": {
            "HTTPHeaders": {
                "content-type": "text/xml;charset=UTF-8",
                "date": generated_at.strftime("%a, %d %b %Y %H:%M:%S GMT"),
                "server": "AmazonEC2",
            },
            "HTTPStatusCode": 200,
            "RequestId": make_hex_id(rng, "", 32),
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
        "instances": resources,
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
    metric_seed = load_metric_history_seed()

    resources: List[Dict[str, Any]] = []
    maxops_rows: List[Dict[str, Any]] = []
    for inventory_id in range(1, instance_count + 1):
        required_examples = config.get("required_examples") or []
        required_example = (
            required_examples[inventory_id - 1]
            if inventory_id <= len(required_examples)
            else None
        )
        resource = build_resource(
            inventory_id,
            rng,
            config,
            generated_at,
            metric_seed,
            required_example=required_example,
        )
        maxops_row = derive_maxops_row(resource, config, resource["metadata"]["usage_profile"], rng)
        resources.append(resource)
        maxops_rows.append(maxops_row)

    aws_payload = build_inventory_payload(config["generator"]["account_id"], generated_at, resources, rng)
    inventory = build_inventory_document(
        resources,
        aws_payload,
        generated_at,
        config["generator"]["account_id"],
    )
    return GeneratedArtifacts(inventory=inventory, maxops=maxops_rows)


def write_artifacts(artifacts: GeneratedArtifacts, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for existing in output_dir.glob("*.json"):
        existing.unlink()
    dump_json(output_dir / "inventory.json", artifacts.inventory)
    dump_json(output_dir / "ec2_maxops.json", artifacts.maxops)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate deterministic synthetic EC2 DB import data.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to ec2 synthetic config JSON.")
    parser.add_argument("--count", type=int, default=None, help="Override instance count.")
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
