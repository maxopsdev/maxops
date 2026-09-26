"""CUR cache API routes: cost reports, setup status, and setup actions."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.cur_cost_service import cur_cost_lookup, set_cur_pricing_enabled
from app.services.cur_jobs_service import (
    JOB_EXPORT,
    JOB_REFRESH,
    create_job,
    estimate_refresh,
    get_job,
    latest_jobs,
    preflight,
    run_export_job,
    run_refresh_job,
    running_job_of_type,
)
from app.services.cur_setup_service import get_setup_status
from pricing.cur.datasets import DATASETS, DEFAULT_CACHE_ROOT
from pricing.cur.reader import RESOURCE_COST_DATASET, CurCacheMissingError, cache_status, query_dataset


router = APIRouter(prefix="/cur", tags=["cur"])

ReportGrain = Literal["daily", "monthly"]

REPORT_DATASETS = {
    "services": {
        "daily": "service_daily",
        "monthly": "service_monthly",
    },
    "usage_types": {
        "daily": "usage_type_daily",
        "monthly": "usage_type_monthly",
    },
    "resources": {
        "daily": "resource_daily",
        "monthly": "resource_monthly",
    },
}

COST_METRIC = "net_amortized_cost"


def _next_month_start(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def default_date_window(grain: ReportGrain, now: Optional[datetime] = None) -> tuple[str, str]:
    current = now or datetime.now(timezone.utc)
    today = current.astimezone(timezone.utc).date()
    if grain == "monthly":
        start = date(today.year, today.month, 1)
        return start.isoformat(), _next_month_start(start).isoformat()
    end = today
    start = end - timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _resolve_report_dataset(report: str, grain: ReportGrain) -> str:
    return REPORT_DATASETS[report][grain]


def _filters(
    *,
    account_id: Optional[str] = None,
    service: Optional[str] = None,
    region: Optional[str] = None,
    resource_id: Optional[str] = None,
) -> dict[str, str]:
    values = {
        "account_id": account_id,
        "service": service,
        "region": region,
        "resource_id": resource_id,
    }
    return {key: value for key, value in values.items() if value is not None}


def _total_net_amortized_cost(rows: list[dict]) -> float:
    return float(sum(float(row.get(COST_METRIC) or 0.0) for row in rows))


def _query_report(
    report: Literal["services", "usage_types", "resources"],
    *,
    grain: ReportGrain,
    start_date: Optional[str],
    end_date: Optional[str],
    filters: dict[str, str],
    limit: int,
    offset: int,
    cache_root: str,
):
    dataset = _resolve_report_dataset(report, grain)
    resolved_start_date = start_date
    resolved_end_date = end_date
    if resolved_start_date is None and resolved_end_date is None:
        resolved_start_date, resolved_end_date = default_date_window(grain)

    try:
        rows = query_dataset(
            dataset,
            cache_root=Path(cache_root),
            start_date=resolved_start_date,
            end_date=resolved_end_date,
            filters=filters,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CurCacheMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "report": report,
        "dataset": dataset,
        "grain": grain,
        "start_date": resolved_start_date,
        "end_date": resolved_end_date,
        "filters": filters,
        "cost_metric": COST_METRIC,
        "limit": limit,
        "offset": offset,
        "row_count": len(rows),
        "total_net_amortized_cost": _total_net_amortized_cost(rows),
        "rows": rows,
    }


@router.get("/datasets")
def list_cur_datasets():
    return {
        "datasets": [
            {
                "name": dataset.name,
                "grain": dataset.grain,
                "description": dataset.description,
                "date_column": dataset.date_column,
                "dimensions": dataset.dimensions,
            }
            for dataset in DATASETS.values()
        ]
    }


class RefreshRequest(BaseModel):
    datasets: Optional[List[str]] = None
    start_month: Optional[str] = None
    end_month: Optional[str] = None
    force: bool = False


class PricingToggleRequest(BaseModel):
    enabled: bool


def _background_session(runner, *args, **kwargs) -> None:
    """Run a job with its own database session.

    The request's session is closed once the response is sent, so background
    work cannot borrow it.
    """
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        runner(db, *args, **kwargs)
    finally:
        db.close()


@router.get("/setup/status")
def get_cur_setup_status(
    cache_root: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    """Live state of the cost-data pipeline, probed rather than remembered."""
    status = get_setup_status(Path(cache_root) if cache_root else None)
    status["jobs"] = latest_jobs(db)
    status["running"] = {
        JOB_EXPORT: running_job_of_type(db, JOB_EXPORT),
        JOB_REFRESH: running_job_of_type(db, JOB_REFRESH),
    }
    return status


@router.get("/setup/preflight/{job_type}")
def get_cur_setup_preflight(job_type: str):
    """Whether the current AWS profile can perform a setup action."""
    try:
        return preflight(job_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/setup/estimate/refresh")
def get_cur_refresh_estimate(
    dataset: Optional[List[str]] = Query(default=None),
    start_month: Optional[str] = None,
    end_month: Optional[str] = None,
    force: bool = False,
):
    """How many Athena queries a refresh would run, and roughly what it costs."""
    try:
        return estimate_refresh(
            datasets=dataset or [RESOURCE_COST_DATASET],
            start_month=start_month,
            end_month=end_month,
            force=force,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/setup/export")
def start_cur_export(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Create the CUR export, its bucket, and the Athena table."""
    existing = running_job_of_type(db, JOB_EXPORT)
    if existing:
        raise HTTPException(status_code=409, detail="An export job is already running.")
    job = create_job(db, JOB_EXPORT)
    background_tasks.add_task(_background_session, run_export_job, job.id)
    return {"job_id": job.id, "status": job.status}


