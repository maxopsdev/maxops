"""Generate deterministic, DB-import-friendly ECS synthetic data from JSON config."""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

CURRENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parents[0]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


CONFIG_PATH = CURRENT_DIR / "ecs_syn_config.json"


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
    for section in (
        "generator",
        "regions",
        "environments",
        "teams",
        "apps",
        "profiles",
    ):
        if section not in config:
            raise ValueError(f"Missing required config section: {section}")
    generator = config["generator"]
    for key in ("seed", "resource_count", "account_id", "output_dir"):
        if key not in generator:
            raise ValueError(f"Missing generator.{key}")
    if int(generator["resource_count"]) <= 0:
        raise ValueError("generator.resource_count must be > 0")
    if not config["profiles"]:
        raise ValueError("profiles must not be empty")


def deterministic_generated_at(seed: int) -> datetime:
    base = datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc)
    return base + timedelta(seconds=seed % 86400)


def _slug(parts: List[str]) -> str:
    text = "-".join(part.strip().lower().replace("_", "-") for part in parts if part)
    allowed = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in text)
    return "-".join(filter(None, allowed.split("-")))[:255].rstrip("-")


def _metric_history(anchor: datetime, profile: Dict[str, Any]) -> Dict[str, Any]:
    timestamps = [
        (anchor - timedelta(days=7 * index)).isoformat()
        for index in range(12)
    ]
    cpu = float(profile["avg_cpu_utilization"])
    memory = float(profile["avg_memory_utilization"])
    desired = int(profile["desired_count"])
    cpu_average = [round(max(0.0, cpu + ((index % 3) - 1) * 1.7), 2) for index in range(12)]
    memory_average = [round(max(0.0, memory + ((index % 4) - 1.5) * 1.9), 2) for index in range(12)]
    return {
        "cpu_utilization": {
            "timestamps": timestamps,
            "average": cpu_average,
            "maximum": [round(min(100.0, value + 18.0 + (index % 4) * 2.5), 2) for index, value in enumerate(cpu_average)],
            "p99": [round(min(100.0, value + 13.0 + (index % 3) * 1.8), 2) for index, value in enumerate(cpu_average)],
            "p95": [round(min(100.0, value + 8.0 + (index % 2) * 1.5), 2) for index, value in enumerate(cpu_average)],
        },
        "memory_utilization": {
            "timestamps": timestamps,
            "average": memory_average,
            "maximum": [round(min(100.0, value + 14.0 + (index % 4) * 2.0), 2) for index, value in enumerate(memory_average)],
            "p99": [round(min(100.0, value + 10.0 + (index % 3) * 1.5), 2) for index, value in enumerate(memory_average)],
            "p95": [round(min(100.0, value + 6.0 + (index % 2) * 1.3), 2) for index, value in enumerate(memory_average)],
        },
        "desired_count": {
            "timestamps": timestamps,
            "maximum": [desired for _ in timestamps],
        },
    }


def _project_metric_value(value: Any, ratio: float | None) -> float | None:
    if value is None or ratio is None:
        return None
    return round(float(value) * ratio, 2)


def _option_chart(metadata: Dict[str, Any], target_cpu: int | None) -> Dict[str, Any]:
    history = metadata.get("metric_history") or {}
    series = history.get("cpu_utilization") if isinstance(history, dict) else {}
    series = series if isinstance(series, dict) else {}
    timestamps = series.get("timestamps") or []
    averages = series.get("average") or []
    maximums = series.get("maximum") or []
    p99s = series.get("p99") or []
    p95s = series.get("p95") or []
    current_cpu = int(metadata.get("cpu_reservation") or 0)
    scale_ratio = float(current_cpu) / float(target_cpu) if current_cpu and target_cpu else None
    points: List[Dict[str, Any]] = []
    for index, timestamp in enumerate(timestamps):
        average = float(averages[index]) if index < len(averages) and averages[index] is not None else None
        maximum = float(maximums[index]) if index < len(maximums) and maximums[index] is not None else average
        p99 = float(p99s[index]) if index < len(p99s) and p99s[index] is not None else maximum
        p95 = float(p95s[index]) if index < len(p95s) and p95s[index] is not None else p99
        points.append(
            {
                "timestamp": timestamp,
                "average": average,
                "maximum": maximum,
                "p99": p99,
                "p95": p95,
                "maximum_on_target": _project_metric_value(maximum, scale_ratio),
                "p99_on_target": _project_metric_value(p99, scale_ratio),
                "p95_on_target": _project_metric_value(p95, scale_ratio),
                "scale_ratio": scale_ratio,
            }
        )
    return {
        "metric": "CPUUtilization",
        "unit": "Percent",
        "points": points,
        "target_capacity_line": 100,
    }


