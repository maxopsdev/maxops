"""MCP-focused read endpoints with server-side filtering/sorting/pagination."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, and_, asc, case, cast, desc, func, literal, or_, union_all
from sqlalchemy.orm import Session

from app.checks.registry import check_registry
from app.database import get_db
from app.models.inventory import (
    DynamoDbInventory,
    EbsInventory,
    Ec2Inventory,
    ElasticacheInventory,
    MaxOpsInventory,
    RdsInventory,
    ResourceTag,
    S3Inventory,
)
from app.models.settings import OnboardingCheckResult, OnboardingExecution, ResourceExemption
from app.services.inventory_service import RESOURCE_MODELS, get_imported_check_summaries
from app.utils.resource_snooze import GLOBAL_RESOURCE_SNOOZE_CHECK_ID, serialize_snooze

router = APIRouter(prefix="/mcp", tags=["mcp"])

SUPPORTED_RESOURCE_TYPES = set(RESOURCE_MODELS.keys())
MAX_PAGE_SIZE = 500


def _validate_resource_type(resource_type: str) -> None:
    if resource_type not in SUPPORTED_RESOURCE_TYPES:
        allowed = ", ".join(sorted(SUPPORTED_RESOURCE_TYPES))
        raise HTTPException(status_code=400, detail=f"Unsupported resource_type '{resource_type}'. Allowed: {allowed}")


def _cost_column(model: Any):
    if hasattr(model, "monthly_cost_estimate"):
        return getattr(model, "monthly_cost_estimate")
    if hasattr(model, "estimated_monthly_cost"):
        return getattr(model, "estimated_monthly_cost")
    return None


def _state_column(model: Any):
    if hasattr(model, "state"):
        return getattr(model, "state")
    return None


def _build_inventory_costs_subquery(db: Session):
    """Build a normalized view of inventory costs across resource tables."""
    cost_selects = []
    model_specs = [
        ("ec2", Ec2Inventory, Ec2Inventory.monthly_cost_estimate),
        ("rds", RdsInventory, RdsInventory.monthly_cost_estimate),
        ("s3", S3Inventory, S3Inventory.estimated_monthly_cost),
        ("dynamodb", DynamoDbInventory, DynamoDbInventory.monthly_cost_estimate),
        ("ebs", EbsInventory, EbsInventory.monthly_cost_estimate),
        ("elasticache", ElasticacheInventory, ElasticacheInventory.monthly_cost_estimate),
    ]
    for resource_type, model, cost_col in model_specs:
        cost_selects.append(
            db.query(
                literal(resource_type).label("resource_type"),
                model.inventory_id.label("inventory_id"),
                model.resource_id.label("resource_id"),
                model.resource_name.label("resource_name"),
                model.account_id.label("account_id"),
                model.region.label("region"),
                model.generated_at.label("generated_at"),
                func.coalesce(cost_col, 0.0).label("monthly_cost_estimate"),
            )
        )

    return union_all(*[query.statement for query in cost_selects]).subquery("inventory_costs")


def _build_savings_by_resource_subquery(db: Session):
    """Aggregate potential savings by resource to avoid duplicate finding rows."""
    return (
        db.query(
            MaxOpsInventory.resource_type.label("resource_type"),
            MaxOpsInventory.inventory_id.label("inventory_id"),
            func.coalesce(func.sum(func.coalesce(MaxOpsInventory.potential_savings_yearly, 0.0)), 0.0).label(
                "potential_savings_yearly"
            ),
        )
        .group_by(MaxOpsInventory.resource_type, MaxOpsInventory.inventory_id)
        .subquery("savings_by_resource")
    )


def _apply_inventory_filters(
    query: Any,
    *,
    model: Any,
    resource_type: str,
    now: datetime,
    q: Optional[str],
    account_id: Optional[str],
    region: Optional[str],
    state: Optional[str],
    check_id: Optional[str],
    severity: Optional[str],
    finding_type: Optional[str],
    tag_key: Optional[str],
    tag_value: Optional[str],
    tag_value_mode: str,
    metadata_key: Optional[str],
    metadata_value: Optional[str],
    snooze_state: str,
    min_savings_yearly: Optional[float],
    max_savings_yearly: Optional[float],
    min_monthly_cost: Optional[float],
    max_monthly_cost: Optional[float],
) -> Any:
    if q:
        needle = f"%{q.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(model.resource_id).like(needle),
                func.lower(func.coalesce(model.resource_name, "")).like(needle),
                func.lower(func.coalesce(MaxOpsInventory.title, "")).like(needle),
                func.lower(func.coalesce(MaxOpsInventory.check_id, "")).like(needle),
                func.lower(func.coalesce(cast(getattr(model, "tags_json"), String), "")).like(needle),
                func.lower(func.coalesce(cast(getattr(model, "metadata_json"), String), "")).like(needle),
            )
        )
    if account_id:
        query = query.filter(model.account_id == account_id)
    if region:
        query = query.filter(model.region == region)
    if check_id:
        query = query.filter(MaxOpsInventory.check_id == check_id)
    if severity:
        query = query.filter(MaxOpsInventory.severity == severity)
    if finding_type:
        query = query.filter(MaxOpsInventory.finding_type == finding_type)
    normalized_tag_key = (tag_key or "").strip().lower()
    normalized_tag_value = (tag_value or "").strip().lower()
    if normalized_tag_key or normalized_tag_value:
        tags_match = query.session.query(ResourceTag.inventory_id).filter(
            ResourceTag.resource_type == resource_type,
        )
        if normalized_tag_key:
            tags_match = tags_match.filter(ResourceTag.tag_key_normalized == normalized_tag_key)
        if normalized_tag_value:
            if tag_value_mode == "equals":
                tags_match = tags_match.filter(ResourceTag.tag_value_normalized == normalized_tag_value)
            else:
                tags_match = tags_match.filter(
                    func.coalesce(ResourceTag.tag_value_normalized, "").like(f"%{normalized_tag_value}%")
                )
        query = query.filter(model.inventory_id.in_(tags_match))
    if metadata_key:
        query = query.filter(
            func.lower(func.coalesce(cast(getattr(model, "metadata_json"), String), "")).like(
                f'%"{metadata_key.strip().lower()}"%'
            )
        )
    if metadata_value:
        query = query.filter(
            func.lower(func.coalesce(cast(getattr(model, "metadata_json"), String), "")).like(
                f"%{metadata_value.strip().lower()}%"
            )
        )

    state_col = _state_column(model)
    if state:
        if state_col is None:
            raise HTTPException(status_code=400, detail=f"state filter is not supported for this resource type")
        query = query.filter(state_col == state)

    savings_col = func.coalesce(MaxOpsInventory.potential_savings_yearly, 0.0)
    if min_savings_yearly is not None:
        query = query.filter(savings_col >= min_savings_yearly)
    if max_savings_yearly is not None:
        query = query.filter(savings_col <= max_savings_yearly)

    cost_col = _cost_column(model)
    if cost_col is not None:
        monthly_cost_col = func.coalesce(cost_col, 0.0)
        if min_monthly_cost is not None:
            query = query.filter(monthly_cost_col >= min_monthly_cost)
        if max_monthly_cost is not None:
            query = query.filter(monthly_cost_col <= max_monthly_cost)
    elif min_monthly_cost is not None or max_monthly_cost is not None:
        raise HTTPException(status_code=400, detail="monthly cost filters are not supported for this resource type")

    active_snooze = and_(ResourceExemption.snoozed_until.isnot(None), ResourceExemption.snoozed_until > now)
    if snooze_state == "snoozed":
        query = query.filter(active_snooze)
    elif snooze_state == "not_snoozed":
        query = query.filter(
            or_(
                ResourceExemption.id.is_(None),
                ResourceExemption.snoozed_until.is_(None),
                ResourceExemption.snoozed_until <= now,
            )
        )

    return query


@router.get("/inventory/{resource_type}/query")
def query_inventory_for_mcp(
    resource_type: str,
    q: Optional[str] = Query(None, description="Case-insensitive search on resource fields, tags, and metadata"),
    account_id: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    check_id: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    finding_type: Optional[str] = Query(None),
    tag_key: Optional[str] = Query(None, description="Filter by tag key, e.g. cost_center"),
    tag_value: Optional[str] = Query(None, description="Filter by tag value substring"),
    tag_value_mode: str = Query("equals", pattern="^(equals|contains)$"),
    metadata_key: Optional[str] = Query(None, description="Filter by metadata key"),
    metadata_value: Optional[str] = Query(None, description="Filter by metadata value substring"),
    snooze_state: str = Query("all", pattern="^(all|snoozed|not_snoozed)$"),
    min_savings_yearly: Optional[float] = Query(None),
    max_savings_yearly: Optional[float] = Query(None),
    min_monthly_cost: Optional[float] = Query(None),
    max_monthly_cost: Optional[float] = Query(None),
    sort_by: str = Query(
        "potential_savings_yearly",
        pattern="^(resource_id|resource_name|account_id|region|state|generated_at|monthly_cost|potential_savings_yearly|severity|check_id|snoozed_until)$",
    ),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=MAX_PAGE_SIZE),
    include_summary: bool = Query(True),
    db: Session = Depends(get_db),
):
    """Query imported inventory using MCP-friendly server-side filters and sort."""
    _validate_resource_type(resource_type)
    model = RESOURCE_MODELS[resource_type]
    now = datetime.now(timezone.utc)

    base_query = (
        db.query(model, MaxOpsInventory, ResourceExemption)
        .outerjoin(
            MaxOpsInventory,
            and_(
                MaxOpsInventory.resource_type == resource_type,
                MaxOpsInventory.inventory_id == model.inventory_id,
            ),
        )
        .outerjoin(
            ResourceExemption,
            and_(
                ResourceExemption.check_id == GLOBAL_RESOURCE_SNOOZE_CHECK_ID,
                ResourceExemption.resource_id == model.resource_id,
            ),
        )
    )

    filtered_query = _apply_inventory_filters(
        base_query,
        model=model,
        resource_type=resource_type,
        now=now,
        q=q,
        account_id=account_id,
        region=region,
        state=state,
        check_id=check_id,
        severity=severity,
        finding_type=finding_type,
        tag_key=tag_key,
        tag_value=tag_value,
        tag_value_mode=tag_value_mode,
        metadata_key=metadata_key,
        metadata_value=metadata_value,
        snooze_state=snooze_state,
        min_savings_yearly=min_savings_yearly,
        max_savings_yearly=max_savings_yearly,
        min_monthly_cost=min_monthly_cost,
        max_monthly_cost=max_monthly_cost,
    )

    cost_col = _cost_column(model)
    state_col = _state_column(model)
    sort_columns = {
        "resource_id": model.resource_id,
        "resource_name": model.resource_name,
        "account_id": model.account_id,
        "region": model.region,
        "generated_at": model.generated_at,
        "monthly_cost": func.coalesce(cost_col, 0.0) if cost_col is not None else model.inventory_id,
        "potential_savings_yearly": func.coalesce(MaxOpsInventory.potential_savings_yearly, 0.0),
        "severity": func.coalesce(MaxOpsInventory.severity, ""),
        "check_id": func.coalesce(MaxOpsInventory.check_id, ""),
        "snoozed_until": ResourceExemption.snoozed_until,
    }
    if state_col is not None:
        sort_columns["state"] = state_col

    sort_col = sort_columns.get(sort_by)
    if sort_col is None:
        raise HTTPException(status_code=400, detail=f"Unsupported sort_by '{sort_by}' for resource_type '{resource_type}'")

    order_by_expr = asc(sort_col) if sort_order == "asc" else desc(sort_col)
    rows = (
        filtered_query
        .order_by(order_by_expr, model.inventory_id.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    total_matching = (
        filtered_query.with_entities(func.count(func.distinct(model.inventory_id))).scalar() or 0
    )

    resources = []
    for inventory_row, finding_row, exemption in rows:
        metadata = getattr(inventory_row, "metadata_json", None) or {}
        tags = getattr(inventory_row, "tags_json", None) or {}
        monthly_cost = 0.0
        if cost_col is not None:
            monthly_cost = float(getattr(inventory_row, cost_col.key) or 0.0)
        potential_savings_yearly = float((finding_row.potential_savings_yearly if finding_row else 0.0) or 0.0)

        resource_payload: Dict[str, Any] = {
            "inventory_id": inventory_row.inventory_id,
            "resource_id": inventory_row.resource_id,
            "resource_name": inventory_row.resource_name,
            "resource_type": inventory_row.resource_type,
            "account_id": inventory_row.account_id,
            "region": inventory_row.region,
            "state": getattr(inventory_row, "state", None),
            "generated_at": inventory_row.generated_at.isoformat() if inventory_row.generated_at else None,
            "monthly_cost_estimate": round(monthly_cost, 6),
            "potential_savings_yearly": round(potential_savings_yearly, 6),
            "snooze": serialize_snooze(exemption),
            "maxops": {
                "check_id": finding_row.check_id if finding_row else None,
                "finding_type": finding_row.finding_type if finding_row else None,
                "severity": finding_row.severity if finding_row else None,
                "title": finding_row.title if finding_row else None,
                "description": finding_row.description if finding_row else None,
                "recommended_action": finding_row.recommended_action if finding_row else None,
            },
            "tags": tags,
            "metadata": metadata,
        }
        resources.append(resource_payload)

    response: Dict[str, Any] = {
        "resource_type": resource_type,
        "query": {
            "q": q,
            "account_id": account_id,
            "region": region,
            "state": state,
            "check_id": check_id,
            "severity": severity,
            "finding_type": finding_type,
            "tag_key": tag_key,
            "tag_value": tag_value,
            "tag_value_mode": tag_value_mode,
            "metadata_key": metadata_key,
            "metadata_value": metadata_value,
            "snooze_state": snooze_state,
            "min_savings_yearly": min_savings_yearly,
            "max_savings_yearly": max_savings_yearly,
            "min_monthly_cost": min_monthly_cost,
            "max_monthly_cost": max_monthly_cost,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "offset": offset,
            "limit": limit,
        },
        "total_matching": int(total_matching),
        "returned": len(resources),
        "resources": resources,
    }

    if include_summary:
        active_snooze = and_(ResourceExemption.snoozed_until.isnot(None), ResourceExemption.snoozed_until > now)
        summary_cols = [
            func.count(func.distinct(model.inventory_id)).label("total_resources"),
            func.coalesce(func.sum(func.coalesce(MaxOpsInventory.potential_savings_yearly, 0.0)), 0.0).label("total_potential_savings_yearly"),
            func.sum(case((active_snooze, 1), else_=0)).label("snoozed_resources"),
        ]
        if cost_col is not None:
            summary_cols.append(
                func.coalesce(func.sum(func.coalesce(cost_col, 0.0)), 0.0).label("total_monthly_cost_estimate")
            )
        else:
            summary_cols.append(literal(0.0).label("total_monthly_cost_estimate"))

        summary_row = filtered_query.with_entities(*summary_cols).first()
        response["summary"] = {
            "total_resources": int(summary_row.total_resources or 0),
            "snoozed_resources": int(summary_row.snoozed_resources or 0),
            "not_snoozed_resources": int((summary_row.total_resources or 0) - (summary_row.snoozed_resources or 0)),
            "total_potential_savings_yearly": round(float(summary_row.total_potential_savings_yearly or 0.0), 6),
            "total_monthly_cost_estimate": round(float(summary_row.total_monthly_cost_estimate or 0.0), 6),
        }

    return response


@router.get("/checks/results/query")
def query_check_results_for_mcp(
    resource_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    check_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Case-insensitive search on check id/name/description"),
    min_resources_found: Optional[int] = Query(None, ge=0),
    min_potential_savings_yearly: Optional[float] = Query(None),
    sort_by: str = Query("potential_savings_yearly", pattern="^(check_id|name|resource_type|status|resources_found|potential_savings_yearly|execution_time)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=MAX_PAGE_SIZE),
    include_parameters: bool = Query(False),
    include_checks_without_results: bool = Query(True),
    db: Session = Depends(get_db),
):
    """Return latest check results with metadata in one MCP-optimized payload."""
    check_meta = {item.check_id: item for item in check_registry.list_checks(resource_type)}

    onboarding_rows = (
        db.query(OnboardingCheckResult, OnboardingExecution.completed_at, OnboardingExecution.started_at)
        .join(OnboardingExecution, OnboardingCheckResult.execution_id == OnboardingExecution.id)
        .filter(
            (OnboardingExecution.completed_at.isnot(None)) |
            (OnboardingExecution.started_at.isnot(None))
        )
        .order_by(func.coalesce(OnboardingExecution.completed_at, OnboardingExecution.started_at).desc())
        .all()
    )

    latest_by_check: Dict[str, Dict[str, Any]] = {}
    for check_result, completed_at, started_at in onboarding_rows:
        if check_result.check_id in latest_by_check:
            continue
        execution_time = completed_at or started_at
        metadata_json = check_result.metadata_json or {}
        potential_savings_yearly = float(
            metadata_json.get("potential_savings_yearly")
            or (metadata_json.get("potential_savings_monthly", 0) * 12)
            or 0.0
        )
        latest_by_check[check_result.check_id] = {
            "check_id": check_result.check_id,
            "name": check_result.name,
            "description": check_result.description,
            "resource_type": check_result.resource_type,
            "status": check_result.status,
            "resources_found": int(check_result.resources_found or 0),
            "potential_savings_yearly": round(potential_savings_yearly, 6),
            "error": check_result.error,
            "execution_time": execution_time.isoformat() if execution_time else None,
            "source": "onboarding",
        }

    imported = get_imported_check_summaries(db)
    for imported_check_id, summary in imported.items():
        imported_time = summary.execution_time.isoformat() if summary.execution_time else None
        existing = latest_by_check.get(imported_check_id)
        if existing and imported_time and existing.get("execution_time") and existing["execution_time"] >= imported_time:
            continue
        latest_by_check[imported_check_id] = {
            "check_id": summary.check_id,
            "name": summary.name,
            "description": summary.description,
            "resource_type": summary.resource_type,
            "status": summary.status,
            "resources_found": int(summary.resources_found or 0),
            "potential_savings_yearly": round(float(summary.potential_savings_yearly or 0.0), 6),
            "error": None,
            "execution_time": imported_time,
            "source": "imported",
        }

    rows = list(latest_by_check.values())
    if include_checks_without_results:
        for metadata in check_meta.values():
            if metadata.check_id in latest_by_check:
                continue
            rows.append(
                {
                    "check_id": metadata.check_id,
                    "name": metadata.name,
                    "description": metadata.description,
                    "resource_type": metadata.resource_type,
                    "status": "not_run",
                    "resources_found": 0,
                    "potential_savings_yearly": 0.0,
                    "error": None,
                    "execution_time": None,
                    "source": "registry",
                }
            )

    def _matches(item: Dict[str, Any]) -> bool:
        if resource_type and item.get("resource_type") != resource_type:
            return False
        if status and item.get("status") != status:
            return False
        if check_id and item.get("check_id") != check_id:
            return False
        if min_resources_found is not None and int(item.get("resources_found") or 0) < min_resources_found:
            return False
        if min_potential_savings_yearly is not None and float(item.get("potential_savings_yearly") or 0.0) < min_potential_savings_yearly:
            return False
        if q:
            needle = q.strip().lower()
            hay = " ".join(
                [
                    str(item.get("check_id") or ""),
                    str(item.get("name") or ""),
                    str(item.get("description") or ""),
                ]
            ).lower()
            if needle not in hay:
                return False
        return True

    filtered = [item for item in rows if _matches(item)]

    def _sort_value(item: Dict[str, Any]):
        value = item.get(sort_by)
        if sort_by in {"resources_found"}:
            return int(value or 0)
        if sort_by in {"potential_savings_yearly"}:
            return float(value or 0.0)
        return str(value or "")

    reverse = sort_order == "desc"
    filtered.sort(key=_sort_value, reverse=reverse)
    sliced = filtered[offset: offset + limit]

    items = []
    for item in sliced:
        metadata = check_meta.get(item["check_id"])
        payload = dict(item)
        payload["default_action"] = metadata.default_action if metadata else None
        payload["parameters"] = metadata.parameters if (metadata and include_parameters) else None
        items.append(payload)

    return {
        "query": {
            "resource_type": resource_type,
            "status": status,
            "check_id": check_id,
            "q": q,
            "min_resources_found": min_resources_found,
            "min_potential_savings_yearly": min_potential_savings_yearly,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "offset": offset,
            "limit": limit,
            "include_parameters": include_parameters,
            "include_checks_without_results": include_checks_without_results,
        },
        "total_matching": len(filtered),
        "returned": len(items),
        "items": items,
    }


@router.get("/tags/cost-savings-summary")
def get_tag_cost_savings_summary(
    tag_key: str = Query(..., min_length=1, description="Tag key, e.g. cost_center"),
    tag_value: str = Query(..., min_length=1, description="Tag value, e.g. CC-1202"),
    tag_value_mode: str = Query("equals", pattern="^(equals|contains)$"),
    resource_type: Optional[str] = Query(None),
    include_details: bool = Query(True),
    details_limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """Fast aggregate totals for monthly cost and potential savings by tag filter."""
    normalized_key = tag_key.strip().lower()
    normalized_value = tag_value.strip().lower()
    if resource_type:
        _validate_resource_type(resource_type)

    costs_sq = _build_inventory_costs_subquery(db)
    savings_sq = _build_savings_by_resource_subquery(db)

    tag_query = db.query(ResourceTag).filter(ResourceTag.tag_key_normalized == normalized_key)
    if resource_type:
        tag_query = tag_query.filter(ResourceTag.resource_type == resource_type)
    if tag_value_mode == "equals":
        tag_query = tag_query.filter(ResourceTag.tag_value_normalized == normalized_value)
    else:
        tag_query = tag_query.filter(func.coalesce(ResourceTag.tag_value_normalized, "").like(f"%{normalized_value}%"))
    tag_matches = tag_query.subquery("tag_matches")

    joined = (
        db.query(
            costs_sq.c.resource_type,
            costs_sq.c.inventory_id,
            costs_sq.c.resource_id,
            costs_sq.c.resource_name,
            costs_sq.c.account_id,
            costs_sq.c.region,
            costs_sq.c.generated_at,
            costs_sq.c.monthly_cost_estimate,
            func.coalesce(savings_sq.c.potential_savings_yearly, 0.0).label("potential_savings_yearly"),
        )
        .join(
            tag_matches,
            and_(
                tag_matches.c.resource_type == costs_sq.c.resource_type,
                tag_matches.c.inventory_id == costs_sq.c.inventory_id,
            ),
        )
        .outerjoin(
            savings_sq,
            and_(
                savings_sq.c.resource_type == costs_sq.c.resource_type,
                savings_sq.c.inventory_id == costs_sq.c.inventory_id,
            ),
        )
    )

    summary_row = joined.with_entities(
        func.count().label("total_resources"),
        func.coalesce(func.sum(func.coalesce(costs_sq.c.monthly_cost_estimate, 0.0)), 0.0).label("total_monthly_cost"),
        func.coalesce(func.sum(func.coalesce(savings_sq.c.potential_savings_yearly, 0.0)), 0.0).label("total_potential_savings_yearly"),
    ).first()

    by_resource_type_rows = (
        joined.with_entities(
            costs_sq.c.resource_type.label("resource_type"),
            func.count().label("resources"),
            func.coalesce(func.sum(func.coalesce(costs_sq.c.monthly_cost_estimate, 0.0)), 0.0).label("total_monthly_cost"),
            func.coalesce(func.sum(func.coalesce(savings_sq.c.potential_savings_yearly, 0.0)), 0.0).label("total_potential_savings_yearly"),
        )
        .group_by(costs_sq.c.resource_type)
        .order_by(costs_sq.c.resource_type.asc())
        .all()
    )

    details = []
    if include_details:
        detail_rows = (
            joined.order_by(costs_sq.c.monthly_cost_estimate.desc(), costs_sq.c.resource_id.asc())
            .limit(details_limit)
            .all()
        )
        details = [
            {
                "resource_type": row.resource_type,
                "inventory_id": row.inventory_id,
                "resource_id": row.resource_id,
                "resource_name": row.resource_name,
                "account_id": row.account_id,
                "region": row.region,
                "generated_at": row.generated_at.isoformat() if row.generated_at else None,
                "monthly_cost_estimate": round(float(row.monthly_cost_estimate or 0.0), 6),
                "potential_savings_yearly": round(float(row.potential_savings_yearly or 0.0), 6),
                "potential_savings_monthly": round(float((row.potential_savings_yearly or 0.0) / 12.0), 6),
            }
            for row in detail_rows
        ]

    total_monthly_cost = float(summary_row.total_monthly_cost or 0.0)
    total_potential_savings_yearly = float(summary_row.total_potential_savings_yearly or 0.0)
    return {
        "filter": {
            "tag_key": normalized_key,
            "tag_value": normalized_value,
            "tag_value_mode": tag_value_mode,
            "resource_type": resource_type,
        },
        "summary": {
            "total_resources": int(summary_row.total_resources or 0),
            "total_monthly_cost": round(total_monthly_cost, 6),
            "total_potential_savings_yearly": round(total_potential_savings_yearly, 6),
            "total_potential_savings_monthly": round(total_potential_savings_yearly / 12.0, 6),
        },
        "by_resource_type": [
            {
                "resource_type": row.resource_type,
                "resources": int(row.resources or 0),
                "total_monthly_cost": round(float(row.total_monthly_cost or 0.0), 6),
                "total_potential_savings_yearly": round(float(row.total_potential_savings_yearly or 0.0), 6),
                "total_potential_savings_monthly": round(float((row.total_potential_savings_yearly or 0.0) / 12.0), 6),
            }
            for row in by_resource_type_rows
        ],
        "details_returned": len(details),
        "details": details,
    }
