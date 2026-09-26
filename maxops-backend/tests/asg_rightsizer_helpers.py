from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.inventory import AsgInventory
from app.services.asg_rightsizer import ASGRightsizer
from app.services.ec2_instance_catalog import EC2InstanceCatalog


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PRICING_DATABASE = BACKEND_ROOT / "maxops_pricing.db"


def catalog() -> EC2InstanceCatalog:
    assert PRICING_DATABASE.is_file(), (
        "maxops_pricing.db is missing; run ./.venv/bin/python "
        "staging_pricing/unpack_pricing_db.py"
    )
    result = EC2InstanceCatalog(PRICING_DATABASE)
    assert result.get("us-east-1", "m6i.large", "linux") is not None
    return result


def requirement(value: int, binding: str = "memory") -> dict[str, Any]:
    record = {
        "timestamp": "2026-07-01T00:00:00+00:00",
        "required": value,
        "binding_dimension": binding,
        "cpu_used_instances": max(0.1, value * 0.55),
        "memory_used_instances": max(0.1, value * 0.60),
    }
    return {
        "p50": value,
        "p99": value,
        "maximum": value,
        "sample_count": 2016,
        "p50_record": record,
        "p99_record": record,
    }


def metadata() -> dict[str, Any]:
    tiers = {
        "conservative": (4, 9),
        "balanced": (3, 7),
        "aggressive": (3, 6),
    }
    windows = {}
    for days in (14, 30, 60):
        windows[f"{days}d"] = {
            "lookback_days": days,
            "period_seconds": 300,
            "normalized": {
                "required_capacity": {
                    tier: {
                        **requirement(desired),
                        "p50": minimum,
                        "p50_record": {
                            **requirement(minimum)["p50_record"],
                            "required": minimum,
                        },
                    }
                    for tier, (minimum, desired) in tiers.items()
                }
            },
            "coverage": {
                "cpu_in_service_pairing_ratio": 1.0,
                "memory_in_service_pairing_ratio": 1.0,
            },
            "signals": {},
            "normalization_version": "asg-v1-exact",
        }
    return {
        "min_size": 6,
        "desired_capacity": 10,
        "max_size": 20,
        "instance_type": "m6i.large",
        "platform_normalized": "linux",
        "availability_zones": ["us-east-1a", "us-east-1b", "us-east-1c"],
        "instance_ids": [f"i-{index}" for index in range(10)],
        "in_service_instance_ids": [f"i-{index}" for index in range(10)],
        "effective_instance_types": ["m6i.large"],
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
        "telemetry_summary": {
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
                    "dimensions": [
                        {"Name": "AutoScalingGroupName", "Value": "asg-test"}
                    ],
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
        },
        "rightsizing_metrics": windows,
    }


def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def run(
    *,
    metadata_override: dict[str, Any] | None = None,
    current_min: int = 6,
    current_desired: int = 10,
    current_max: int = 20,
    current_instance_type: str = "m6i.large",
    min_monthly_savings: float = 0.0,
    service_kwargs: dict[str, Any] | None = None,
    catalog_override: Any | None = None,
) -> tuple[dict[str, Any], Any, AsgInventory]:
    db = session()
    payload = metadata()
    if metadata_override:
        for key, value in metadata_override.items():
            payload[key] = deepcopy(value)
    payload["min_size"] = current_min
    payload["desired_capacity"] = current_desired
    payload["max_size"] = current_max
    payload["instance_type"] = current_instance_type
    if not metadata_override or "effective_instance_types" not in metadata_override:
        payload["effective_instance_types"] = [current_instance_type]
    row = AsgInventory(
        inventory_id=1,
        resource_id="asg-test",
        resource_name="asg-test",
        resource_type="asg",
        account_id="123456789012",
        region="us-east-1",
        state="active",
        min_size=current_min,
        desired_capacity=current_desired,
        max_size=current_max,
        instance_type=current_instance_type,
        platform_normalized="linux",
        generated_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        metadata_json=payload,
    )
    db.add(row)
    db.commit()
    result = ASGRightsizer(
        db, catalog=catalog_override or catalog(), **(service_kwargs or {})
    ).get_recommendation(1, min_monthly_savings=min_monthly_savings)
    assert result is not None
    return result, db, row
