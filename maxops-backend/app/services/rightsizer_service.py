"""Read models for the rightsizer UI and API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.inventory import AsgInventory, Ec2Inventory, MaxOpsInventory, RdsInventory, S3Inventory
from app.services.asg_rightsizer import ASGRightsizer
from app.services.ec2_rightsizer import EC2Rightsizer, EC2ScopePolicy
from app.services.settings_service import get_effective_rightsizer_policy
from rightsizers.ec2.ec2_rightsizer.models import PerformanceWarningPolicy


SUPPORTED_RIGHTSIZER_RESOURCE_TYPES = (
    {
        "resource_type": "ec2",
        "label": "EC2",
        "description": "Instance family, size, and Graviton opportunities.",
        "detail_available": True,
    },
    {
        "resource_type": "asg",
        "label": "ASG",
        "description": "Auto Scaling Group capacity and savings opportunities.",
        "detail_available": True,
    },
    {
        "resource_type": "ecs",
        "label": "ECS",
        "description": "Service and task sizing inventory.",
        "detail_available": True,
    },
    {
        "resource_type": "rds",
        "label": "RDS",
        "description": "Database instance sizing inventory.",
        "detail_available": True,
    },
    {
        "resource_type": "s3",
        "label": "S3",
        "description": "Storage class and lifecycle sizing inventory.",
        "detail_available": False,
    },
)


def _rightsizer(db: Session) -> EC2Rightsizer:
    policy = get_effective_rightsizer_policy(db)
    return EC2Rightsizer(
        db,
        warning_policy=PerformanceWarningPolicy(
            network_medium_ratio=policy["network_medium_ratio"],
            network_high_ratio=policy["network_high_ratio"],
            ebs_medium_ratio=policy["ebs_medium_ratio"],
            ebs_high_ratio=policy["ebs_high_ratio"],
        ),
        scope_policy=EC2ScopePolicy(
            allow_unknown_instance_store_usage=policy[
                "allow_unknown_instance_store_usage"
            ]
        ),
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _classification_label(value: str | None) -> str:
    normalized = str(value or "").upper()
    if normalized == "ACTIONABLE":
        return "Actionable"
    if normalized == "CONDITIONAL":
        return "Conditional - review required"
    if normalized == "PREVIEW":
        return "Preview - metrics required"
    if normalized == "OPPORTUNITY":
        return "Opportunity - migration"
    if normalized == "DEFERRED":
        return "Deferred"
    if normalized == "INSUFFICIENT_DATA":
        return "Insufficient data"
    return "No recommendation"


def _select_recommendation(payload: dict[str, Any]) -> dict[str, Any] | None:
    recommendations = payload.get("recommendations") or []
    tiers = payload.get("tiers") or {}
    default_tier = tiers.get("default")
    if default_tier and isinstance(tiers.get(default_tier), dict):
        target = tiers[default_tier].get("target_instance_type")
        for recommendation in recommendations:
            if recommendation.get("target_instance_type") == target:
                return recommendation
    return recommendations[0] if recommendations else None


def _selected_classification(payload: dict[str, Any]) -> str:
    selected = _select_recommendation(payload)
    return str(
        (selected or {}).get("classification")
        or payload.get("classification")
        or "INSUFFICIENT_DATA"
    )


def _select_asg_recommendation(payload: dict[str, Any]) -> dict[str, Any] | None:
    tiers = payload.get("tiers") or {}
    default_tier = tiers.get("default")
    if default_tier and isinstance(tiers.get(default_tier), dict):
        return tiers[default_tier]
    for tier in ("balanced", "conservative", "aggressive"):
        if isinstance(tiers.get(tier), dict):
            return tiers[tier]
    recommendations = payload.get("recommendations") or []
    if recommendations:
        return recommendations[0]
    for preview in payload.get("savings_previews") or []:
        options = preview.get("options") if isinstance(preview, dict) else {}
        if not isinstance(options, dict):
            continue
        for tier in ("balanced", "conservative", "aggressive"):
            if isinstance(options.get(tier), dict):
                return options[tier]
    return None


def _asg_target_type(selected: dict[str, Any] | None) -> str | None:
    if not selected:
        return None
    target_min = selected.get("target_min_size")
    target_desired = selected.get("target_desired_capacity")
    target_max = selected.get("target_max_size")
    if target_min is None or target_desired is None or target_max is None:
        return None
    return f"{target_min}/{target_desired}/{target_max}"


def _selected_asg_classification(payload: dict[str, Any]) -> str:
    selected = _select_asg_recommendation(payload)
    if not selected and "CURRENT_CAPACITY_NOT_OVERPROVISIONED" in (
        payload.get("blocking_reasons") or []
    ):
        return "NO_RECOMMENDATION"
    return str(
        (selected or {}).get("classification")
        or payload.get("classification")
        or "INSUFFICIENT_DATA"
    )


def list_rightsizer_resource_types(db: Session) -> dict[str, Any]:
    counts = {
        "ec2": db.query(func.count(Ec2Inventory.id)).scalar() or 0,
        "asg": db.query(func.count(AsgInventory.id)).scalar() or 0,
        "rds": db.query(func.count(RdsInventory.id)).scalar() or 0,
        "s3": db.query(func.count(S3Inventory.id)).scalar() or 0,
        "ecs": db.query(func.count(MaxOpsInventory.id))
        .filter(MaxOpsInventory.resource_type.in_(("ecs", "ecs_service", "ecs_cluster")))
        .scalar()
        or 0,
    }
    return {
        "resource_types": [
            {**item, "resource_count": int(counts[item["resource_type"]])}
            for item in SUPPORTED_RIGHTSIZER_RESOURCE_TYPES
        ],
        "policy": get_effective_rightsizer_policy(db),
    }


def _filter_options(resources: list[dict[str, Any]]) -> dict[str, list[str]]:
    keys = ("region", "state", "status", "current_type", "target_type")
    return {
        key: sorted(
            {
                str(resource.get(key))
                for resource in resources
                if resource.get(key) not in (None, "")
            }
        )
        for key in keys
    }


def _matches_filter(value: Any, expected: str | None) -> bool:
    if expected in (None, "", "all"):
        return True
    return str(value or "").lower() == expected.lower()


def _filter_resources(
    resources: list[dict[str, Any]],
    *,
    q: str | None = None,
    region: str | None = None,
    state: str | None = None,
    status: str | None = None,
    current_type: str | None = None,
    target_type: str | None = None,
) -> list[dict[str, Any]]:
    term = (q or "").strip().lower()
    filtered = []
    for resource in resources:
        if term:
            search_values = (
                resource.get("resource_id"),
                resource.get("resource_name"),
                resource.get("current_type"),
                resource.get("target_type"),
                resource.get("status"),
                resource.get("classification"),
                resource.get("region"),
                resource.get("state"),
            )
            if not any(term in str(value or "").lower() for value in search_values):
                continue
        if not _matches_filter(resource.get("region"), region):
            continue
        if not _matches_filter(resource.get("state"), state):
            continue
        if not _matches_filter(resource.get("status"), status):
            continue
        if not _matches_filter(resource.get("current_type"), current_type):
            continue
        if not _matches_filter(resource.get("target_type"), target_type):
            continue
        filtered.append(resource)
    return filtered


def _ec2_summary_row(payload: dict[str, Any]) -> dict[str, Any]:
    selected = _select_recommendation(payload)
    classification = _selected_classification(payload)
    deferred_reasons = payload.get("deferred_reason_codes") or []
    blocking_reasons = payload.get("blocking_reasons") or []
    reason_codes = (
        (selected or {}).get("reason_codes")
        or deferred_reasons
        or blocking_reasons
        or []
    )
    return {
        "inventory_id": payload.get("inventory_id"),
        "resource_id": payload.get("resource_id"),
        "resource_name": payload.get("resource_name"),
        "resource_type": "ec2",
        "account_id": payload.get("account_id"),
        "region": payload.get("region"),
        "state": payload.get("state"),
        "current_type": payload.get("current_instance_type"),
        "target_type": (selected or {}).get("target_instance_type"),
        "status": _classification_label(classification),
        "classification": classification,
        "monthly_savings": round(float((selected or {}).get("monthly_savings") or 0), 2),
        "yearly_savings": round(float((selected or {}).get("yearly_savings") or 0), 2),
        "risk_overall": ((selected or {}).get("risk_assessment") or {}).get("overall"),
        "reason_codes": reason_codes,
        "detail_available": True,
    }


def _asg_summary_row(payload: dict[str, Any]) -> dict[str, Any]:
    selected = _select_asg_recommendation(payload)
    classification = _selected_asg_classification(payload)
    deferred_reasons = payload.get("deferred_reason_codes") or []
    blocking_reasons = payload.get("blocking_reasons") or []
    reason_codes = (
        (selected or {}).get("reason_codes")
        or deferred_reasons
        or blocking_reasons
        or []
    )
    current = payload.get("current_configuration") or {}
    return {
        "inventory_id": payload.get("inventory_id"),
        "resource_id": payload.get("resource_id"),
        "resource_name": payload.get("resource_name"),
        "resource_type": "asg",
        "account_id": payload.get("account_id"),
        "region": payload.get("region"),
        "state": payload.get("state"),
        "current_type": current.get("instance_type"),
        "target_type": _asg_target_type(selected),
        "status": _classification_label(classification),
        "classification": classification,
        "monthly_cost": round(float(payload.get("current_monthly_cost") or 0), 2),
        "monthly_savings": round(float((selected or {}).get("monthly_savings") or 0), 2),
        "yearly_savings": round(float((selected or {}).get("yearly_savings") or 0), 2),
        "risk_overall": ((selected or {}).get("risk_assessment") or {}).get("overall"),
        "reason_codes": reason_codes,
        "detail_available": True,
    }


def _risk_from_severity(severity: str | None) -> str | None:
    normalized = str(severity or "").upper()
    if normalized in {"CRITICAL", "HIGH"}:
        return "HIGH"
    if normalized == "MEDIUM":
        return "MEDIUM"
    if normalized == "LOW":
        return "LOW"
    return None


def _rds_target_from_finding(finding: MaxOpsInventory | None) -> str | None:
    if not finding:
        return None
    target = finding.target_config_json if isinstance(finding.target_config_json, dict) else {}
    return target.get("db_instance_class") or target.get("db_instance_status")


def _rds_summary_row(row: RdsInventory, finding: MaxOpsInventory | None) -> dict[str, Any]:
    classification = "ACTIONABLE" if finding and finding.check_id else "NO_RECOMMENDATION"
    reason_codes = [finding.check_id] if finding and finding.check_id else []
    return {
        "inventory_id": row.inventory_id,
        "resource_id": row.resource_id,
        "resource_name": row.resource_name,
        "resource_type": "rds",
        "account_id": row.account_id,
        "region": row.region,
        "state": row.state,
        "current_type": row.db_instance_class,
        "target_type": _rds_target_from_finding(finding),
        "status": _classification_label(classification),
        "classification": classification,
        "monthly_cost": round(float(row.monthly_cost_estimate or 0), 2),
        "monthly_savings": round(float((finding.potential_savings_monthly if finding else 0) or 0), 2),
        "yearly_savings": round(float((finding.potential_savings_yearly if finding else 0) or 0), 2),
        "risk_overall": _risk_from_severity(finding.severity if finding else None),
        "reason_codes": reason_codes,
        "detail_available": True,
    }


def _generic_inventory_row(row: Any, resource_type: str) -> dict[str, Any]:
    monthly_cost = getattr(row, "monthly_cost_estimate", None)
    if monthly_cost is None:
        monthly_cost = getattr(row, "estimated_monthly_cost", None)
    current_type = (
        getattr(row, "db_instance_class", None)
        or getattr(row, "resource_type", None)
        or resource_type
    )
    return {
        "inventory_id": row.inventory_id,
        "resource_id": row.resource_id,
        "resource_name": row.resource_name,
        "resource_type": resource_type,
        "account_id": row.account_id,
        "region": row.region,
        "state": getattr(row, "state", None),
        "current_type": current_type,
        "target_type": None,
        "status": "Inventory only",
        "classification": "NOT_SUPPORTED",
        "monthly_cost": round(float(monthly_cost or 0), 2),
        "monthly_savings": 0.0,
        "yearly_savings": 0.0,
        "risk_overall": None,
        "reason_codes": ["RIGHTSIZER_ENGINE_NOT_AVAILABLE"],
        "detail_available": False,
    }


def _ecs_rows(db: Session) -> list[dict[str, Any]]:
    rows = (
        db.query(MaxOpsInventory)
        .filter(MaxOpsInventory.resource_type.in_(("ecs", "ecs_service", "ecs_cluster")))
        .order_by(MaxOpsInventory.inventory_id.asc())
        .all()
    )
    resources = []
    for row in rows:
        metadata = row.metadata_json or {}
        current = row.current_config_json or {}
        target = row.target_config_json or {}
        classification = "ACTIONABLE" if row.check_id else "NO_RECOMMENDATION"
        current_type = row.resource_type
        if current.get("cpu_reservation") or current.get("memory_reservation"):
            current_type = f"{current.get('cpu_reservation', 'n/a')} CPU / {current.get('memory_reservation', 'n/a')} MiB"
        target_type = None
        if row.check_id and (target.get("cpu_reservation") or target.get("memory_reservation")):
            target_type = f"{target.get('cpu_reservation', 'n/a')} CPU / {target.get('memory_reservation', 'n/a')} MiB"
        resources.append(
            {
            "inventory_id": row.inventory_id,
            "resource_id": row.resource_id,
            "resource_name": row.resource_name,
            "resource_type": "ecs",
            "account_id": row.account_id,
            "region": row.region,
            "state": metadata.get("resource_state"),
            "current_type": current_type,
            "target_type": target_type,
            "status": _classification_label(classification),
            "classification": classification,
            "monthly_savings": round(float(row.potential_savings_monthly or 0), 2),
            "yearly_savings": round(float(row.potential_savings_yearly or 0), 2),
            "risk_overall": "MEDIUM" if row.check_id else "LOW",
            "reason_codes": [row.check_id] if row.check_id else [],
            "detail_available": True,
            }
        )
    return resources


def list_rightsizer_resources(
    resource_type: str,
    db: Session,
    *,
    q: str | None = None,
    region: str | None = None,
    state: str | None = None,
    status: str | None = None,
    current_type: str | None = None,
    target_type: str | None = None,
) -> dict[str, Any]:
    normalized = resource_type.lower()
    if normalized == "ec2":
        all_resources = [
            _ec2_summary_row(item)
            for item in _rightsizer(db).list_recommendations(state="")
        ]
    elif normalized == "asg":
        all_resources = [
            _asg_summary_row(item)
            for item in ASGRightsizer(db).list_recommendations(state="")
        ]
    elif normalized == "rds":
        all_resources = [
            _rds_summary_row(row, finding)
            for row, finding in (
                db.query(RdsInventory, MaxOpsInventory)
                .outerjoin(
                    MaxOpsInventory,
                    (MaxOpsInventory.resource_type == "rds")
                    & (MaxOpsInventory.inventory_id == RdsInventory.inventory_id),
                )
                .order_by(RdsInventory.inventory_id.asc())
                .all()
            )
        ]
    elif normalized == "s3":
        all_resources = [
            _generic_inventory_row(row, "s3")
            for row in db.query(S3Inventory).order_by(S3Inventory.inventory_id.asc())
        ]
    elif normalized == "ecs":
        all_resources = _ecs_rows(db)
    else:
        raise ValueError(f"Unsupported rightsizer resource type '{resource_type}'")
    resources = _filter_resources(
        all_resources,
        q=q,
        region=region,
        state=state,
        status=status,
        current_type=current_type,
        target_type=target_type,
    )

    return {
        "resource_type": normalized,
        "generated_at": _iso(datetime.utcnow()),
        "policy": get_effective_rightsizer_policy(db),
        "summary": {
            "total_resources": len(resources),
            "actionable_resources": sum(
                1
                for resource in resources
                if resource["classification"] in {"ACTIONABLE", "CONDITIONAL", "OPPORTUNITY"}
            ),
            "inventory_only_resources": sum(
                1 for resource in resources if resource["classification"] == "NOT_SUPPORTED"
            ),
            "monthly_savings": round(
                sum(float(resource.get("monthly_savings") or 0) for resource in resources),
                2,
            ),
            "yearly_savings": round(
                sum(float(resource.get("yearly_savings") or 0) for resource in resources),
                2,
            ),
        },
        "filters": _filter_options(all_resources),
        "resources": resources,
    }


def _metric_series(metric_history: dict[str, Any], key: str) -> list[dict[str, Any]]:
    series = metric_history.get(key) if isinstance(metric_history, dict) else {}
    series = series if isinstance(series, dict) else {}
    timestamps = series.get("timestamps") or []
    points: list[dict[str, Any]] = []
    for index, timestamp in enumerate(timestamps):
        point = {"timestamp": timestamp}
        for stat in ("average", "maximum", "p90", "p95", "p99"):
            values = series.get(stat) or []
            point[stat] = float(values[index]) if index < len(values) and values[index] is not None else None
        points.append(point)
    return points


def _projected_metric_chart(
    metric_history: dict[str, Any],
    metric_key: str,
    metric_name: str,
    selected: dict[str, Any] | None,
    projected_key: str,
    *,
    use_performance_fallback: bool = False,
) -> dict[str, Any]:
    points = _metric_series(metric_history, metric_key)
    observed_p99 = max(
        (float(point["p99"]) for point in points if point.get("p99") is not None),
        default=None,
    )
    projected = (selected or {}).get(projected_key)
    ratio = None
    if observed_p99 not in (None, 0) and projected is not None:
        ratio = float(projected) * 100.0 / float(observed_p99)
    elif use_performance_fallback and (selected or {}).get("performance_ratio"):
        performance_ratio = float((selected or {}).get("performance_ratio") or 0)
        ratio = 1.0 / performance_ratio if performance_ratio > 0 else None
    chart_points = []
    for point in points:
        chart_points.append(
            {
                **point,
                "maximum_on_target": (
                    round(point["maximum"] * ratio, 2)
                    if point.get("maximum") is not None and ratio is not None
                    else None
                ),
                "p99_on_target": (
                    round(point["p99"] * ratio, 2)
                    if point.get("p99") is not None and ratio is not None
                    else None
                ),
                "p95_on_target": (
                    round(point["p95"] * ratio, 2)
                    if point.get("p95") is not None and ratio is not None
                    else None
                ),
                "scale_ratio": ratio,
            }
        )
    return {
        "metric": metric_name,
        "unit": "Percent",
        "points": chart_points,
        "target_capacity_line": 100,
    }


def _scale_chart_points(row: Ec2Inventory, selected: dict[str, Any] | None) -> dict[str, Any]:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    metric_history = row.metric_history_json or metadata.get("metric_history") or {}
    return _projected_metric_chart(
        metric_history,
        "cpuutilization",
        "CPUUtilization",
        selected,
        "projected_cpu_util",
        use_performance_fallback=True,
    )


def _ec2_memory_chart_points(row: Ec2Inventory, selected: dict[str, Any] | None) -> dict[str, Any]:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    metric_history = row.metric_history_json or metadata.get("metric_history") or {}
    return _projected_metric_chart(
        metric_history,
        "memoryutilization",
        "MemoryUtilization",
        selected,
        "projected_memory_util",
    )


def _lookback_summary(row: Ec2Inventory) -> list[dict[str, Any]]:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    rightsizing = metadata.get("rightsizing_metrics")
    rightsizing = rightsizing if isinstance(rightsizing, dict) else {}
    summary = []
    for key in ("14d", "30d", "60d"):
        payload = rightsizing.get(key) if isinstance(rightsizing.get(key), dict) else {}
        normalized = payload.get("normalized") if isinstance(payload, dict) else {}
        cpu = normalized.get("cpu_percent") if isinstance(normalized, dict) else {}
        summary.append(
            {
                "window": key,
                "lookback_days": payload.get("lookback_days"),
                "maximum": cpu.get("maximum") if isinstance(cpu, dict) else None,
                "p99": cpu.get("p99") if isinstance(cpu, dict) else None,
                "p95": cpu.get("p95") if isinstance(cpu, dict) else None,
                "sample_count": cpu.get("sample_count") if isinstance(cpu, dict) else 0,
            }
        )
    return summary


def _utilization_bars(selected: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not selected:
        return []
    network = ((selected.get("network_evaluation") or {}).get("evidence") or {})
    storage = ((selected.get("storage_evaluation") or {}).get("evidence") or {})
    compute = selected.get("compute_evidence") or {}
    projected_cpu = selected.get("projected_cpu_util")
    return [
        {
            "id": "cpu",
            "label": "CPU %",
            "value_ratio": projected_cpu,
            "detail": {
                "p99": compute.get("observed_cpu_p99_percent"),
                "headroom": (
                    round(1 / projected_cpu, 2)
                    if projected_cpu is not None and projected_cpu > 0
                    else None
                ),
            },
        },
        {
            "id": "network_in",
            "label": "Network in",
            "value_ratio": network.get("network_in_utilization_ratio"),
            "detail": {
                "observed": network.get("observed_in_p99_mbps"),
                "capacity": network.get("baseline_mbps"),
                "capacity_kind": network.get("capacity_kind"),
            },
        },
        {
            "id": "network_out",
            "label": "Network out",
            "value_ratio": network.get("network_out_utilization_ratio"),
            "detail": {
                "observed": network.get("observed_out_p99_mbps"),
                "capacity": network.get("baseline_mbps"),
                "capacity_kind": network.get("capacity_kind"),
            },
        },
        {
            "id": "ebs_iops",
            "label": "EBS IOPS",
            "value_ratio": storage.get("ebs_iops_utilization_ratio"),
            "detail": {},
        },
        {
            "id": "ebs_throughput",
            "label": "EBS throughput",
            "value_ratio": storage.get("ebs_throughput_utilization_ratio"),
            "detail": {},
        },
    ]


def _asg_lookback_summary(row: AsgInventory) -> list[dict[str, Any]]:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    rightsizing = metadata.get("rightsizing_metrics")
    rightsizing = rightsizing if isinstance(rightsizing, dict) else {}
    summary = []
    for key in ("14d", "30d", "60d"):
        payload = rightsizing.get(key) if isinstance(rightsizing.get(key), dict) else {}
        normalized = payload.get("normalized") if isinstance(payload, dict) else {}
        cpu = normalized.get("cpu_percent") if isinstance(normalized, dict) else {}
        summary.append(
            {
                "window": key,
                "lookback_days": payload.get("lookback_days"),
                "maximum": cpu.get("maximum") if isinstance(cpu, dict) else None,
                "p99": cpu.get("p99") if isinstance(cpu, dict) else None,
                "p95": cpu.get("p95") if isinstance(cpu, dict) else None,
                "sample_count": cpu.get("sample_count") if isinstance(cpu, dict) else 0,
            }
        )
    return summary


def _asg_chart_points(row: AsgInventory, selected: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    metric_history = metadata.get("metric_history") or {}
    points = _metric_series(metric_history, "cpuutilization")
    evidence = (selected or {}).get("evidence") or {}
    current_desired = evidence.get("current_desired") or row.desired_capacity
    target_desired = (selected or {}).get("target_desired_capacity")
    scale_ratio = (
        float(current_desired) / float(target_desired)
        if current_desired and target_desired
        else None
    )
    chart_points = [
        {
            **point,
            "maximum_on_target": _project_metric_value(point.get("maximum"), scale_ratio),
            "p99_on_target": _project_metric_value(point.get("p99"), scale_ratio),
            "p95_on_target": _project_metric_value(point.get("p95"), scale_ratio),
            "scale_ratio": scale_ratio,
        }
        for point in points
    ]
    return {
        "metric": "CPUUtilization",
        "unit": "Percent",
        "points": chart_points,
        "target_capacity_line": 100,
    }


def _asg_memory_chart_points(row: AsgInventory, selected: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    metric_history = metadata.get("metric_history") or {}
    points = _metric_series(metric_history, "memoryutilization")
    evidence = (selected or {}).get("evidence") or {}
    current_desired = evidence.get("current_desired") or row.desired_capacity
    target_desired = (selected or {}).get("target_desired_capacity")
    scale_ratio = (
        float(current_desired) / float(target_desired)
        if current_desired and target_desired
        else None
    )
    chart_points = [
        {
            **point,
            "maximum_on_target": _project_metric_value(point.get("maximum"), scale_ratio),
            "p99_on_target": _project_metric_value(point.get("p99"), scale_ratio),
            "p95_on_target": _project_metric_value(point.get("p95"), scale_ratio),
            "scale_ratio": scale_ratio,
        }
        for point in points
    ]
    return {
        "metric": "MemoryUtilization",
        "unit": "Percent",
        "points": chart_points,
        "target_capacity_line": 100,
    }


def _asg_utilization_bars(selected: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not selected:
        return []
    evidence = selected.get("evidence") or {}
    return [
        {
            "id": "projected_cpu",
            "label": "Projected CPU",
            "value_ratio": selected.get("projected_cpu_util"),
            "detail": {
                "binding_dimension": selected.get("binding_dimension"),
                "window_days": evidence.get("desired_selected_window_days"),
            },
        },
        {
            "id": "projected_memory",
            "label": "Projected memory",
            "value_ratio": selected.get("projected_memory_util"),
            "detail": {
                "binding_dimension": selected.get("binding_dimension"),
                "window_days": evidence.get("desired_selected_window_days"),
            },
        },
        {
            "id": "desired_capacity",
            "label": "Target desired capacity",
            "value_ratio": (
                float(selected.get("target_desired_capacity")) / float(evidence.get("current_desired"))
                if selected.get("target_desired_capacity") is not None and evidence.get("current_desired")
                else None
            ),
            "detail": {
                "current_desired": evidence.get("current_desired"),
                "target_desired": selected.get("target_desired_capacity"),
            },
        },
    ]


def _project_metric_value(value: Any, ratio: float | None) -> float | None:
    if value is None or ratio is None:
        return None
    return round(float(value) * ratio, 2)


def _ecs_metric_points(
    row: MaxOpsInventory,
    selected: dict[str, Any] | None,
    *,
    metric_key: str = "cpu_utilization",
    metric_name: str = "CPUUtilization",
    current_capacity_key: str = "cpu_reservation",
    target_capacity_key: str = "target_cpu_reservation",
) -> dict[str, Any]:
    current = row.current_config_json or {}
    metric_history = current.get("metric_history") if isinstance(current, dict) else {}
    series = metric_history.get(metric_key) if isinstance(metric_history, dict) else {}
    series = series if isinstance(series, dict) else {}
    timestamps = series.get("timestamps") or []
    averages = series.get("average") or []
    maximums = series.get("maximum") or []
    p99s = series.get("p99") or []
    p95s = series.get("p95") or []
    current_capacity = current.get(current_capacity_key)
    target_capacity = (selected or {}).get(target_capacity_key)
    scale_ratio = (
        float(current_capacity) / float(target_capacity)
        if current_capacity and target_capacity
        else None
    )
    points = []
    for index, timestamp in enumerate(timestamps):
        average = (
            float(averages[index])
            if index < len(averages) and averages[index] is not None
            else None
        )
        maximum = (
            float(maximums[index])
            if index < len(maximums) and maximums[index] is not None
            else average
        )
        p99 = (
            float(p99s[index])
            if index < len(p99s) and p99s[index] is not None
            else maximum
        )
        p95 = (
            float(p95s[index])
            if index < len(p95s) and p95s[index] is not None
            else p99
        )
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
        "metric": metric_name,
        "unit": "Percent",
        "points": points,
        "target_capacity_line": 100,
    }


def _ecs_memory_metric_points(row: MaxOpsInventory, selected: dict[str, Any] | None) -> dict[str, Any]:
    return _ecs_metric_points(
        row,
        selected,
        metric_key="memory_utilization",
        metric_name="MemoryUtilization",
        current_capacity_key="memory_reservation",
        target_capacity_key="target_memory_reservation",
    )


def _ecs_lookback_summary(row: MaxOpsInventory) -> list[dict[str, Any]]:
    current = row.current_config_json or {}
    metric_history = current.get("metric_history") if isinstance(current, dict) else {}
    cpu = metric_history.get("cpu_utilization") if isinstance(metric_history, dict) else {}
    values = [
        float(value)
        for value in (cpu.get("average") or [])
        if value is not None
    ] if isinstance(cpu, dict) else []
    maximum = max(values) if values else None
    sorted_values = sorted(values)
    p95 = sorted_values[int((len(sorted_values) - 1) * 0.95)] if values else None
    p99 = sorted_values[-1] if values else None
    return [
        {
            "window": label,
            "lookback_days": days,
            "maximum": maximum,
            "p99": p99,
            "p95": p95,
            "sample_count": len(values),
        }
        for label, days in (("14d", 14), ("30d", 30), ("60d", 60))
    ]


def _ratio(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, min(float(value) / 100.0, 1.0))
    except (TypeError, ValueError):
        return None


def _ecs_utilization_bars(row: MaxOpsInventory) -> list[dict[str, Any]]:
    current = row.current_config_json or {}
    target = row.target_config_json or {}
    desired = current.get("desired_count")
    target_desired = target.get("desired_count")
    return [
        {
            "id": "cpu",
            "label": "CPU",
            "value_ratio": _ratio(current.get("avg_cpu_utilization")),
            "detail": {
                "reservation": current.get("cpu_reservation"),
                "target_reservation": target.get("cpu_reservation"),
            },
        },
        {
            "id": "memory",
            "label": "Memory",
            "value_ratio": _ratio(current.get("avg_memory_utilization")),
            "detail": {
                "reservation": current.get("memory_reservation"),
                "target_reservation": target.get("memory_reservation"),
            },
        },
        {
            "id": "desired_count",
            "label": "Desired count",
            "value_ratio": (
                float(target_desired) / float(desired)
                if desired and target_desired is not None
                else None
            ),
            "detail": {
                "current": desired,
                "target": target_desired,
            },
        },
    ]


def _ecs_detail(row: MaxOpsInventory, db: Session) -> dict[str, Any]:
    metadata = row.metadata_json or {}
    current = row.current_config_json or {}
    target = row.target_config_json or {}
    evidence = row.evidence_json or {}
    classification = "ACTIONABLE" if row.check_id else "NO_RECOMMENDATION"
    stored_tiers = evidence.get("rightsizer_tiers")
    stored_tiers = stored_tiers if isinstance(stored_tiers, dict) else {}

    def stored_option(key: str) -> dict[str, Any] | None:
        option = stored_tiers.get(key)
        if not isinstance(option, dict):
            return None
        enriched = {**option}
        if not isinstance(enriched.get("chart"), dict):
            enriched["chart"] = _ecs_metric_points(row, enriched)
        if not isinstance(enriched.get("memory_chart"), dict):
            enriched["memory_chart"] = _ecs_memory_metric_points(row, enriched)
        return enriched

    tier_options = {
        "conservative": stored_option("conservative"),
        "balanced": stored_option("balanced"),
        "aggressive": stored_option("aggressive"),
        "default": stored_tiers.get("default")
        if stored_tiers.get("default") in {"conservative", "balanced", "aggressive"}
        else None,
    }
    recommendations = [
        option
        for option in (
            tier_options["conservative"],
            tier_options["balanced"],
            tier_options["aggressive"],
        )
        if isinstance(option, dict)
    ]
    default_key = tier_options.get("default")
    recommendation = (
        tier_options.get(default_key) if isinstance(default_key, str) else None
    ) or (recommendations[0] if recommendations else None)
    return {
        "resource_type": "ecs",
        "inventory_id": row.inventory_id,
        "resource": {
            "inventory_id": row.inventory_id,
            "resource_id": row.resource_id,
            "resource_name": row.resource_name,
            "account_id": row.account_id,
            "region": row.region,
            "availability_zone": None,
            "state": metadata.get("resource_state"),
            "current_type": row.resource_type,
            "tags": {},
        },
        "status": _classification_label(classification),
        "classification": classification,
        "recommendation": recommendation,
        "recommendations": recommendations,
        "current_instance": {
            "desired_count": current.get("desired_count"),
            "running_count": current.get("running_count"),
            "cpu_reservation": current.get("cpu_reservation"),
            "memory_reservation": current.get("memory_reservation"),
            "vcpus": current.get("cpu_reservation"),
            "memory_gib": (
                round(float(current.get("memory_reservation")) / 1024.0, 2)
                if current.get("memory_reservation") is not None
                else None
            ),
        },
        "current_capacity_evidence": {
            "desired_count": current.get("desired_count"),
            "running_count": current.get("running_count"),
            "cpu_reservation": current.get("cpu_reservation"),
            "memory_reservation": current.get("memory_reservation"),
            "avg_cpu_utilization": current.get("avg_cpu_utilization"),
            "avg_memory_utilization": current.get("avg_memory_utilization"),
        },
        "tiers": tier_options,
        "deferred_reason_codes": [],
        "blocking_reasons": [] if row.check_id else ["CURRENT_ECS_CONFIGURATION_ACCEPTABLE"],
        "telemetry_summary": {
            "cpu_percent": {
                "present": current.get("avg_cpu_utilization") is not None,
                "observed_days": 84.0,
                "average": current.get("avg_cpu_utilization"),
            },
            "memory_percent": {
                "present": current.get("avg_memory_utilization") is not None,
                "observed_days": 84.0,
                "average": current.get("avg_memory_utilization"),
            },
        },
        "risk_assessment": (recommendation or {}).get("risk_assessment"),
        "warnings": [
            {"code": row.check_id, "message": row.description or row.title or row.check_id}
        ] if row.check_id else [],
        "lookback_summary": _ecs_lookback_summary(row),
        "chart": _ecs_metric_points(row, recommendation),
        "memory_chart": _ecs_memory_metric_points(row, recommendation),
        "utilization_bars": _ecs_utilization_bars(row),
        "coverage_caveats": {
            "current_config": current,
            "target_config": target,
            "metadata": metadata,
            "available_actions": row.available_actions_json or [],
        },
        "policy": get_effective_rightsizer_policy(db),
    }


def _ec2_option_with_chart(row: Ec2Inventory, option: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(option, dict):
        return None
    enriched = {**option}
    enriched["chart"] = _scale_chart_points(row, enriched)
    enriched["memory_chart"] = _ec2_memory_chart_points(row, enriched)
    return enriched


def _asg_option_with_chart(row: AsgInventory, option: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(option, dict):
        return None
    enriched = {**option}
    target_capacity = _asg_target_type(enriched)
    if target_capacity:
        enriched["target_capacity"] = target_capacity
    enriched["chart"] = _asg_chart_points(row, enriched)
    enriched["memory_chart"] = _asg_memory_chart_points(row, enriched)
    return enriched


def _asg_tiers_with_charts(row: AsgInventory, tiers: dict[str, Any]) -> dict[str, Any]:
    enriched: dict[str, Any] = {}
    for key, value in (tiers or {}).items():
        enriched[key] = _asg_option_with_chart(row, value) if isinstance(value, dict) else value
    return enriched


def _rds_metric_history(row: RdsInventory) -> dict[str, Any]:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    return row.metric_history_json or metadata.get("metric_history") or {}


def _rds_class_units(db_instance_class: str | None) -> float | None:
    if not db_instance_class:
        return None
    size = str(db_instance_class).split(".")[-1].lower()
    if size.endswith("xlarge"):
        prefix = size[: -len("xlarge")]
        return float(prefix) * 8.0 if prefix.isdigit() else 8.0
    return {
        "nano": 0.25,
        "micro": 0.5,
        "small": 1.0,
        "medium": 2.0,
        "large": 4.0,
    }.get(size)


def _rds_action_scale_ratio(row: RdsInventory, option: dict[str, Any] | None) -> float | None:
    if not option:
        return 1.0
    action_key = option.get("action_key")
    if action_key == "stop":
        return 0.0
    target_class = option.get("target_db_instance_class")
    current_units = _rds_class_units(row.db_instance_class)
    target_units = _rds_class_units(target_class)
    if current_units and target_units:
        return round(current_units / target_units, 4)
    return 1.0


def _projected_points_chart(
    points: list[dict[str, Any]],
    metric_name: str,
    unit: str,
    ratio: float | None,
    target_capacity_line: float = 100,
) -> dict[str, Any]:
    return {
        "metric": metric_name,
        "unit": unit,
        "points": [
            {
                **point,
                "maximum_on_target": _project_metric_value(point.get("maximum"), ratio),
                "p99_on_target": _project_metric_value(point.get("p99"), ratio),
                "p95_on_target": _project_metric_value(point.get("p95"), ratio),
                "scale_ratio": ratio,
            }
            for point in points
        ],
        "target_capacity_line": target_capacity_line,
    }


def _rds_metric_chart(
    row: RdsInventory,
    option: dict[str, Any] | None,
    metric_key: str,
    metric_name: str,
    unit: str = "Percent",
) -> dict[str, Any]:
    points = _metric_series(_rds_metric_history(row), metric_key)
    return _projected_points_chart(points, metric_name, unit, _rds_action_scale_ratio(row, option))


def _rds_iops_points(row: RdsInventory) -> list[dict[str, Any]]:
    metric_history = _rds_metric_history(row)
    read_points = _metric_series(metric_history, "readiops")
    write_points = _metric_series(metric_history, "writeiops")
    points = []
    for index in range(max(len(read_points), len(write_points))):
        read_point = read_points[index] if index < len(read_points) else {}
        write_point = write_points[index] if index < len(write_points) else {}
        point = {"timestamp": read_point.get("timestamp") or write_point.get("timestamp")}
        for stat in ("average", "maximum", "p90", "p95", "p99"):
            read_value = read_point.get(stat)
            write_value = write_point.get(stat)
            point[stat] = (
                round(float(read_value or 0) + float(write_value or 0), 2)
                if read_value is not None or write_value is not None
                else None
            )
        points.append(point)
    return points


def _rds_iops_chart(row: RdsInventory, option: dict[str, Any] | None) -> dict[str, Any]:
    return _projected_points_chart(
        _rds_iops_points(row),
        "ReadWriteIOPS",
        "IOPS",
        _rds_action_scale_ratio(row, option),
        target_capacity_line=0,
    )


def _rds_risk_assessment(finding: MaxOpsInventory | None) -> dict[str, Any]:
    overall = _risk_from_severity(finding.severity if finding else None) or "LOW"
    return {
        "telemetry": "LOW",
        "compute": overall if finding and finding.check_id == "rds_idle_databases" else "LOW",
        "memory": "LOW",
        "network": "LOW",
        "storage": "MEDIUM" if finding and finding.check_id == "rds_non_graviton_instance_class" else "LOW",
        "compatibility": "MEDIUM" if finding and finding.check_id == "rds_non_graviton_instance_class" else "LOW",
        "migration": "MEDIUM" if finding and finding.check_id else "LOW",
        "overall": overall,
    }


def _rds_action_option(
    row: RdsInventory,
    finding: MaxOpsInventory,
    action: dict[str, Any],
) -> dict[str, Any]:
    parameters = action.get("parameters") if isinstance(action.get("parameters"), dict) else {}
    target_config = finding.target_config_json if isinstance(finding.target_config_json, dict) else {}
    target_class = parameters.get("target_db_instance_class")
    if action.get("is_recommended") and not target_class:
        target_class = target_config.get("db_instance_class")
    target_status = target_config.get("db_instance_status") if action.get("is_recommended") else None
    target_display = target_class or target_status or action.get("label") or action.get("action_key")
    enriched = {
        "option": action.get("label") or action.get("action_key"),
        "action_key": action.get("action_key"),
        "label": action.get("label"),
        "description": action.get("description"),
        "target_capacity": target_display,
        "target_db_instance_class": target_class,
        "target_instance_type": target_class,
        "target_db_instance_status": target_status,
        "monthly_savings": round(float(action.get("estimated_monthly_savings") or finding.potential_savings_monthly or 0), 2),
        "yearly_savings": round(float(action.get("estimated_annual_savings") or finding.potential_savings_yearly or 0), 2),
        "risk_assessment": _rds_risk_assessment(finding),
        "reason_codes": [code for code in (finding.check_id, action.get("action_key")) if code],
        "warning_details": [{"code": finding.check_id, "message": finding.description}] if finding.check_id else [],
    }
    scale_ratio = _rds_action_scale_ratio(row, enriched)
    projected_cpu = _project_metric_value(row.avg_cpu_utilization, scale_ratio)
    projected_memory = _project_metric_value(
        (row.metadata_json or {}).get("avg_memory_utilization") if isinstance(row.metadata_json, dict) else None,
        scale_ratio,
    )
    enriched["projected_cpu_util"] = projected_cpu / 100.0 if projected_cpu is not None else None
    enriched["projected_memory_util"] = projected_memory / 100.0 if projected_memory is not None else None
    enriched["chart"] = _rds_metric_chart(row, enriched, "cpuutilization", "CPUUtilization")
    enriched["memory_chart"] = _rds_metric_chart(row, enriched, "memoryutilization", "MemoryUtilization")
    enriched["iops_chart"] = _rds_iops_chart(row, enriched)
    return enriched


def _rds_recommendations(row: RdsInventory, finding: MaxOpsInventory | None) -> list[dict[str, Any]]:
    if not finding or not finding.check_id:
        return []
    actions = finding.available_actions_json if isinstance(finding.available_actions_json, list) else []
    return [
        _rds_action_option(row, finding, action)
        for action in actions
        if isinstance(action, dict)
    ]


def _rds_lookback_summary(row: RdsInventory) -> list[dict[str, Any]]:
    points = _metric_series(_rds_metric_history(row), "cpuutilization")
    windows = (("14d", 14), ("30d", 30), ("60d", 60))
    return [
        {
            "window": label,
            "lookback_days": days,
            "maximum": max((point.get("maximum") for point in points if point.get("maximum") is not None), default=None),
            "p99": max((point.get("p99") for point in points if point.get("p99") is not None), default=None),
            "p95": max((point.get("p95") for point in points if point.get("p95") is not None), default=None),
            "sample_count": len(points),
        }
        for label, days in windows
    ]


def _rds_utilization_bars(row: RdsInventory, selected: dict[str, Any] | None) -> list[dict[str, Any]]:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    memory = metadata.get("avg_memory_utilization")
    total_iops = float(row.avg_read_iops or 0) + float(row.avg_write_iops or 0)
    return [
        {"id": "cpu", "label": "CPU %", "value_ratio": (row.avg_cpu_utilization or 0) / 100.0, "detail": {}},
        {"id": "memory", "label": "Memory %", "value_ratio": (float(memory or 0) / 100.0), "detail": {}},
        {"id": "connections", "label": "Connections", "value_ratio": min(float(row.avg_connections or 0) / 250.0, 1.0), "detail": {}},
        {"id": "iops", "label": "Read/write IOPS", "value_ratio": min(total_iops / 5000.0, 1.0), "detail": {}},
    ]


def _rds_detail(row: RdsInventory, finding: MaxOpsInventory | None, db: Session) -> dict[str, Any]:
    recommendations = _rds_recommendations(row, finding)
    selected = next((option for option in recommendations if option.get("action_key") == (finding.recommended_action if finding else None)), None)
    selected = selected or (recommendations[0] if recommendations else None)
    classification = "ACTIONABLE" if finding and finding.check_id else "NO_RECOMMENDATION"
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    current = {
        "db_instance_class": row.db_instance_class,
        "engine": row.engine,
        "storage_type": metadata.get("storage_type"),
        "avg_cpu_utilization": row.avg_cpu_utilization,
        "avg_memory_utilization": metadata.get("avg_memory_utilization"),
        "avg_connections": row.avg_connections,
        "avg_read_iops": row.avg_read_iops,
        "avg_write_iops": row.avg_write_iops,
    }
    return {
        "resource_type": "rds",
        "inventory_id": row.inventory_id,
        "resource": {
            "inventory_id": row.inventory_id,
            "resource_id": row.resource_id,
            "resource_name": row.resource_name,
            "account_id": row.account_id,
            "region": row.region,
            "availability_zone": row.availability_zone,
            "state": row.state,
            "current_type": row.db_instance_class,
            "tags": row.tags_json or {},
        },
        "status": _classification_label(classification),
        "classification": classification,
        "recommendation": selected,
        "recommendations": recommendations,
        "current_instance": current,
        "current_capacity_evidence": finding.evidence_json if finding else {},
        "tiers": {},
        "deferred_reason_codes": [],
        "blocking_reasons": [],
        "telemetry_summary": {
            "cpu_percent": {"observed_days": 60, "thin_data": False},
            "memory_percent": {"observed_days": 60 if metadata.get("avg_memory_utilization") is not None else 0, "thin_data": metadata.get("avg_memory_utilization") is None},
            "network_in_mbps": {"observed_days": 0, "thin_data": True},
            "network_out_mbps": {"observed_days": 0, "thin_data": True},
        },
        "risk_assessment": _rds_risk_assessment(finding),
        "warnings": [{"code": finding.check_id, "message": finding.description}] if finding and finding.check_id else [],
        "lookback_summary": _rds_lookback_summary(row),
        "chart": (selected or {}).get("chart") or _rds_metric_chart(row, selected, "cpuutilization", "CPUUtilization"),
        "memory_chart": (selected or {}).get("memory_chart") or _rds_metric_chart(row, selected, "memoryutilization", "MemoryUtilization"),
        "iops_chart": (selected or {}).get("iops_chart") or _rds_iops_chart(row, selected),
        "utilization_bars": _rds_utilization_bars(row, selected),
        "coverage_caveats": {
            "engine": row.engine,
            "storage_type": metadata.get("storage_type"),
            "finding": {
                "check_id": finding.check_id,
                "title": finding.title,
                "description": finding.description,
                "available_actions": finding.available_actions_json or [],
            } if finding else None,
        },
        "policy": get_effective_rightsizer_policy(db),
    }


def get_rightsizer_detail(
    resource_type: str,
    inventory_id: int,
    db: Session,
) -> dict[str, Any]:
    normalized = resource_type.lower()
    if normalized == "ecs":
        row = (
            db.query(MaxOpsInventory)
            .filter(
                MaxOpsInventory.resource_type.in_(("ecs", "ecs_service", "ecs_cluster")),
                MaxOpsInventory.inventory_id == inventory_id,
            )
            .first()
        )
        if row is None:
            raise LookupError("ECS inventory resource not found")
        return _ecs_detail(row, db)
    if normalized == "asg":
        row = db.query(AsgInventory).filter(AsgInventory.inventory_id == inventory_id).first()
        if row is None:
            raise LookupError("ASG inventory resource not found")

        recommendation = ASGRightsizer(db).get_recommendation(inventory_id)
        if recommendation is None:
            raise LookupError("ASG rightsizer recommendation not found")

        tiers = _asg_tiers_with_charts(row, recommendation.get("tiers") or {})
        recommendations = [
            option
            for option in (
                _asg_option_with_chart(row, item)
                for item in (recommendation.get("recommendations") or [])
            )
            if option
        ]
        selected = _select_asg_recommendation(
            {
                **recommendation,
                "tiers": tiers,
                "recommendations": recommendations,
            }
        )
        classification = _selected_asg_classification(recommendation)
        current = recommendation.get("current_configuration") or {}
        return {
            "resource_type": "asg",
            "inventory_id": inventory_id,
            "resource": {
                "inventory_id": row.inventory_id,
                "resource_id": row.resource_id,
                "resource_name": row.resource_name,
                "account_id": row.account_id,
                "region": row.region,
                "availability_zone": None,
                "state": row.state,
                "current_type": row.instance_type,
                "tags": row.tags_json or {},
            },
            "status": _classification_label(classification),
            "classification": classification,
            "recommendation": selected,
            "recommendations": recommendations,
            "current_instance": {
                "min_size": current.get("min_size"),
                "desired_capacity": current.get("desired_capacity"),
                "max_size": current.get("max_size"),
                "availability_zones": current.get("availability_zones") or [],
            },
            "current_capacity_evidence": recommendation.get("current_capacity_evidence"),
            "tiers": tiers,
            "deferred_reason_codes": recommendation.get("deferred_reason_codes") or [],
            "blocking_reasons": recommendation.get("blocking_reasons") or [],
            "telemetry_summary": recommendation.get("telemetry_summary") or {},
            "risk_assessment": (selected or {}).get("risk_assessment"),
            "warnings": [
                {"code": code, "message": code.replace("_", " ")}
                for code in ((selected or {}).get("reason_codes") or [])
            ],
            "lookback_summary": _asg_lookback_summary(row),
            "chart": _asg_chart_points(row, selected),
            "memory_chart": _asg_memory_chart_points(row, selected),
            "utilization_bars": _asg_utilization_bars(selected),
            "coverage_caveats": {
                "capacity_policy": recommendation.get("capacity_policy"),
                "scope_policy": recommendation.get("scope_policy"),
                "pricing_evidence": recommendation.get("pricing_evidence"),
                "messages": recommendation.get("messages") or [],
                "savings_previews": recommendation.get("savings_previews") or [],
            },
            "policy": get_effective_rightsizer_policy(db),
        }
    if normalized == "rds":
        pair = (
            db.query(RdsInventory, MaxOpsInventory)
            .outerjoin(
                MaxOpsInventory,
                (MaxOpsInventory.resource_type == "rds")
                & (MaxOpsInventory.inventory_id == RdsInventory.inventory_id),
            )
            .filter(RdsInventory.inventory_id == inventory_id)
            .first()
        )
        if pair is None:
            raise LookupError("RDS inventory resource not found")
        row, finding = pair
        return _rds_detail(row, finding, db)
    if normalized != "ec2":
        raise ValueError("Detailed rightsizer evidence is currently available for EC2, ASG, ECS, and RDS only")

    row = db.query(Ec2Inventory).filter(Ec2Inventory.inventory_id == inventory_id).first()
    if row is None:
        raise LookupError("EC2 inventory resource not found")

    recommendation = _rightsizer(db).get_recommendation(inventory_id)
    if recommendation is None:
        raise LookupError("EC2 rightsizer recommendation not found")

    raw_tiers = recommendation.get("tiers") or {}
    tiers = {
        key: _ec2_option_with_chart(row, raw_tiers.get(key))
        for key in ("conservative", "balanced", "aggressive")
    }
    tiers["default"] = (
        raw_tiers.get("default")
        if raw_tiers.get("default") in {"conservative", "balanced", "aggressive"}
        else None
    )
    recommendations = [
        option
        for option in (
            _ec2_option_with_chart(row, item)
            for item in (recommendation.get("recommendations") or [])
        )
        if option
    ]
    default_key = tiers.get("default")
    selected = (
        tiers.get(default_key) if isinstance(default_key, str) else None
    ) or (recommendations[0] if recommendations else None)
    classification = _selected_classification(recommendation)
    return {
        "resource_type": "ec2",
        "inventory_id": inventory_id,
        "resource": {
            "inventory_id": row.inventory_id,
            "resource_id": row.resource_id,
            "resource_name": row.resource_name,
            "account_id": row.account_id,
            "region": row.region,
            "availability_zone": row.availability_zone,
            "state": row.state,
            "current_type": row.instance_type,
            "tags": row.tags_json or {},
        },
        "status": _classification_label(classification),
        "classification": classification,
        "recommendation": selected,
        "recommendations": recommendations,
        "current_instance": recommendation.get("current_instance"),
        "current_capacity_evidence": recommendation.get("current_capacity_evidence"),
        "tiers": tiers,
        "deferred_reason_codes": recommendation.get("deferred_reason_codes") or [],
        "blocking_reasons": recommendation.get("blocking_reasons") or [],
        "telemetry_summary": recommendation.get("telemetry_summary") or {},
        "risk_assessment": (selected or {}).get("risk_assessment"),
        "warnings": (selected or {}).get("warning_details") or [],
        "lookback_summary": _lookback_summary(row),
        "chart": _scale_chart_points(row, selected),
        "memory_chart": _ec2_memory_chart_points(row, selected),
        "utilization_bars": _utilization_bars(selected),
        "coverage_caveats": {
            "availability_note": recommendation.get("availability_note"),
            "availability_validated": recommendation.get("availability_validated"),
            "capability_catalog": recommendation.get("capability_catalog"),
            "policy": recommendation.get("policy"),
            "compute_policy": recommendation.get("compute_policy"),
            "scope_policy": recommendation.get("scope_policy"),
        },
        "policy": get_effective_rightsizer_policy(db),
    }