def _round_reservation(value: float, step: int, tier: str) -> int:
    quotient = value / step
    if tier == "conservative":
        return int((int(quotient) + (0 if quotient.is_integer() else 1)) * step)
    if tier == "aggressive":
        return int(quotient) * step
    return int(round(quotient) * step)


def _rightsizer_tiers(
    resource: Dict[str, Any],
    profile: Dict[str, Any],
    target_config: Dict[str, Any],
    monthly_savings: float,
) -> Dict[str, Any]:
    metadata = resource["metadata"]
    current_cpu = int(metadata["cpu_reservation"])
    current_memory = int(metadata["memory_reservation"])
    base_target_cpu = int(target_config["cpu_reservation"])
    base_target_memory = int(target_config["memory_reservation"])
    avg_cpu_ratio = float(metadata["avg_cpu_utilization"]) / 100.0
    avg_memory_ratio = float(metadata["avg_memory_utilization"]) / 100.0

    def option(tier: str, ratio: float, risk: str) -> Dict[str, Any]:
        target_cpu = max(256, _round_reservation(base_target_cpu * ratio, 256, tier))
        target_memory = max(512, _round_reservation(base_target_memory * ratio, 512, tier))
        target_shape = f"{target_cpu} CPU / {target_memory} MiB"
        option_savings = round(monthly_savings * (2 - ratio), 2)
        return {
            "tier": tier,
            "target_instance_type": target_shape,
            "target_capacity": target_shape,
            "target_cpu_reservation": target_cpu,
            "target_memory_reservation": target_memory,
            "target_desired_count": target_config.get("desired_count"),
            "monthly_savings": option_savings,
            "yearly_savings": round(option_savings * 12, 2),
            "classification": "ACTIONABLE",
            "projected_cpu_util": round(avg_cpu_ratio * (current_cpu / target_cpu), 4),
            "projected_memory_util": round(avg_memory_ratio * (current_memory / target_memory), 4),
            "risk_assessment": {
                "telemetry": "LOW",
                "compute": risk,
                "memory": risk,
                "network": None,
                "storage": None,
                "compatibility": "LOW",
                "migration": None,
                "overall": risk,
            },
            "reason_codes": [profile["check_id"]],
            "evidence": {
                "avg_cpu_utilization": metadata["avg_cpu_utilization"],
                "avg_memory_utilization": metadata["avg_memory_utilization"],
                "desired_count": metadata["desired_count"],
                "running_count": metadata["running_count"],
            },
            "chart": _option_chart(metadata, target_cpu),
        }

    return {
        "conservative": option("conservative", 1.25, "LOW"),
        "balanced": option("balanced", 1.0, "MEDIUM"),
        "aggressive": option("aggressive", 0.75, "HIGH"),
        "default": "balanced",
    }


def _build_resource(
    *,
    inventory_id: int,
    config: Dict[str, Any],
    profile: Dict[str, Any],
    rng: random.Random,
    generated_at: datetime,
) -> Dict[str, Any]:
    env = rng.choice(config["environments"])
    team = rng.choice(config["teams"])
    app = rng.choice(config["apps"])
    region = rng.choice(config["regions"])
    resource_type = profile["resource_type"]
    resource_id = _slug(["maxops-demo", env, app, profile["name"], f"{inventory_id:03d}"])
    cluster_name = _slug(["maxops-demo", env, app, "cluster"])
    resource_name = resource_id
    tags = {
        "env": env,
        "team": team,
        "service": app,
        "owner": f"{team}.team",
    }
    metadata = {
        "usage_profile": profile["name"],
        "environment": env,
        "team": team,
        "cluster_name": cluster_name,
        "desired_count": int(profile["desired_count"]),
        "running_count": int(profile["running_count"]),
        "cpu_reservation": int(profile["cpu_reservation"]),
        "memory_reservation": int(profile["memory_reservation"]),
        "avg_cpu_utilization": float(profile["avg_cpu_utilization"]),
        "avg_memory_utilization": float(profile["avg_memory_utilization"]),
        "monthly_cost_estimate": float(profile["monthly_cost"]),
        "metric_history": _metric_history(generated_at, profile),
        "resource_kind": resource_type,
    }
    return {
        "inventory_id": inventory_id,
        "resource_id": resource_id,
        "resource_name": resource_name,
        "resource_type": resource_type,
        "account_id": config["generator"]["account_id"],
        "region": region,
        "state": "active" if int(profile["running_count"]) > 0 else "inactive",
        "tags": tags,
        "metadata": metadata,
        "aws_payload": {
            "clusterName": cluster_name,
            "serviceName": resource_name if resource_type == "ecs_service" else None,
            "desiredCount": int(profile["desired_count"]),
            "runningCount": int(profile["running_count"]),
            "launchType": "FARGATE",
        },
    }


