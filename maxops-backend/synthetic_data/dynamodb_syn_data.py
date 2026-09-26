"""Generate deterministic, DB-import-friendly DynamoDB synthetic data from JSON config."""

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


CONFIG_PATH = CURRENT_DIR / "dynamodb_syn_config.json"
PAYLOAD_ROOT = REPO_ROOT / "tests" / "payloads" / "dynamodb"
HISTORY_SERIES_COUNT = 15
HISTORY_SERIES_SPACING_DAYS = 30


def load_base_module():
    module_path = REPO_ROOT / "app" / "checks" / "base.py"
    spec = importlib.util.spec_from_file_location("maxops_check_base", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


BASE_MODULE = load_base_module()
estimate_dynamodb_provisioned_monthly_cost = BASE_MODULE.estimate_dynamodb_provisioned_monthly_cost
estimate_dynamodb_ondemand_monthly_cost = BASE_MODULE.estimate_dynamodb_ondemand_monthly_cost


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


def validate_weights(name: str, weights: Dict[str, float]) -> None:
    if not weights:
        raise ValueError(f"{name} must not be empty")
    if sum(weights.values()) <= 0:
        raise ValueError(f"{name} weights must sum to > 0")
    for key, value in weights.items():
        if value < 0:
            raise ValueError(f"{name} weight for {key} must be non-negative")


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


def deterministic_generated_at(seed: int) -> datetime:
    base = datetime(2026, 4, 23, 19, 0, tzinfo=timezone.utc)
    return base + timedelta(seconds=seed % 86400)


def _history_timestamps(anchor: datetime) -> List[str]:
    base = anchor.astimezone(timezone.utc).replace(hour=19, minute=0, second=0, microsecond=0)
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


def _seed_bundle(config: Dict[str, Any], name: str) -> Dict[str, Any]:
    profile = config["seed_profiles"][name]
    scenario_dir = PAYLOAD_ROOT / profile["seed_check_id"] / profile["seed_scenario"]
    payloads = {path.name: load_json(path) for path in scenario_dir.glob("*.json")}
    list_tables = deepcopy(payloads["list_tables.json"])
    describe_filename = next((key for key in payloads if key.startswith("describe_table__")), "")
    describe_payload = deepcopy(payloads[describe_filename])
    tag_filename = next((key for key in payloads if key.startswith("list_tags_of_resource__")), "")
    tag_payload = deepcopy(payloads[tag_filename]) if tag_filename else {"Tags": []}
    metric_payloads = {
        key[len("get_metric_statistics__") : -len(".json")]: deepcopy(value)
        for key, value in payloads.items()
        if key.startswith("get_metric_statistics__")
    }
    return {
        "list_tables": list_tables,
        "describe_payload": describe_payload,
        "tag_payload": tag_payload,
        "metric_payloads": metric_payloads,
    }


def load_seed_bundles(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {name: _seed_bundle(config, name) for name in config["seed_profiles"]}


def _scenario_sequence(config: Dict[str, Any], count: int, rng: random.Random) -> List[str]:
    sequence = []
    if count >= 9:
        sequence.extend(
            [
                "table_low_rcu",
                "table_low_wcu",
                "table_low_item",
                "gsi_unused",
                "table_best_fit_on_demand",
                "table_best_fit_provisioned",
                "table_healthy_provisioned",
                "gsi_healthy",
                "table_healthy_on_demand",
            ]
        )
    while len(sequence) < count:
        sequence.append(weighted_choice(rng, config["distributions"]["usage_profiles"]))
    rng.shuffle(sequence)
    return sequence[:count]


def build_metric_history(
    rng: random.Random,
    generated_at: datetime,
    metrics: Dict[str, float],
    pattern: str,
) -> Dict[str, Dict[str, List[float] | List[str]]]:
    timestamps = _history_timestamps(generated_at)
    history: Dict[str, Dict[str, List[float] | List[str]]] = {}
    for metric_key, base in metrics.items():
        series = {"timestamps": list(timestamps)}
        volatility = 0.3 if pattern == "bursty" else 0.12 if pattern == "steady" else 0.05
        values = []
        maximum = []
        for index in range(HISTORY_SERIES_COUNT):
            multiplier = 1 + rng.uniform(-volatility, volatility)
            if pattern == "bursty" and index % 4 == 0:
                multiplier *= 1.8
            value = max(0.0, base * multiplier)
            values.append(round(value, 4))
            maximum.append(round(value * rng.uniform(1.05, 1.5), 4))
        series["average"] = values
        series["maximum"] = maximum
        history[metric_key] = series
    return history


def _datapoints_payload(seed_payload: Dict[str, Any], label: str, value: float, generated_at: datetime, stat_key: str) -> Dict[str, Any]:
    payload = deepcopy(seed_payload)
    payload["Label"] = label
    payload["Datapoints"] = [{
        stat_key: round(value, 4),
        "Timestamp": generated_at.isoformat(),
        "Unit": "Count",
    }]
    return payload


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
            "billing_mode": metadata.get("BillingMode") or metadata.get("billing_mode"),
            "item_count": metadata.get("item_count"),
        },
        "current_config": {
            "billing_mode": metadata.get("BillingMode") or metadata.get("billing_mode"),
            "read_capacity_units": metadata.get("read_capacity_units"),
            "write_capacity_units": metadata.get("write_capacity_units"),
        },
        "target_config": {},
        "metadata": {
            "status": "healthy",
            "resource_name": resource["resource_name"],
            "resource_type": resource["resource_type"],
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
    billing_mode = metadata.get("BillingMode") or metadata.get("billing_mode")
    target_config: Dict[str, Any]
    if finding_name == "dynamodb_underutilized_rcu":
        target_config = {
            "target_read_capacity_units": max(1, int(round(metadata["avg_consumed_rcu"] * 1.2))),
            "target_write_capacity_units": metadata.get("write_capacity_units", 0),
        }
    elif finding_name == "dynamodb_underutilized_wcu":
        target_config = {
            "target_read_capacity_units": metadata.get("read_capacity_units", 0),
            "target_write_capacity_units": max(1, int(round(metadata["avg_consumed_wcu"] * 1.2))),
        }
    elif finding_name == "dynamodb_underutilized_tables":
        target_config = {"target_state": "delete_or_archive"}
    elif finding_name == "dynamodb_gsi_unused":
        target_config = {"target_state": "delete_gsi"}
    elif finding_name == "dynamodb_best_fit_on_demand":
        target_config = {"target_billing_mode": "PAY_PER_REQUEST"}
    else:
        target_config = {
            "target_billing_mode": "PROVISIONED",
            "target_read_capacity_units": max(1, int(round(metadata["p95_consumed_rcu"] * 1.2))),
            "target_write_capacity_units": max(1, int(round(metadata["p95_consumed_wcu"] * 1.2))),
        }
    action_keys = [finding_rule["recommended_action"], *finding_rule["alternative_actions"]]
    actions = [build_action_option(action_key, config, target_config, finding_rule) for action_key in action_keys]
    return {
        "inventory_id": resource["inventory_id"],
        "check_id": finding_rule["check_id"],
        "finding_type": finding_rule["finding_type"],
        "title": finding_rule["title"],
        "description": f"{resource['resource_name']} matches the synthetic pattern for {finding_rule['check_id']}.",
        "severity": finding_rule["severity"],
        "confidence_score": confidence,
        "risk_score": risk_score,
        "recommended_action": finding_rule["recommended_action"],
        "recommended_actions": [finding_rule["recommended_action"], *finding_rule["alternative_actions"]],
        "available_actions": actions,
        "potential_savings_monthly": round(metadata["monthly_cost_estimate"] * finding_rule["savings_factor"], 2),
        "potential_savings_yearly": round(metadata["monthly_cost_estimate"] * finding_rule["savings_factor"] * 12, 2),
        "evidence": {
            "consumed_read_capacity_units": metadata["avg_consumed_rcu"],
            "consumed_write_capacity_units": metadata["avg_consumed_wcu"],
            "item_count": metadata["item_count"],
            "billing_mode": billing_mode,
            "avg_read_throttle_events": metadata["avg_read_throttle_events"],
            "avg_write_throttle_events": metadata["avg_write_throttle_events"],
        },
        "current_config": {
            "billing_mode": billing_mode,
            "read_capacity_units": metadata.get("read_capacity_units", 0),
            "write_capacity_units": metadata.get("write_capacity_units", 0),
        },
        "target_config": target_config,
        "metadata": {
            "status": "actionable",
            "resource_name": resource["resource_name"],
            "resource_type": resource["resource_type"],
            "usage_profile": profile_name,
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
    profile = config["usage_profiles"][profile_name]
    seed_bundle = seed_bundles[profile["seed_profile"]]
    if inventory_id == 1:
        region_cfg = config["regions"][0]
    else:
        selected_region = weighted_choice(rng, {item["name"]: item["weight"] for item in config["regions"]})
        region_cfg = next(region for region in config["regions"] if region["name"] == selected_region)
    region = region_cfg["name"]
    env = weighted_choice(rng, config["distributions"]["environments"])
    team = weighted_choice(rng, config["distributions"]["teams"])
    owner = weighted_choice(rng, config["distributions"]["owners"])
    cost_center = weighted_choice(rng, config["distributions"]["cost_centers"])
    business_unit = weighted_choice(rng, config["distributions"]["business_units"])
    criticality = choice(rng, config["tag_dimensions"]["criticality"])
    compliance = choice(rng, config["tag_dimensions"]["compliance"])
    table_class = choice(rng, config["tag_dimensions"]["table_class"])
    data_classification = choice(rng, config["tag_dimensions"]["data_classification"])
    app = choice(rng, config["naming"]["apps"])
    table_suffix = choice(rng, config["naming"]["tables"])
    table_name = f"{config['naming']['org_prefix']}-{app}-{env}-{table_suffix}-{inventory_id:03d}"
    table_arn = f"arn:aws:dynamodb:{region}:{config['generator']['account_id']}:table/{table_name}"
    old_table = seed_bundle["describe_payload"]["Table"]
    old_table_name = old_table["TableName"]
    old_table_arn = old_table["TableArn"]

    if profile["resource_type"] == "dynamodb_gsi":
        index_name = f"{app}_{choice(rng, config['naming']['gsi_suffixes'])}_{inventory_id:02d}"
        resource_id = f"{table_name}/{index_name}"
        resource_type = "dynamodb_gsi"
        resource_name = resource_id
    else:
        index_name = None
        resource_id = table_name
        resource_type = "dynamodb_table"
        resource_name = table_name

    replacements = {
        old_table_name: table_name,
        old_table_arn: table_arn,
        "123456789012": config["generator"]["account_id"],
        "maxops_payload_unused_gsi": index_name or "",
    }
    describe_table = _replace_strings(deepcopy(seed_bundle["describe_payload"]), replacements)
    describe_table["Table"]["TableName"] = table_name
    describe_table["Table"]["TableArn"] = table_arn
    describe_table["Table"]["CreationDateTime"] = (generated_at - timedelta(days=rng.randint(10, 360))).isoformat()
    describe_table["Table"]["ItemCount"] = int(rand_range(rng, profile["item_count_range"][0], profile["item_count_range"][1], 0))
    describe_table["Table"]["TableSizeBytes"] = describe_table["Table"]["ItemCount"] * rand_range(rng, 512, 2048, 0)
    if profile["billing_mode"] == "PROVISIONED":
        read_capacity_units = rand_range(rng, profile["provisioned_read_range"][0], profile["provisioned_read_range"][1], 2)
        write_capacity_units = rand_range(rng, profile["provisioned_write_range"][0], profile["provisioned_write_range"][1], 2)
        describe_table["Table"]["ProvisionedThroughput"]["ReadCapacityUnits"] = int(round(read_capacity_units))
        describe_table["Table"]["ProvisionedThroughput"]["WriteCapacityUnits"] = int(round(write_capacity_units))
        describe_table["Table"]["BillingModeSummary"] = {"BillingMode": "PROVISIONED"}
    else:
        read_capacity_units = 0.0
        write_capacity_units = 0.0
        describe_table["Table"].pop("ProvisionedThroughput", None)
        describe_table["Table"]["BillingModeSummary"] = {"BillingMode": "PAY_PER_REQUEST"}

    if index_name:
        gsi = describe_table["Table"].setdefault("GlobalSecondaryIndexes", [{}])[0]
        gsi["IndexName"] = index_name
        gsi["IndexArn"] = f"{table_arn}/index/{index_name}"
        gsi["ItemCount"] = int(rand_range(rng, profile["item_count_range"][0], profile["item_count_range"][1], 0))
        gsi["ProvisionedThroughput"] = {
            "NumberOfDecreasesToday": 0,
            "ReadCapacityUnits": int(round(rand_range(rng, profile["provisioned_read_range"][0], profile["provisioned_read_range"][1], 2))),
            "WriteCapacityUnits": int(round(rand_range(rng, profile["provisioned_write_range"][0], profile["provisioned_write_range"][1], 2))),
        }

    tag_payload = _replace_strings(deepcopy(seed_bundle["tag_payload"]), replacements)
    tags = [
        {"Key": "Name", "Value": table_name},
        {"Key": "env", "Value": env},
        {"Key": "owner", "Value": owner},
        {"Key": "team", "Value": team},
        {"Key": "cost_center", "Value": cost_center},
        {"Key": "business_unit", "Value": business_unit},
        {"Key": "criticality", "Value": criticality},
    ]
    tag_payload["Tags"] = tags

    avg_consumed_rcu = rand_range(rng, profile["consumed_read_range"][0], profile["consumed_read_range"][1], 4)
    avg_consumed_wcu = rand_range(rng, profile["consumed_write_range"][0], profile["consumed_write_range"][1], 4)
    avg_read_throttle_events = rand_range(rng, profile["read_throttle_range"][0], profile["read_throttle_range"][1], 4)
    avg_write_throttle_events = rand_range(rng, profile["write_throttle_range"][0], profile["write_throttle_range"][1], 4)
    item_count = describe_table["Table"]["GlobalSecondaryIndexes"][0]["ItemCount"] if index_name else describe_table["Table"]["ItemCount"]
    metric_history = build_metric_history(
        rng,
        generated_at,
        {
            "consumedreadcapacityunits": avg_consumed_rcu,
            "consumedwritecapacityunits": avg_consumed_wcu,
            "readthrottleevents": avg_read_throttle_events,
            "writethrottleevents": avg_write_throttle_events,
            "itemcount": float(item_count),
            "provisionedreadcapacityunits": read_capacity_units,
            "provisionedwritecapacityunits": write_capacity_units,
        },
        profile["metric_pattern"],
    )
    p95_consumed_rcu = max(metric_history["consumedreadcapacityunits"]["maximum"]) if metric_history["consumedreadcapacityunits"]["maximum"] else avg_consumed_rcu
    p95_consumed_wcu = max(metric_history["consumedwritecapacityunits"]["maximum"]) if metric_history["consumedwritecapacityunits"]["maximum"] else avg_consumed_wcu
    if profile["billing_mode"] == "PROVISIONED":
        monthly_cost = estimate_dynamodb_provisioned_monthly_cost(read_capacity_units, write_capacity_units)
    else:
        monthly_cost = estimate_dynamodb_ondemand_monthly_cost(avg_consumed_rcu, avg_consumed_wcu)

    metrics = {}
    for key, seed_payload in seed_bundle["metric_payloads"].items():
        metric_name = key.split("__")[-1]
        if "consumedreadcapacityunits" in metric_name:
            metrics[metric_name] = _datapoints_payload(seed_payload, "ConsumedReadCapacityUnits", avg_consumed_rcu, generated_at, "Average" if not index_name else "Sum")
        elif "consumedwritecapacityunits" in metric_name:
            metrics[metric_name] = _datapoints_payload(seed_payload, "ConsumedWriteCapacityUnits", avg_consumed_wcu, generated_at, "Average")
        elif "provisionedreadcapacityunits" in metric_name:
            metrics[metric_name] = _datapoints_payload(seed_payload, "ProvisionedReadCapacityUnits", read_capacity_units, generated_at, "Average")
        elif "provisionedwritecapacityunits" in metric_name:
            metrics[metric_name] = _datapoints_payload(seed_payload, "ProvisionedWriteCapacityUnits", write_capacity_units, generated_at, "Average")
        elif "readthrottleevents" in metric_name:
            metrics[metric_name] = _datapoints_payload(seed_payload, "ReadThrottleEvents", avg_read_throttle_events, generated_at, "Average")
        elif "writethrottleevents" in metric_name:
            metrics[metric_name] = _datapoints_payload(seed_payload, "WriteThrottleEvents", avg_write_throttle_events, generated_at, "Average")

    metadata = {
        "BillingMode": profile["billing_mode"] if resource_type == "dynamodb_table" else None,
        "billing_mode": profile["billing_mode"] if resource_type == "dynamodb_gsi" else profile["billing_mode"],
        "read_capacity_units": int(round(read_capacity_units)),
        "write_capacity_units": int(round(write_capacity_units)),
        "item_count": int(item_count),
        "table_class": table_class,
        "stream_enabled": env == "prod",
        "gsi_count": len(describe_table["Table"].get("GlobalSecondaryIndexes", [])),
        "monthly_cost_estimate": round(monthly_cost, 2),
        "usage_profile": profile_name,
        "owner": owner,
        "team": team,
        "business_unit": business_unit,
        "criticality": criticality,
        "compliance": compliance,
        "cost_center": cost_center,
        "data_classification": data_classification,
        "metric_history": metric_history,
        "avg_consumed_rcu": avg_consumed_rcu,
        "avg_consumed_wcu": avg_consumed_wcu,
        "p95_consumed_rcu": p95_consumed_rcu,
        "p95_consumed_wcu": p95_consumed_wcu,
        "avg_read_throttle_events": avg_read_throttle_events,
        "avg_write_throttle_events": avg_write_throttle_events,
        "resource_arn": table_arn if not index_name else f"{table_arn}/index/{index_name}",
        "TableName": table_name,
    }
    if index_name:
        metadata.update(
            {
                "IndexName": index_name,
                "ProvisionedThroughput": describe_table["Table"]["GlobalSecondaryIndexes"][0]["ProvisionedThroughput"],
            }
        )
    return {
        "inventory_id": inventory_id,
        "resource_id": resource_id,
        "resource_type": resource_type,
        "resource_name": resource_name,
        "account_id": config["generator"]["account_id"],
        "region": region,
        "state": "ACTIVE",
        "tags": {item["Key"]: item["Value"] for item in tags},
        "metadata": metadata,
        "aws_table": describe_table,
        "aws_tags_payload": tag_payload,
        "cloudwatch_payloads": metrics,
        "table_name": table_name,
    }


def build_inventory_payload(account_id: str, generated_at: datetime, resources: List[Dict[str, Any]]) -> Dict[str, Any]:
    table_names = []
    table_details: Dict[str, Any] = {}
    tag_details: Dict[str, Any] = {}
    metric_details: Dict[str, Any] = {}
    for resource in resources:
        table_name = resource["table_name"]
        if table_name not in table_details:
            table_names.append(table_name)
            table_details[table_name] = deepcopy(resource["aws_table"])
        tag_details[resource["resource_id"]] = deepcopy(resource["aws_tags_payload"])
        metric_details[resource["resource_id"]] = {
            key: _json_safe(payload) for key, payload in resource["cloudwatch_payloads"].items()
        }
    return {
        "TableNames": table_names,
        "TableDetails": table_details,
        "TagDetails": tag_details,
        "MetricDetails": metric_details,
        "ResponseMetadata": {
            "HTTPHeaders": {
                "content-type": "application/x-amz-json-1.0",
                "date": generated_at.strftime("%a, %d %b %Y %H:%M:%S GMT"),
            },
            "HTTPStatusCode": 200,
            "RequestId": f"dynamodb-syn-{generated_at.strftime('%Y%m%d%H%M%S')}",
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
        "resources": [
            {
                key: deepcopy(value)
                for key, value in resource.items()
                if key not in {"aws_table", "aws_tags_payload", "cloudwatch_payloads", "table_name"}
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
    resource_count = count_override if count_override is not None else config["generator"]["resource_count"]
    rng = random.Random(seed)
    generated_at = deterministic_generated_at(seed)
    seed_bundles = load_seed_bundles(config)
    scenario_sequence = _scenario_sequence(config, resource_count, rng)

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
    dump_json(output_dir / "dynamodb_maxops.json", artifacts.maxops)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate deterministic synthetic DynamoDB DB import data.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to DynamoDB synthetic config JSON.")
    parser.add_argument("--count", type=int, default=None, help="Override DynamoDB resource count.")
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
