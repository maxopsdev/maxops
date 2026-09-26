"""API routes for check operations."""
import logging
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone
from app.schemas.check import (
    CheckMetadataResponse,
    CheckTestRequest,
    CheckTestResponse,
    CheckActionRequest,
    CheckActionResponse,
)
from app.checks.registry import check_registry
from app.adapters.aws.adapter import AWSAdapter
from app.services.aws_pricing_cache import AwsPricingCacheService
from app.database import get_db
from app.models.settings import AccountSettings, OnboardingCheckResult, OnboardingExecution, CheckFilter, ResourceExemption
from app.utils.check_filter_applier import apply_check_filters
from app.utils.resource_snooze import build_exemption_filter_payload, get_excluded_resource_ids
from app.utils.settings_guard import require_account_region, get_settings_scope, get_settings_regions
from app.api.routes.action_helpers import execute_action_for_check
from app.pricing.base import PricingContext
from app.pricing import pricing_registry
from app.services.settings_service import ensure_account_settings, get_effective_check_parameters
from app.services.inventory_service import (
    get_imported_check_resources,
    get_imported_check_summaries,
    summarize_imported_check_history,
)

router = APIRouter()
logger = logging.getLogger("uvicorn.error")


def _build_pricing_context(check_id: str, resources: List[Dict[str, Any]], db: Session) -> Optional[PricingContext]:
    """Build pricing context with check-specific parameters from registry."""
    # Get pricing metadata from registry
    pricing_metadata = pricing_registry.get_pricing(check_id)
    if not pricing_metadata:
        return None  # No pricing registered for this check
    
    # Build context with parameters from registry
    return PricingContext(
        check_id=check_id,
        resources=resources,
        db=db,
        savings_ratio=pricing_metadata.parameters.get("savings_ratio"),
        full_savings=pricing_metadata.parameters.get("full_savings"),
        metadata={"note": pricing_metadata.parameters.get("note")} if pricing_metadata.parameters.get("note") else None,
    )