@router.post("/setup/refresh")
def start_cur_refresh(
    request: RefreshRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Rebuild the local Parquet cost cache from Athena."""
    existing = running_job_of_type(db, JOB_REFRESH)
    if existing:
        raise HTTPException(status_code=409, detail="A refresh job is already running.")
    job = create_job(db, JOB_REFRESH)
    background_tasks.add_task(
        _background_session,
        run_refresh_job,
        job.id,
        datasets=request.datasets or [RESOURCE_COST_DATASET],
        start_month=request.start_month,
        end_month=request.end_month,
        force=request.force,
    )
    return {"job_id": job.id, "status": job.status}


@router.get("/setup/jobs/{job_id}")
def get_cur_setup_job(job_id: int, db: Session = Depends(get_db)):
    try:
        return get_job(db, job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/setup/pricing")
def set_cur_pricing(request: PricingToggleRequest, db: Session = Depends(get_db)):
    """Turn CUR-based pricing on or off. Takes effect without a restart."""
    try:
        enabled = set_cur_pricing_enabled(db, request.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    cur_cost_lookup.refresh(force=True)
    return {"enabled": enabled, "pricing": cur_cost_lookup.status()}


@router.get("/status")
def get_cur_cache_status(cache_root: str = Query(default=str(DEFAULT_CACHE_ROOT))):
    return {
        "cache_root": cache_root,
        "datasets": cache_status(Path(cache_root)),
        # Whether findings and rightsizing are currently priced from the bill
        # rather than from list prices.
        "pricing": cur_cost_lookup.status(),
    }


@router.get("/reports/services")
def get_service_cost_report(
    grain: ReportGrain = "daily",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    account_id: Optional[str] = None,
    service: Optional[str] = None,
    region: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
    cache_root: str = str(DEFAULT_CACHE_ROOT),
):
    return _query_report(
        "services",
        grain=grain,
        start_date=start_date,
        end_date=end_date,
        filters=_filters(account_id=account_id, service=service, region=region),
        limit=limit,
        offset=offset,
        cache_root=cache_root,
    )


@router.get("/reports/usage-types")
def get_usage_type_cost_report(
    grain: ReportGrain = "daily",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    account_id: Optional[str] = None,
    service: Optional[str] = None,
    region: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
    cache_root: str = str(DEFAULT_CACHE_ROOT),
):
    return _query_report(
        "usage_types",
        grain=grain,
        start_date=start_date,
        end_date=end_date,
        filters=_filters(account_id=account_id, service=service, region=region),
        limit=limit,
        offset=offset,
        cache_root=cache_root,
    )


@router.get("/reports/resources")
def get_resource_cost_report(
    grain: ReportGrain = "daily",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    account_id: Optional[str] = None,
    service: Optional[str] = None,
    region: Optional[str] = None,
    resource_id: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
    cache_root: str = str(DEFAULT_CACHE_ROOT),
):
    return _query_report(
        "resources",
        grain=grain,
        start_date=start_date,
        end_date=end_date,
        filters=_filters(account_id=account_id, service=service, region=region, resource_id=resource_id),
        limit=limit,
        offset=offset,
        cache_root=cache_root,
    )


@router.get("/reports/resources/{resource_id}/cost")
def get_resource_cost_by_id(
    resource_id: str,
    grain: ReportGrain = "daily",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    account_id: Optional[str] = None,
    service: Optional[str] = None,
    region: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
    cache_root: str = str(DEFAULT_CACHE_ROOT),
):
    return _query_report(
        "resources",
        grain=grain,
        start_date=start_date,
        end_date=end_date,
        filters=_filters(account_id=account_id, service=service, region=region, resource_id=resource_id),
        limit=limit,
        offset=offset,
        cache_root=cache_root,
    )


@router.get("/{dataset}")
def get_cur_dataset(
    dataset: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    account_id: Optional[str] = None,
    service: Optional[str] = None,
    region: Optional[str] = None,
    resource_id: Optional[str] = None,
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    cache_root: str = Query(default=str(DEFAULT_CACHE_ROOT)),
):
    if dataset not in DATASETS:
        raise HTTPException(status_code=400, detail=f"Unknown CUR dataset: {dataset}")
    filters = {
        "account_id": account_id,
        "service": service,
        "region": region,
        "resource_id": resource_id,
    }
    try:
        rows = query_dataset(
            dataset,
            cache_root=Path(cache_root),
            start_date=start_date,
            end_date=end_date,
            filters={key: value for key, value in filters.items() if value is not None},
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CurCacheMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "dataset": dataset,
        "start_date": start_date,
        "end_date": end_date,
        "limit": limit,
        "offset": offset,
        "row_count": len(rows),
        "cache_root": cache_root,
        "rows": rows,
    }
