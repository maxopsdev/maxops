"""Comprehensive demo data seeding for MaxOps.

Builds demo data for every registered check with historical runs and
pricing-based savings. Random savings are intentionally not used.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import random
from typing import Any, Dict, Tuple

from app.database import SessionLocal, engine, init_db
from app.models.optimization import EnrichedCandidate, Finding, Recommendation, ResourceSnapshot
from app.models.policy import Policy, PolicyCostSavings, PolicyExecution, PolicyExecutionResult
from app.models.settings import OnboardingCheckResult, OnboardingExecution, UserSettings
from app.checks.registry import check_registry
from app.pricing.base import PricingContext
from app.pricing.registry import pricing_registry
from app.services.aws_pricing_cache import AwsPricingCacheService
from app.checks.base import estimate_ec2_monthly_cost, estimate_rds_monthly_cost

# Ensure checks and pricing handlers are registered.
import app.checks  # noqa: F401
import app.pricing  # noqa: F401


ACCOUNT_ID = "123456789012"
REGIONS = ["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1"]
HISTORY_DAYS = 100
RUNS_PER_CHECK = 4
RNG_SEED = 42


def clear_all_tables(db) -> None:
    """Clear seed-related tables in FK-safe order."""
    for model in [
        Recommendation,
        EnrichedCandidate,
        Finding,
        ResourceSnapshot,
        PolicyExecutionResult,
        PolicyCostSavings,
        PolicyExecution,
        Policy,
        OnboardingCheckResult,
        OnboardingExecution,
        UserSettings,
    ]:
        db.query(model).delete()
    db.commit()


def get_all_checks():
    checks = check_registry.list_checks()
    if not checks:
        raise RuntimeError("No checks were registered. Ensure app.checks is imported before seeding.")
    return checks


def generate_resource_id(resource_type: str, region: str, index: int) -> str:
    prefixes = {
        "ec2": "i-",
        "rds": "db-",
        "ebs": "vol-",
        "snapshot": "snap-",
        "lambda": "arn:aws:lambda:",
        "efs": "fs-",
        "s3": "arn:aws:s3:::",
        "vpc": "vpc-",
        "dynamodb": "arn:aws:dynamodb:",
        "cloudwatch_alarm": "arn:aws:cloudwatch:",
        "cloudwatch_log_group": "arn:aws:logs:",
        "aurora_cluster": "arn:aws:rds:",
        "opensearch_domain": "arn:aws:es:",
        "asg": "arn:aws:autoscaling:",
        "kinesis_stream": "arn:aws:kinesis:",
        "firehose_delivery_stream": "arn:aws:firehose:",
        "athena_workgroup": "arn:aws:athena:",
        "glue_table": "arn:aws:glue:",
        "redshift_cluster": "arn:aws:redshift:",
        "emr_cluster": "j-",
        "elasticache": "arn:aws:elasticache:",
        "glue_job": "arn:aws:glue:",
        "ecs": "arn:aws:ecs:",
    }
    base = prefixes.get(resource_type, f"{resource_type}-")
    compact_region = region.replace("-", "")
    suffix = f"{compact_region}{index:04d}"
    arn_types = {
        "lambda",
        "s3",
        "dynamodb",
        "cloudwatch_alarm",
        "cloudwatch_log_group",
        "aurora_cluster",
        "opensearch_domain",
        "asg",
        "kinesis_stream",
        "firehose_delivery_stream",
        "athena_workgroup",
        "glue_table",
        "redshift_cluster",
        "elasticache",
        "glue_job",
        "ecs",
    }
    if resource_type in arn_types:
        return f"{base}{region}:{ACCOUNT_ID}:demo-resource-{index}"
    return f"{base}{suffix}"


def build_run_timestamps(now: datetime, runs: int = RUNS_PER_CHECK):
    start = now - timedelta(days=HISTORY_DAYS)
    if runs <= 1:
        return [start]
    total_seconds = (now - start).total_seconds()
    points = []
    for idx in range(runs):
        frac = idx / (runs - 1)
        ts = start + timedelta(seconds=total_seconds * frac)
        points.append(ts.replace(hour=10 + (idx % 7), minute=(idx * 13) % 60, second=0, microsecond=0))
    return points


def _resource_name(resource_type: str, region: str, check_id: str, index: int) -> str:
    aliases = {
        "ec2": "instance",
        "rds": "database",
        "ebs": "volume",
        "lambda": "function",
        "efs": "filesystem",
        "aurora_cluster": "cluster",
        "opensearch_domain": "domain",
        "asg": "autoscaling-group",
        "redshift_cluster": "cluster",
        "emr_cluster": "cluster",
        "elasticache": "cache",
        "dynamodb": "table",
        "kinesis_stream": "stream",
        "glue_job": "job",
        "ecs": "service",
        "s3": "bucket",
        "vpc": "vpc",
        "cloudwatch_alarm": "alarm",
        "cloudwatch_log_group": "log-group",
        "snapshot": "snapshot",
        "athena_workgroup": "workgroup",
        "firehose_delivery_stream": "delivery-stream",
        "glue_table": "table",
    }
    return f"demo-{aliases.get(resource_type, resource_type)}-{region}-{check_id}-{index}"


def _build_resource_metadata(resource_type: str, check_id: str, index: int) -> Dict[str, Any]:
    instance_types = ["m6i.large", "m6i.xlarge", "c6i.large", "m5.large"]
    db_classes = ["db.m6g.large", "db.m6g.xlarge", "db.m5.large"]
    engines = ["postgres", "mysql"]
    cache_nodes = ["cache.m6g.large", "cache.m5.large"]
    size_gb = 100 + (index % 6) * 50

    base = {
        "check_id": check_id,
        "generated_by": "seed_comprehensive_demo",
    }
    if resource_type == "ec2":
        base.update({"InstanceType": instance_types[index % len(instance_types)], "instance_type": instance_types[index % len(instance_types)]})
    elif resource_type == "asg":
        base.update(
            {
                "InstanceType": instance_types[index % len(instance_types)],
                "desired_capacity": 2 + (index % 3),
                "warm_pool_size": index % 2,
            }
        )
    elif resource_type in {"rds", "aurora_cluster"}:
        base.update(
            {
                "DBInstanceClass": db_classes[index % len(db_classes)],
                "instance_class": db_classes[index % len(db_classes)],
                "Engine": engines[index % len(engines)],
                "engine": engines[index % len(engines)],
            }
        )
    elif resource_type == "elasticache":
        base.update({"CacheNodeType": cache_nodes[index % len(cache_nodes)], "Engine": "redis"})
    elif resource_type == "ebs":
        base.update({"Size": size_gb, "size": size_gb, "VolumeType": "gp3", "volume_type": "gp3"})
    elif resource_type == "snapshot":
        base.update({"VolumeSize": size_gb, "size_gb": size_gb})
    elif resource_type == "efs":
        base.update({"size_gb": size_gb, "ThroughputMode": "bursting"})
    elif resource_type == "ecs":
        base.update({"launchType": "FARGATE", "taskCpu": 1024, "taskMemory": 2048, "desiredCount": 2})
    else:
        base.update({"units": 1 + (index % 3)})
    return base


def _decorate_resource_metadata(
    metadata: Dict[str, Any],
    *,
    resource_id: str,
    resource_name: str,
    resource_type: str,
    region: str,
    check_id: str,
    index: int,
    now: datetime,
) -> Dict[str, Any]:
    """Add common, rich metadata fields for every seeded resource."""
    enriched = dict(metadata)
    created_at = (now - timedelta(days=30 + (index % 120))).isoformat()
    last_seen_at = (now - timedelta(hours=1 + (index % 24))).isoformat()
    tags = {
        "Environment": "demo",
        "Owner": "finops-team",
        "CostCenter": f"CC-{100 + (index % 20)}",
        "Application": f"maxops-{resource_type}",
    }
    utilization = {
        "cpu_avg_percent": round(4.5 + (index % 7) * 1.3, 2),
        "cpu_p95_percent": round(8.0 + (index % 8) * 1.8, 2),
        "memory_avg_percent": round(18.0 + (index % 9) * 3.1, 2),
        "network_in_mbps": round(1.0 + (index % 5) * 0.9, 2),
        "network_out_mbps": round(0.8 + (index % 4) * 0.7, 2),
        "io_avg": round(40 + (index % 12) * 9, 2),
    }
    lifecycle = {
        "created_at": created_at,
        "last_seen_at": last_seen_at,
        "state": "running" if resource_type in {"ec2", "rds", "lambda", "ecs"} else "available",
        "age_days": 30 + (index % 120),
    }
    ownership = {
        "team": "FinOps",
        "business_unit": "Platform",
        "owner_email": "finops@example.com",
        "managed_by": "terraform",
    }

    enriched["resource_profile"] = {
        "resource_id": resource_id,
        "resource_name": resource_name,
        "resource_type": resource_type,
        "cloud_provider": "aws",
        "account_id": ACCOUNT_ID,
        "region": region,
        "check_id": check_id,
    }
    enriched["tags"] = tags
    enriched["utilization"] = utilization
    enriched["lifecycle"] = lifecycle
    enriched["ownership"] = ownership
    enriched["compliance"] = {
        "environment": "non-production",
        "contains_pii": False,
        "is_customer_facing": False,
    }
    return enriched


def _flatten_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten nested metadata for UI surfaces that only read scalar fields."""
    flattened: Dict[str, Any] = {}
    for key, value in metadata.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            flattened[key] = value
            continue
        if isinstance(value, dict):
            for nested_key, nested_value in value.items():
                if isinstance(nested_value, (str, int, float, bool)) or nested_value is None:
                    flattened[f"{key}_{nested_key}"] = nested_value
    return flattened