def _serialize_metadata(value: Any) -> Any:
    """Ensure metadata is JSON-serializable (datetimes -> ISO strings)."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _serialize_metadata(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize_metadata(v) for v in value]
    return value


def _dedupe_resources(resources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()

    for resource in resources:
        key = (
            str(resource.get("resource_type") or ""),
            str(resource.get("resource_id") or ""),
            str(resource.get("region") or ""),
            str(resource.get("account_id") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(resource)

    return deduped


def _execute_check_across_settings(
    check_id: str,
    parameters: Optional[Dict[str, Any]],
    user_settings: AccountSettings,
    db: Optional[Session] = None,
) -> List[Dict[str, Any]]:
    account, _ = get_settings_scope(user_settings)
    regions = get_settings_regions(user_settings)
    if not regions:
        return []

    resolved_parameters = (
        get_effective_check_parameters(db, check_id, parameters)
        if db is not None
        else parameters or {}
    )

    normalized_resources: List[Dict[str, Any]] = []
    for region in regions:
        aws_adapter = AWSAdapter(default_region=region)
        resources = check_registry.execute_check(
            check_id=check_id,
            aws_adapter=aws_adapter,
            parameters=resolved_parameters,
        )

        for resource in resources:
            normalized_resource = dict(resource)
            if not normalized_resource.get("region"):
                normalized_resource["region"] = region
            if not normalized_resource.get("account_id") and account:
                normalized_resource["account_id"] = account
            normalized_resources.append(normalized_resource)

    return _dedupe_resources(normalized_resources)


def _recalculate_latest_check_result(db: Session, check_id: str) -> None:
    """Recalculate savings and resource count for the latest check result."""
    latest_result = (
        db.query(OnboardingCheckResult, OnboardingExecution.completed_at, OnboardingExecution.started_at)
        .join(OnboardingExecution, OnboardingCheckResult.execution_id == OnboardingExecution.id)
        .filter(OnboardingCheckResult.check_id == check_id)
        .filter(
            (OnboardingExecution.completed_at.isnot(None)) |
            (OnboardingExecution.started_at.isnot(None))
        )
        .order_by(
            func.coalesce(OnboardingExecution.completed_at, OnboardingExecution.started_at).desc()
        )
        .first()
    )

    if not latest_result:
        return

    check_result, _, _ = latest_result
    if not check_result.metadata_json or "resources" not in check_result.metadata_json:
        return

    resources = check_result.metadata_json.get("resources", [])
    excluded_ids = get_excluded_resource_ids(db, check_id)

    filtered_resources = [
        r for r in resources
        if r.get("resource_id") not in excluded_ids
    ]

    total_savings = sum(
        r.get("metadata", {}).get("potential_savings_yearly", 0) or
        (r.get("metadata", {}).get("potential_savings_monthly", 0) * 12)
        for r in filtered_resources
    )

    check_result.resources_found = len(filtered_resources)
    check_result.metadata_json["potential_savings_yearly"] = round(total_savings, 6)
    db.commit()


@router.get("/checks", response_model=List[CheckMetadataResponse])
def list_checks(resource_type: Optional[str] = None):
    """
    List all available checks.

    Args:
        resource_type: Optional filter by resource type (ec2, rds, ebs, snapshot)

    Returns:
        List of check metadata
    """
    checks = check_registry.list_checks(resource_type)

    return [
        CheckMetadataResponse(
            check_id=check.check_id,
            name=check.name,
            description=check.description,
            resource_type=check.resource_type,
            default_action=check.default_action,
            parameters=check.parameters
        )
        for check in checks
    ]


# IMPORTANT: These routes must come BEFORE /checks/{check_id} to avoid route conflicts
@router.get("/checks/last-runs")
def get_check_last_runs(db: Session = Depends(get_db)):
    """
    Get the last run time for each check.
    
    Returns a dictionary mapping check_id to the most recent execution timestamp.
    """
    # Query the most recent OnboardingCheckResult for each check_id
    # Join with OnboardingExecution to get the completed_at timestamp
    results = (
        db.query(
            OnboardingCheckResult.check_id,
            func.max(OnboardingExecution.completed_at).label('last_run')
        )
        .join(OnboardingExecution, OnboardingCheckResult.execution_id == OnboardingExecution.id)
        .filter(OnboardingExecution.completed_at.isnot(None))
        .filter(OnboardingCheckResult.status == 'completed')
        .group_by(OnboardingCheckResult.check_id)
        .all()
    )
    
    # Convert to dictionary
    last_runs = {
        row.check_id: row.last_run.isoformat() if row.last_run else None
        for row in results
    }
    
    imported_summaries = get_imported_check_summaries(db)
    for check_id, summary in imported_summaries.items():
        imported_time = summary.execution_time.isoformat() if summary.execution_time else None
        if check_id not in last_runs:
            last_runs[check_id] = imported_time
            continue
        if imported_time and (last_runs[check_id] is None or imported_time > last_runs[check_id]):
            last_runs[check_id] = imported_time

    return last_runs


@router.get("/checks/latest-results")
def get_latest_check_results(db: Session = Depends(get_db)):
    """
    Get the latest result for each check across all executions.
    
    Returns a dictionary mapping check_id to the most recent check result.
    Includes both completed and failed checks.
    """
    # Get all check results with their execution timestamps
    # Order by execution completed_at descending to get the latest first
    all_results = (
        db.query(OnboardingCheckResult, OnboardingExecution.completed_at, OnboardingExecution.started_at)
        .join(OnboardingExecution, OnboardingCheckResult.execution_id == OnboardingExecution.id)
        .filter(
            (OnboardingExecution.completed_at.isnot(None)) | 
            (OnboardingExecution.started_at.isnot(None))
        )
        .order_by(
            func.coalesce(OnboardingExecution.completed_at, OnboardingExecution.started_at).desc()
        )
        .all()
    )
    
    # Build response dictionary, keeping only the latest result for each check_id
    latest_results = {}
    seen_check_ids = set()
    
    for check_result, completed_at, started_at in all_results:
        if check_result.check_id not in seen_check_ids:
            seen_check_ids.add(check_result.check_id)
            execution_time = completed_at if completed_at else started_at
            
            # Extract resource count and potential savings, excluding exempted/snoozed resources
            resource_count = check_result.resources_found
            potential_savings_yearly = 0
            if check_result.metadata_json and "resources" in check_result.metadata_json:
                resources = check_result.metadata_json.get("resources", [])

                # Exclude check-specific exemptions and global resource snoozes.
                excluded_ids = get_excluded_resource_ids(db, check_result.check_id)

                filtered_resources = [
                    r for r in resources
                    if r.get("resource_id") not in excluded_ids
                ]

                resource_count = len(filtered_resources)
                potential_savings_yearly = sum(
                    r.get("metadata", {}).get("potential_savings_yearly", 0) or
                    (r.get("metadata", {}).get("potential_savings_monthly", 0) * 12)
                    for r in filtered_resources
                )
            elif check_result.metadata_json:
                potential_savings_yearly = check_result.metadata_json.get(
                    "potential_savings_yearly",
                    check_result.metadata_json.get("potential_savings_monthly", 0) * 12
                )
            
            latest_results[check_result.check_id] = {
                "check_id": check_result.check_id,
                "name": check_result.name,
                "description": check_result.description,
                "resource_type": check_result.resource_type,
                "status": check_result.status,
                "resources_found": resource_count,
                "potential_savings_yearly": round(potential_savings_yearly, 6),
                "error": check_result.error,
                "execution_time": execution_time.isoformat() if execution_time else None,
            }
    
    imported_summaries = get_imported_check_summaries(db)
    for check_id, summary in imported_summaries.items():
        imported_time = summary.execution_time.isoformat() if summary.execution_time else None
        existing = latest_results.get(check_id)
        if existing and imported_time and existing.get("execution_time") and existing["execution_time"] >= imported_time:
            continue
        latest_results[check_id] = {
            "check_id": summary.check_id,
            "name": summary.name,
            "description": summary.description,
            "resource_type": summary.resource_type,
            "status": summary.status,
            "resources_found": summary.resources_found,
            "potential_savings_yearly": round(summary.potential_savings_yearly, 6),
            "error": None,
            "execution_time": imported_time,
        }

    return latest_results


@router.get("/checks/{check_id}", response_model=CheckMetadataResponse)
def get_check(check_id: str):
    """
    Get metadata for a specific check.

    Args:
        check_id: Check identifier

    Returns:
        Check metadata

    Raises:
        HTTPException: If check not found
    """
    check = check_registry.get_check(check_id)
    if not check:
        raise HTTPException(status_code=404, detail=f"Check '{check_id}' not found")

    return CheckMetadataResponse(
        check_id=check.check_id,
        name=check.name,
        description=check.description,
        resource_type=check.resource_type,
        default_action=check.default_action,
        parameters=check.parameters
    )


@router.get("/checks/{check_id}/savings-history")
def get_check_savings_history(check_id: str, db: Session = Depends(get_db)):
    """
    Get historical savings data for a check over time.
    
    Returns a list of savings data points with timestamps for trend analysis.
    """
    # Get all completed check results for this check_id, ordered by execution time
    history = (
        db.query(OnboardingCheckResult, OnboardingExecution.completed_at, OnboardingExecution.started_at)
        .join(OnboardingExecution, OnboardingCheckResult.execution_id == OnboardingExecution.id)
        .filter(OnboardingCheckResult.check_id == check_id)
        .filter(OnboardingCheckResult.status == 'completed')
        .filter(
            (OnboardingExecution.completed_at.isnot(None)) | 
            (OnboardingExecution.started_at.isnot(None))
        )
        .order_by(
            func.coalesce(OnboardingExecution.completed_at, OnboardingExecution.started_at).asc()
        )
        .all()
    )
    
    # Build response with savings and timestamps
    data_points = []
    for check_result, completed_at, started_at in history:
        execution_time = completed_at if completed_at else started_at
        if not execution_time:
            continue
            
        # Extract savings (convert monthly to yearly if needed)
        savings = 0
        if check_result.metadata_json:
            savings = check_result.metadata_json.get("potential_savings_yearly",
                check_result.metadata_json.get("potential_savings_monthly", 0) * 12)
        
        data_points.append({
            "timestamp": execution_time.isoformat(),
            "savings": round(savings, 2),
            "resources_found": check_result.resources_found
        })
    
    if not data_points:
        data_points = summarize_imported_check_history(db, check_id)

    return {
        "check_id": check_id,
        "data_points": data_points
    }


@router.get("/checks/{check_id}/filters")
def get_check_filters(check_id: str, db: Session = Depends(get_db)):
    """
    Get filters configured for a specific check.
    
    Returns the filter configuration for the check, or empty list if none configured.
    """
    check_filter = db.query(CheckFilter).filter(CheckFilter.check_id == check_id).first()
    
    if not check_filter:
        return {
            "check_id": check_id,
            "filters": []
        }
    
    return {
        "check_id": check_id,
        "filters": check_filter.filters_json or [],
        "updated_at": check_filter.updated_at.isoformat() if check_filter.updated_at else None
    }


@router.put("/checks/{check_id}/filters")
def save_check_filters(
    check_id: str,
    filters: List[Dict[str, Any]],
    db: Session = Depends(get_db)
):
    """
    Save filters for a specific check.
    
    Args:
        check_id: Check identifier
        filters: List of filter conditions in format:
            [
                {
                    "type": "tags",
                    "operator": "contains",
                    "key": "Environment",
                    "value": "Production"
                },
                {
                    "type": "region",
                    "operator": "equals",
                    "value": "us-east-1"
                }
            ]
    """
    # Validate check exists
    check = check_registry.get_check(check_id)
    if not check:
        raise HTTPException(status_code=404, detail=f"Check '{check_id}' not found")
    
    # Get or create filter record
    check_filter = db.query(CheckFilter).filter(CheckFilter.check_id == check_id).first()
    
    if check_filter:
        check_filter.filters_json = filters
        check_filter.updated_at = datetime.now(timezone.utc)
    else:
        check_filter = CheckFilter(
            check_id=check_id,
            filters_json=filters
        )
        db.add(check_filter)
    
    db.commit()
    db.refresh(check_filter)
    
    return {
        "check_id": check_id,
        "filters": check_filter.filters_json,
        "updated_at": check_filter.updated_at.isoformat() if check_filter.updated_at else None
    }


@router.delete("/checks/{check_id}/filters")
def delete_check_filters(check_id: str, db: Session = Depends(get_db)):
    """
    Delete filters for a specific check.
    """
    check_filter = db.query(CheckFilter).filter(CheckFilter.check_id == check_id).first()
    
    if check_filter:
        db.delete(check_filter)
        db.commit()
    
    return {
        "check_id": check_id,
        "message": "Filters deleted successfully"
    }


@router.post("/checks/{check_id}/resources/{resource_id}/snooze")
def snooze_resource(
    check_id: str,
    resource_id: str,
    days: int = Query(..., description="Number of days to snooze"),
    db: Session = Depends(get_db)
):
    """
    Snooze a resource for a specific number of days.
    
    Args:
        check_id: Check identifier
        resource_id: Resource identifier
        days: Number of days to snooze (resource will be excluded from checks until this date)
    """
    # Validate check exists
    check = check_registry.get_check(check_id)
    if not check:
        raise HTTPException(status_code=404, detail=f"Check '{check_id}' not found")
    
    # Validate days parameter
    if days < 1:
        raise HTTPException(status_code=400, detail="days must be a positive integer")

    # Calculate snooze until date
    from datetime import timedelta
    snoozed_until = datetime.now(timezone.utc) + timedelta(days=days)
    
    # Get or create exemption record
    exemption = db.query(ResourceExemption).filter(
        ResourceExemption.check_id == check_id,
        ResourceExemption.resource_id == resource_id
    ).first()
    
    if exemption:
        exemption.snoozed_until = snoozed_until
        exemption.snooze_days = days
        exemption.exempted = False  # Clear exemption if snoozing
        exemption.updated_at = datetime.now(timezone.utc)
    else:
        exemption = ResourceExemption(
            check_id=check_id,
            resource_id=resource_id,
            resource_type=check.resource_type,
            exempted=False,
            snoozed_until=snoozed_until,
            snooze_days=days
        )
        db.add(exemption)
    
    db.commit()
    db.refresh(exemption)

    # Update latest check result savings/counts
    _recalculate_latest_check_result(db, check_id)
    
    return {
        "check_id": check_id,
        "resource_id": resource_id,
        "snoozed_until": exemption.snoozed_until.isoformat() if exemption.snoozed_until else None,
        "snooze_days": exemption.snooze_days,
        "message": f"Resource snoozed for {days} days"
    }


@router.post("/checks/{check_id}/resources/{resource_id}/snooze/cancel")
def cancel_snooze_resource(
    check_id: str,
    resource_id: str,
    db: Session = Depends(get_db)
):
    """
    Cancel a snooze for a resource.
    
    Args:
        check_id: Check identifier
        resource_id: Resource identifier
    """
    # Validate check exists
    check = check_registry.get_check(check_id)
    if not check:
        raise HTTPException(status_code=404, detail=f"Check '{check_id}' not found")
    
    exemption = db.query(ResourceExemption).filter(
        ResourceExemption.check_id == check_id,
        ResourceExemption.resource_id == resource_id
    ).first()
    
    if not exemption or not exemption.snoozed_until:
        return {
            "check_id": check_id,
            "resource_id": resource_id,
            "snoozed_until": None,
            "snooze_days": None,
            "message": "Resource is not currently snoozed"
        }
    
    exemption.snoozed_until = None
    exemption.snooze_days = None
    exemption.updated_at = datetime.now(timezone.utc)
    
    db.commit()
    db.refresh(exemption)
    
    # Update latest check result savings/counts
    _recalculate_latest_check_result(db, check_id)
    
    return {
        "check_id": check_id,
        "resource_id": resource_id,
        "snoozed_until": None,
        "snooze_days": None,
        "message": "Snooze cancelled"
    }


@router.post("/checks/{check_id}/resources/{resource_id}/exempt")
def exempt_resource(
    check_id: str,
    resource_id: str,
    exempted: bool = Query(..., description="True to exempt, False to remove exemption"),
    db: Session = Depends(get_db)
):
    """
    Exempt or un-exempt a resource from a check.
    
    Args:
        check_id: Check identifier
        resource_id: Resource identifier
        exempted: True to exempt, False to remove exemption
    """
    # Validate check exists
    check = check_registry.get_check(check_id)
    if not check:
        raise HTTPException(status_code=404, detail=f"Check '{check_id}' not found")
    
    # Get or create exemption record
    exemption = db.query(ResourceExemption).filter(
        ResourceExemption.check_id == check_id,
        ResourceExemption.resource_id == resource_id
    ).first()
    
    if exemption:
        exemption.exempted = exempted
        if exempted:
            # Clear snooze when exempting
            exemption.snoozed_until = None
            exemption.snooze_days = None
        exemption.updated_at = datetime.now(timezone.utc)
    else:
        exemption = ResourceExemption(
            check_id=check_id,
            resource_id=resource_id,
            resource_type=check.resource_type,
            exempted=exempted,
            snoozed_until=None,
            snooze_days=None
        )
        db.add(exemption)
    
    db.commit()
    db.refresh(exemption)

    # Update latest check result savings/counts
    _recalculate_latest_check_result(db, check_id)
    
    return {
        "check_id": check_id,
        "resource_id": resource_id,
        "exempted": exemption.exempted,
        "message": f"Resource {'exempted' if exempted else 'un-exempted'} successfully"
    }


@router.get("/checks/{check_id}/resources/{resource_id}/exemption")
def get_resource_exemption(
    check_id: str,
    resource_id: str,
    db: Session = Depends(get_db)
):
    """
    Get exemption status for a resource.
    
    Returns:
        Exemption status including exempted flag and snooze information
    """
    exemption = db.query(ResourceExemption).filter(
        ResourceExemption.check_id == check_id,
        ResourceExemption.resource_id == resource_id
    ).first()
    
    if not exemption:
        return {
            "check_id": check_id,
            "resource_id": resource_id,
            "exempted": False,
            "snoozed_until": None,
            "snooze_days": None
        }
    
    return {
        "check_id": check_id,
        "resource_id": resource_id,
        "exempted": exemption.exempted,
        "snoozed_until": exemption.snoozed_until.isoformat() if exemption.snoozed_until else None,
        "snooze_days": exemption.snooze_days
    }


@router.get("/checks/{check_id}/resources")
def get_check_resources(check_id: str, db: Session = Depends(get_db)):
    """
    Get the stored resource details for the latest run of a check.
    
    Returns the full resource list from the most recent check execution.
    """
    # Get the latest check result for this check_id
    latest_result = (
        db.query(OnboardingCheckResult, OnboardingExecution.completed_at)
        .join(OnboardingExecution, OnboardingCheckResult.execution_id == OnboardingExecution.id)
        .filter(OnboardingCheckResult.check_id == check_id)
        .filter(OnboardingCheckResult.status == 'completed')
        .filter(OnboardingExecution.completed_at.isnot(None))
        .order_by(OnboardingExecution.completed_at.desc())
        .first()
    )
    
    imported_payload = get_imported_check_resources(db, check_id)
    imported_time = imported_payload.get("execution_time")

    if not latest_result:
        return imported_payload
    
    check_result, completed_at = latest_result
    
    # Extract resources from metadata_json
    resources = []
    if check_result.metadata_json and "resources" in check_result.metadata_json:
        resources = check_result.metadata_json["resources"]
        excluded_ids = get_excluded_resource_ids(db, check_id)
        resources = [resource for resource in resources if resource.get("resource_id") not in excluded_ids]

    latest_execution_time = completed_at.isoformat() if completed_at else None

    if imported_time and (not latest_execution_time or imported_time > latest_execution_time):
        return imported_payload

    if resources:
        pricing_service = AwsPricingCacheService()
        pricing_map = pricing_service.get_prices_for_resources(db, resources)
        for resource in resources:
            resource_id = resource.get("resource_id")
            if resource_id and resource_id in pricing_map:
                resource["pricing"] = pricing_map[resource_id]
                metadata = resource.get("metadata")
                if isinstance(metadata, dict):
                    metadata["pricing"] = pricing_map[resource_id]
        
        # Apply check-specific pricing using factory pattern
        pricing_context = _build_pricing_context(check_id, resources, db)
        if pricing_context:
            pricing_registry.apply_pricing(pricing_context)

    return {
        "check_id": check_id,
        "resources": resources,
        "resources_found": len(resources),
        "execution_time": latest_execution_time
    }


@router.post("/checks/{check_id}/test", response_model=CheckTestResponse)
def test_check(
    check_id: str,
    test_request: CheckTestRequest = CheckTestRequest(),
    db: Session = Depends(get_db)
):
    """
    Test a check without creating a policy (dry-run only).

    Args:
        check_id: Check identifier
        test_request: Test parameters
        db: Database session

    Returns:
        Test results with resources found

    Raises:
        HTTPException: If check not found or execution fails
    """
    # Verify check exists
    check = check_registry.get_check(check_id)
    if not check:
        raise HTTPException(status_code=404, detail=f"Check '{check_id}' not found")

    try:
        # Require configured scope for running checks
        settings = require_account_region(db)
        resources = _execute_check_across_settings(
            check_id=check_id,
            parameters=test_request.parameters or check.parameters,
            user_settings=settings,
            db=db,
        )
        
        exemption_list = build_exemption_filter_payload(db, check_id)
        
        # Apply check filters and exemptions if configured
        check_filter = db.query(CheckFilter).filter(CheckFilter.check_id == check_id).first()
        if check_filter and check_filter.filters_json:
            resources = apply_check_filters(resources, check_filter.filters_json, check.resource_type, exemption_list)
        elif exemption_list:
            resources = apply_check_filters(resources, [], check.resource_type, exemption_list)

        # Initialize total_savings to 0
        total_savings = 0.0

        # Save check result to database for tracking
        try:
            if resources:
                pricing_service = AwsPricingCacheService()
                pricing_map = pricing_service.get_prices_for_resources(db, resources)
                for resource in resources:
                    resource_id = resource.get("resource_id")
                    if resource_id and resource_id in pricing_map:
                        resource["pricing"] = pricing_map[resource_id]
                        metadata = resource.get("metadata")
                        if isinstance(metadata, dict):
                            metadata["pricing"] = pricing_map[resource_id]
                
                # Apply check-specific pricing using factory pattern
                pricing_context = _build_pricing_context(check_id, resources, db)
                if pricing_context:
                    pricing_registry.apply_pricing(pricing_context)
                
                # Calculate total potential savings AFTER pricing is applied
                # Calculate total potential savings from resources (convert monthly to yearly if needed)
                total_savings = sum(
                    resource.get('metadata', {}).get('potential_savings_yearly',
                        resource.get('metadata', {}).get('potential_savings_monthly', 0) * 12)
                    for resource in resources
                )
                logger.info(f"Total savings calculated for check {check_id}: ${total_savings:.2f}/year from {len(resources)} resources")

            # Get user settings
            settings = ensure_account_settings(db)
            if settings:
                # Create a single-check execution record
                execution = OnboardingExecution(
                    settings_id=settings.id,
                    status="completed",
                    total_checks=1,
                    completed_checks=1,
                    failed_checks=0,
                    completed_at=datetime.now(timezone.utc),
                    results_json={"check_id": check_id, "resources_found": len(resources)}
                )
                db.add(execution)
                db.flush()

                # Prepare resources for storage (simplify to essential fields)
                stored_resources = []
                for resource in resources:
                    stored_resource = {
                        "resource_id": resource.get("resource_id", ""),
                        "resource_type": resource.get("resource_type", ""),
                        "resource_name": resource.get("resource_name"),
                        "region": resource.get("region"),
                        "account_id": resource.get("account_id"),
                        "tags": resource.get("tags"),
                        "metadata": _serialize_metadata(resource.get("metadata", {})),
                        "pricing": resource.get("pricing"),  # Include pricing
                    }
                    stored_resources.append(stored_resource)

                # Save check result with full resource details
                check_result = OnboardingCheckResult(
                    execution_id=execution.id,
                    check_id=check_id,
                    name=check.name,
                    description=check.description,
                    resource_type=check.resource_type,
                    status="completed",
                    resources_found=len(resources),
                    error=None,
                    metadata_json={
                        "potential_savings_yearly": round(total_savings, 6) if total_savings else 0.0,
                        "resources": stored_resources  # Store full resource list
                    }
                )
                db.add(check_result)
                db.commit()
        except Exception as save_error:
            # Don't fail the check execution if saving fails
            # Just log and continue
            print(f"Warning: Failed to save check result to database: {save_error}")
            db.rollback()

        return CheckTestResponse(
            check_id=check_id,
            resources_found=len(resources),
            resources=resources,
            potential_savings_yearly=round(total_savings, 6) if total_savings else 0.0
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to execute check: {str(e)}"
        )


@router.post("/checks/{check_id}/actions", response_model=CheckActionResponse)
def execute_check_action(
    check_id: str,
    payload: CheckActionRequest,
    db: Session = Depends(get_db),
):
    """
    Run an action against a real resource using explicit inputs.
    """
    check = check_registry.get_check(check_id)
    if not check:
        raise HTTPException(status_code=404, detail=f"Check '{check_id}' not found")
    return execute_action_for_check(check_id, payload, check, db)