def _build_maxops_row(resource: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    monthly_savings = float(profile["monthly_savings"])
    check_id = profile.get("check_id")
    metadata = resource["metadata"]
    if not check_id:
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
            },
            "current_config": metadata,
            "target_config": metadata,
            "metadata": {
                "status": "healthy",
                "usage_profile": profile["name"],
                "resource_state": resource["state"],
            },
        }
    target_config = {
        **metadata,
        "desired_count": max(0, int(metadata["desired_count"]) - 1),
        "cpu_reservation": max(256, int(metadata["cpu_reservation"]) // 2),
        "memory_reservation": max(512, int(metadata["memory_reservation"]) // 2),
    }
    tiers = _rightsizer_tiers(resource, profile, target_config, monthly_savings)
    return {
        "inventory_id": resource["inventory_id"],
        "check_id": check_id,
        "finding_type": profile["finding_type"],
        "title": profile["name"].replace("_", " ").title(),
        "description": (
            f"ECS {resource['resource_name']} has low observed utilization for "
            f"its current CPU and memory reservation."
        ),
        "severity": profile["severity"],
        "confidence_score": 0.86,
        "risk_score": 0.22,
        "recommended_action": "review_ecs_reservations",
        "recommended_actions": ["review_ecs_reservations"],
        "available_actions": [
            {
                "action_key": "review_ecs_reservations",
                "label": "Review ECS reservations",
                "description": "Tune task CPU, memory, desired count, or idle services.",
                "estimated_monthly_savings": monthly_savings,
                "estimated_annual_savings": round(monthly_savings * 12, 2),
                "is_recommended": True,
                "parameters": {
                    "resource_id": resource["resource_id"],
                    "resource_type": resource["resource_type"],
                },
            }
        ],
        "potential_savings_monthly": monthly_savings,
        "potential_savings_yearly": round(monthly_savings * 12, 2),
        "evidence": {
            "avg_cpu_utilization": metadata["avg_cpu_utilization"],
            "avg_memory_utilization": metadata["avg_memory_utilization"],
            "desired_count": metadata["desired_count"],
            "running_count": metadata["running_count"],
            "rightsizer_tiers": tiers,
        },
        "current_config": metadata,
        "target_config": target_config,
        "metadata": {
            "status": "actionable",
            "usage_profile": profile["name"],
            "resource_state": resource["state"],
            "monthly_cost_estimate": metadata["monthly_cost_estimate"],
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
    resources: List[Dict[str, Any]] = []
    maxops_rows: List[Dict[str, Any]] = []
    profiles = config["profiles"]

    for inventory_id in range(1, count + 1):
        profile = profiles[(inventory_id - 1) % len(profiles)]
        resource = _build_resource(
            inventory_id=inventory_id,
            config=config,
            profile=profile,
            rng=rng,
            generated_at=generated_at,
        )
        resources.append(resource)
        maxops_rows.append(_build_maxops_row(resource, profile))

    return GeneratedArtifacts(
        inventory={
            "generated_at": generated_at.isoformat(),
            "account_id": config["generator"]["account_id"],
            "inventory_count": len(resources),
            "resources": resources,
        },
        maxops=maxops_rows,
    )


def write_artifacts(artifacts: GeneratedArtifacts, output_dir: Path) -> None:
    dump_json(output_dir / "inventory.json", artifacts.inventory)
    dump_json(output_dir / "ecs_maxops.json", artifacts.maxops)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate deterministic synthetic ECS DB import data.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to ECS synthetic config JSON.")
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
    print(f"Wrote ECS synthetic artifacts to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