def _get_aws_monthly_cost(
    pricing_service: AwsPricingCacheService,
    db,
    resource_type: str,
    region: str,
    metadata: Dict[str, Any],
    cache: Dict[str, Tuple[float, Dict[str, Any] | None]],
) -> Tuple[float, Dict[str, Any] | None]:
    """Get monthly cost from AWS pricing cache service where supported."""
    payload = None
    multiplier = 1.0
    conversion = "hourly"

    if resource_type == "ec2":
        payload = {"resource_type": "ec2", "resource_id": "seed", "region": region, "metadata": {"InstanceType": metadata.get("InstanceType")}}
    elif resource_type == "asg":
        payload = {"resource_type": "ec2", "resource_id": "seed", "region": region, "metadata": {"InstanceType": metadata.get("InstanceType")}}
        multiplier = max(1, int(metadata.get("desired_capacity", 2)) + int(metadata.get("warm_pool_size", 0)))
    elif resource_type in {"rds", "aurora_cluster"}:
        payload = {
            "resource_type": "rds",
            "resource_id": "seed",
            "region": region,
            "metadata": {"DBInstanceClass": metadata.get("DBInstanceClass"), "Engine": metadata.get("Engine", "postgres")},
        }
    elif resource_type == "elasticache":
        payload = {
            "resource_type": "elasticache",
            "resource_id": "seed",
            "region": region,
            "metadata": {"CacheNodeType": metadata.get("CacheNodeType"), "Engine": metadata.get("Engine", "redis")},
        }
    elif resource_type == "ebs":
        payload = {"resource_type": "ebs", "resource_id": "seed", "region": region, "metadata": {"VolumeType": metadata.get("VolumeType", "gp3")}}
        multiplier = float(metadata.get("Size", 100))
        conversion = "size"
    elif resource_type == "snapshot":
        payload = {"resource_type": "snapshot", "resource_id": "seed", "region": region, "metadata": {}}
        multiplier = float(metadata.get("VolumeSize", metadata.get("size_gb", 100)))
        conversion = "size"
    elif resource_type == "efs":
        payload = {"resource_type": "efs", "resource_id": "seed", "region": region, "metadata": {"ThroughputMode": metadata.get("ThroughputMode", "bursting")}}
        multiplier = float(metadata.get("size_gb", 100))
        conversion = "size"

    if not payload:
        return 0.0, None

    cache_key = f"{payload['resource_type']}|{region}|{str(payload.get('metadata', {}))}"
    if cache_key in cache:
        return cache[cache_key]

    price = pricing_service.get_price_for_resource(db, payload)
    if not price or price.get("price_per_unit") is None:
        result = (0.0, price)
        cache[cache_key] = result
        return result

    per_unit = float(price["price_per_unit"])
    if conversion == "hourly":
        monthly = per_unit * 730 * multiplier
    else:
        monthly = per_unit * multiplier
    result = (max(0.0, monthly), price)
    cache[cache_key] = result
    return result


def _calculate_monthly_savings(
    check_id: str,
    resource_type: str,
    region: str,
    metadata: Dict[str, Any],
    db,
    pricing_service: AwsPricingCacheService,
    aws_lookup_cache: Dict[str, Tuple[float, Dict[str, Any] | None]],
) -> Tuple[float, float, Dict[str, Any] | None]:
    """Calculate savings from pricing handlers first, then AWS pricing lookup."""
    resource = {
        "resource_id": "seed",
        "resource_type": resource_type,
        "region": region,
        "metadata": dict(metadata),
    }

    pricing_meta = pricing_registry.get_pricing(check_id)
    if pricing_meta:
        params = pricing_meta.parameters or {}
        context = PricingContext(
            check_id=check_id,
            resources=[resource],
            db=db,
            savings_ratio=params.get("savings_ratio"),
            full_savings=params.get("full_savings"),
            metadata={"note": params.get("note")} if params.get("note") else None,
        )
        try:
            pricing_registry.apply_pricing(context)
            md = resource.get("metadata", {})
            savings = float(md.get("potential_savings_monthly") or 0.0)
            estimated = float(md.get("estimated_monthly_cost") or 0.0)
            pricing = resource.get("pricing") or md.get("pricing")
            if savings > 0:
                return savings, estimated, pricing
        except Exception:
            pass

    estimated_monthly, pricing = _get_aws_monthly_cost(
        pricing_service,
        db,
        resource_type,
        region,
        metadata,
        aws_lookup_cache,
    )
    if estimated_monthly > 0:
        default_ratio = {
            "ec2": 0.7,
            "asg": 0.3,
            "rds": 0.3,
            "aurora_cluster": 0.3,
            "elasticache": 0.25,
            "ebs": 0.5,
            "snapshot": 1.0,
            "efs": 0.5,
        }.get(resource_type, 0.2)
        return estimated_monthly * default_ratio, estimated_monthly, pricing

    # Deterministic fallback (never random).
    if resource_type in {"ec2", "asg"}:
        base = estimate_ec2_monthly_cost(metadata.get("InstanceType", "m6i.large"))
        if resource_type == "asg":
            base *= max(1, int(metadata.get("desired_capacity", 2)) + int(metadata.get("warm_pool_size", 0)))
        return base * 0.3, base, {"source": "estimate"}
    if resource_type in {"rds", "aurora_cluster"}:
        base = estimate_rds_monthly_cost(metadata.get("DBInstanceClass", "db.m6g.large"))
        return base * 0.25, base, {"source": "estimate"}
    if resource_type == "ebs":
        base = float(metadata.get("Size", 100)) * 0.08
        return base * 0.5, base, {"source": "estimate"}
    if resource_type == "snapshot":
        base = float(metadata.get("VolumeSize", 100)) * 0.05
        return base, base, {"source": "estimate"}
    if resource_type == "efs":
        base = float(metadata.get("size_gb", 100)) * 0.30
        return base * 0.5, base, {"source": "estimate"}

    base_small = {"s3": 45.0, "cloudwatch_log_group": 35.0, "dynamodb": 120.0, "lambda": 80.0}.get(resource_type, 25.0)
    return base_small * 0.2, base_small, {"source": "estimate"}


def seed_comprehensive_demo_data(force: bool = True):
    if force:
        db_url = engine.url
        if db_url.drivername == "sqlite":
            sqlite_path = Path(db_url.database)
            if sqlite_path.exists():
                try:
                    sqlite_path.unlink(missing_ok=True)
                except PermissionError:
                    pass

    init_db()

    db = SessionLocal()
    rng = random.Random(RNG_SEED)
    pricing_service = AwsPricingCacheService()
    aws_lookup_cache: Dict[str, Tuple[float, Dict[str, Any] | None]] = {}
    try:
        clear_all_tables(db)
        checks = get_all_checks()
        now = datetime.now(timezone.utc)

        user_settings = UserSettings(
            environment="demo",
            account=ACCOUNT_ID,
            region=REGIONS[0],
            accounts=[ACCOUNT_ID],
            regions=REGIONS,
            idle_days=7,
            a_days=14,
            b_days=30,
            onboarding_completed=True,
        )
        db.add(user_settings)
        db.flush()

        totals = {
            "checks_processed": len(checks),
            "policies_created": 0,
            "resources_created": 0,
            "executions_created": 0,
            "onboarding_executions_created": 0,
            "onboarding_results_created": 0,
            "recommendations_created": 0,
            "cost_savings_created": 0,
        }

        for check_index, check in enumerate(checks):
            policy = Policy(
                policy_code=f"D{check_index + 1:05d}",
                name=f"Demo: {check.name}",
                description=f"Demo policy for {check.description}",
                check_id=check.check_id,
                parameters_json=(check.parameters or {}).copy(),
                policy_yaml="",
                resource_type=check.resource_type,
                status="active",
            )
            db.add(policy)
            db.flush()
            totals["policies_created"] += 1

            num_resources = 2 + (check_index % 3)
            snapshots = []
            pricing_by_resource_id: Dict[str, Dict[str, Any]] = {}
            for res_idx in range(num_resources):
                region = REGIONS[(check_index + res_idx) % len(REGIONS)]
                resource_id = generate_resource_id(check.resource_type, region, (check_index * 10) + res_idx + 1)
                resource_name = _resource_name(check.resource_type, region, check.check_id, res_idx + 1)
                resource_metadata = _build_resource_metadata(check.resource_type, check.check_id, check_index + res_idx)
                monthly_savings, estimated_monthly, pricing = _calculate_monthly_savings(
                    check_id=check.check_id,
                    resource_type=check.resource_type,
                    region=region,
                    metadata=resource_metadata,
                    db=db,
                    pricing_service=pricing_service,
                    aws_lookup_cache=aws_lookup_cache,
                )
                monthly_savings = round(max(0.0, monthly_savings), 2)
                estimated_monthly = round(max(monthly_savings, estimated_monthly), 2)

                resource_metadata["estimated_monthly_cost"] = estimated_monthly
                resource_metadata["potential_savings_monthly"] = monthly_savings
                resource_metadata["potential_savings_yearly"] = round(monthly_savings * 12, 2)
                if pricing:
                    resource_metadata["pricing"] = pricing
                resource_metadata = _decorate_resource_metadata(
                    resource_metadata,
                    resource_id=resource_id,
                    resource_name=resource_name,
                    resource_type=check.resource_type,
                    region=region,
                    check_id=check.check_id,
                    index=check_index + res_idx,
                    now=now,
                )

                snapshot = ResourceSnapshot(
                    resource_id=resource_id,
                    resource_type=check.resource_type,
                    resource_name=resource_name,
                    cloud_provider="aws",
                    account_id=ACCOUNT_ID,
                    region=region,
                    state="running" if check.resource_type in {"ec2", "rds", "lambda", "ecs"} else None,
                    instance_type=resource_metadata.get("InstanceType"),
                    size_gb=resource_metadata.get("Size") or resource_metadata.get("size_gb") or resource_metadata.get("VolumeSize"),
                    tags_json={"env": "demo", "team": "finops", "check_id": check.check_id},
                    metadata_json=resource_metadata,
                    snapshot_timestamp=now - timedelta(hours=(res_idx + 1)),
                    scan_id=f"demo-scan-{check.check_id}-{res_idx + 1}",
                )
                db.add(snapshot)
                db.flush()

                snapshots.append(snapshot)
                totals["resources_created"] += 1
                pricing_by_resource_id[snapshot.resource_id] = {
                    "monthly_savings": monthly_savings,
                    "estimated_monthly": estimated_monthly,
                    "pricing": pricing,
                }

                finding = Finding(
                    snapshot_id=snapshot.id,
                    finding_type="optimization",
                    severity=["low", "medium", "high"][res_idx % 3],
                    title=f"{check.name} finding",
                    description=f"Resource matched check '{check.check_id}'",
                    evidence_json={"check_id": check.check_id, "signal_strength": 0.8 + (res_idx * 0.03)},
                    observation_days=30,
                    observation_start=now - timedelta(days=30),
                    observation_end=now,
                    is_production=False,
                    has_owner=True,
                    has_redundancy=False,
                    status="open",
                )
                db.add(finding)
                db.flush()

                candidate = EnrichedCandidate(
                    finding_id=finding.id,
                    dependency_graph_json={"depends_on": []},
                    business_rules_json={"change_window": "weekend"},
                    compliance_tags_json={"pci": False, "sox": False},
                    risk_score=round(rng.uniform(8, 45), 2),
                    risk_factors_json=["demo-seeded"],
                    can_modify=True,
                    requires_approval=False,
                    status="ready",
                )
                db.add(candidate)
                db.flush()

                recommendation = Recommendation(
                    candidate_id=candidate.id,
                    recommendation_type="optimize",
                    recommendation_engine="rules-based",
                    current_config_json={"resource_id": snapshot.resource_id, "region": snapshot.region},
                    target_config_json={"optimized": True, "resource_id": snapshot.resource_id},
                    change_plan_json={"steps": ["assess", "execute", "verify"]},
                    estimated_monthly_savings=monthly_savings,
                    estimated_annual_savings=round(monthly_savings * 12, 2),
                    cost_breakdown_json={"estimated_monthly_cost": estimated_monthly, "source": (pricing or {}).get("source", "estimate")},
                    confidence_score=round(rng.uniform(75, 97), 2),
                    risk_score=round(rng.uniform(10, 35), 2),
                    risk_factors_json=["low-business-impact"],
                    assumptions_json=["steady-state workload"],
                    evidence_window_start=now - timedelta(days=30),
                    evidence_window_end=now,
                    status="draft",
                )
                db.add(recommendation)
                totals["recommendations_created"] += 1

            run_times = build_run_timestamps(now, RUNS_PER_CHECK)
            for run_idx, run_time in enumerate(run_times):
                resources_found = min(len(snapshots), 1 + ((check_index + run_idx) % len(snapshots)))
                selected = snapshots[:resources_found]
                trend_factor = max(0.72, 1 - (run_idx * 0.08))

                execution = PolicyExecution(
                    policy_id=policy.id,
                    execution_type="dry-run",
                    status="completed",
                    resources_found=resources_found,
                    results_json={
                        "total_resources_checked": len(snapshots),
                        "matching_resources": resources_found,
                        "execution_type": "dry-run",
                    },
                    started_at=run_time - timedelta(minutes=4),
                    completed_at=run_time,
                )
                db.add(execution)
                db.flush()
                totals["executions_created"] += 1

                stored_resources = []
                execution_total_monthly = 0.0
                for snapshot in selected:
                    base = pricing_by_resource_id[snapshot.resource_id]
                    monthly = round(base["monthly_savings"] * trend_factor, 2)
                    yearly = round(monthly * 12, 2)
                    execution_total_monthly += monthly

                    result = PolicyExecutionResult(
                        execution_id=execution.id,
                        resource_id=snapshot.resource_id,
                        resource_type=snapshot.resource_type,
                        resource_name=snapshot.resource_name,
                        region=snapshot.region,
                        account_id=ACCOUNT_ID,
                        reason=f"Matched {check.name}",
                        metadata_json={
                            "check_id": check.check_id,
                            "estimated_monthly_cost": base["estimated_monthly"],
                            "potential_savings_monthly": monthly,
                            "potential_savings_yearly": yearly,
                            "pricing": base["pricing"],
                            "resource_metadata": snapshot.metadata_json or {},
                            "resource_metadata_flat": _flatten_metadata(snapshot.metadata_json or {}),
                            "run_index": run_idx + 1,
                        },
                    )
                    db.add(result)

                    flattened_resource_metadata = _flatten_metadata(snapshot.metadata_json or {})
                    stored_resources.append(
                        {
                            "resource_id": snapshot.resource_id,
                            "resource_type": snapshot.resource_type,
                            "resource_name": snapshot.resource_name,
                            "region": snapshot.region,
                            "account_id": ACCOUNT_ID,
                            "tags": snapshot.tags_json or {},
                            "metadata": {
                                "check_id": check.check_id,
                                "check_reason": f"Matched {check.name}",
                                "estimated_monthly_cost": base["estimated_monthly"],
                                "potential_savings_monthly": monthly,
                                "potential_savings_yearly": yearly,
                                "pricing": base["pricing"],
                                "resource_metadata": snapshot.metadata_json or {},
                                **flattened_resource_metadata,
                                "run_index": run_idx + 1,
                            },
                        }
                    )

                cost_row = PolicyCostSavings(
                    policy_id=policy.id,
                    execution_id=execution.id,
                    date=run_time,
                    cost_saved=round(execution_total_monthly, 2),
                    resources_fixed=resources_found,
                    notes=f"Demo run {run_idx + 1} for {check.check_id}",
                )
                db.add(cost_row)
                totals["cost_savings_created"] += 1

                onboarding_execution = OnboardingExecution(
                    settings_id=user_settings.id,
                    status="completed",
                    total_checks=1,
                    completed_checks=1,
                    failed_checks=0,
                    results_json={"check_id": check.check_id, "resources_found": resources_found},
                    started_at=run_time - timedelta(minutes=6),
                    completed_at=run_time,
                )
                db.add(onboarding_execution)
                db.flush()
                totals["onboarding_executions_created"] += 1

                onboarding_result = OnboardingCheckResult(
                    execution_id=onboarding_execution.id,
                    check_id=check.check_id,
                    name=check.name,
                    description=check.description,
                    resource_type=check.resource_type,
                    status="completed",
                    resources_found=resources_found,
                    error=None,
                    metadata_json={
                        "potential_savings_yearly": round(execution_total_monthly * 12, 6),
                        "resources": stored_resources,
                    },
                )
                db.add(onboarding_result)
                totals["onboarding_results_created"] += 1

        db.commit()
        return {
            "account_id": ACCOUNT_ID,
            "regions": REGIONS,
            "historical_period_days": HISTORY_DAYS,
            "runs_per_check": RUNS_PER_CHECK,
            **totals,
        }
    finally:
        db.close()


if __name__ == "__main__":
    summary = seed_comprehensive_demo_data(force=True)
    print("Comprehensive demo data seeded successfully:")
    for key, value in summary.items():
        print(f"  {key}: {value}")
