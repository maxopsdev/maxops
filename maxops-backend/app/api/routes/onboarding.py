"""API routes for onboarding check execution."""
from fastapi import APIRouter, Body, HTTPException, Depends, status
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from pathlib import Path
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import datetime, timezone
from app.database import get_db
from app.checks.registry import check_registry
from app.models.settings import OnboardingExecution, OnboardingCheckResult, CheckFilter
from app.utils.check_filter_applier import apply_check_filters
from app.utils.settings_guard import require_account_region
from app.api.routes.checks import _serialize_metadata, _execute_check_across_settings
from app.services.settings_service import ensure_account_settings
from app.services.settings_service import save_onboarding_iam_role_result
from app.services.iam_onboarding_service import (
    IamRoleCreationError,
    create_or_update_read_only_role,
    list_available_aws_profiles,
    use_existing_read_only_role,
)
from app.startup_checks import get_pricing_db_status, resolve_pricing_db_path
from staging_pricing.unpack_pricing_db import unpack_bundled_pricing_db
from app.utils.resource_snooze import build_exemption_filter_payload

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


class CreateReadOnlyIamRoleRequest(BaseModel):
    profile_name: Optional[str] = None


class UseExistingIamRoleRequest(BaseModel):
    role_arn: str
    profile_name: Optional[str] = None


class UnpackPricingDatabaseRequest(BaseModel):
    force: bool = False


@router.get("/iam/profiles")
def list_iam_profiles():
    """List local AWS profiles and their account IDs when available."""
    return {"profiles": list_available_aws_profiles()}


@router.post("/iam/read-only-role")
def create_read_only_iam_role(
    payload: Optional[CreateReadOnlyIamRoleRequest] = Body(default=None),
    db: Session = Depends(get_db),
):
    """Create or update the MaxOps read-only IAM role for scan checks."""
    try:
        result = create_or_update_read_only_role(profile_name=payload.profile_name if payload else None)
        save_onboarding_iam_role_result(db, result)
        return result
    except IamRoleCreationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.post("/iam/use-existing-role")
def use_existing_iam_role(
    payload: UseExistingIamRoleRequest,
    db: Session = Depends(get_db),
):
    """Register an existing read-only IAM role ARN instead of having MaxOps create one.

    For users whose local AWS credentials can't create IAM resources but who already
    have (or can have someone else create) a role matching the downloadable read-only
    policy. Makes no IAM write calls.
    """
    try:
        result = use_existing_read_only_role(role_arn=payload.role_arn, profile_name=payload.profile_name)
        save_onboarding_iam_role_result(db, result)
        return result
    except IamRoleCreationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.get("/pricing-database")
def get_pricing_database_status():
    """Return bundled pricing database setup status for onboarding UI."""
    return get_pricing_db_status()


@router.post("/pricing-database/unpack")
def unpack_pricing_database(
    payload: Optional[UnpackPricingDatabaseRequest] = Body(default=None),
):
    """Explicitly unpack the bundled pricing database artifact on the local machine."""
    try:
        result = unpack_bundled_pricing_db(
            artifact_dir=(Path(__file__).resolve().parents[3] / "pricing_artifacts"),
            db_path=resolve_pricing_db_path(),
            force=payload.force if payload else False,
        )
        return {
            "status": get_pricing_db_status(),
            "result": result,
        }
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.post("/run-all-checks")
def run_all_checks(db: Session = Depends(get_db)):
    """
    Run all checks sequentially for onboarding.
    
    Returns a list of check execution results with status.
    Results are saved to the database for later review.
    """
    # Require configured scope for running checks
    try:
        settings = require_account_region(db)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc)
        )
    
    # Get all checks
    all_checks = check_registry.list_checks()
    
    if not all_checks:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No checks available"
        )
    
    # Create execution record
    execution = OnboardingExecution(
        settings_id=settings.id,
        status="running",
        total_checks=len(all_checks),
        completed_checks=0,
        failed_checks=0
    )
    db.add(execution)
    db.flush()  # Get the execution ID
    
    results = []
    
    try:
        for check in all_checks:
            check_result = OnboardingCheckResult(
                execution_id=execution.id,
                check_id=check.check_id,
                name=check.name,
                description=check.description,
                resource_type=check.resource_type,
                status="running",
                resources_found=0
            )
            db.add(check_result)
            db.flush()
            
            try:
                resources = _execute_check_across_settings(
                    check_id=check.check_id,
                    parameters=None,
                    user_settings=settings,
                    db=db,
                )
                
                exemption_list = build_exemption_filter_payload(db, check.check_id)

                # Apply check filters and global/check-specific exemptions if configured
                check_filter = db.query(CheckFilter).filter(CheckFilter.check_id == check.check_id).first()
                if check_filter and check_filter.filters_json:
                    resources = apply_check_filters(resources, check_filter.filters_json, check.resource_type, exemption_list)
                elif exemption_list:
                    resources = apply_check_filters(resources, [], check.resource_type, exemption_list)
                
                check_result.status = "completed"
                check_result.resources_found = len(resources)
                check_result.error = None
                
                # Calculate total potential savings from resources (convert monthly to yearly if needed)
                total_savings = sum(
                    resource.get('metadata', {}).get('potential_savings_yearly', 
                        resource.get('metadata', {}).get('potential_savings_monthly', 0) * 12)
                    for resource in resources
                )
                
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

                # Store savings and resources in metadata_json
                check_result.metadata_json = {
                    "potential_savings_yearly": round(total_savings, 6),
                    "resources": stored_resources  # Store full resource list
                }
                
                execution.completed_checks += 1
                
                results.append({
                    "check_id": check.check_id,
                    "name": check.name,
                    "description": check.description,
                    "resource_type": check.resource_type,
                    "status": "completed",
                    "resources_found": len(resources),
                    "potential_savings_monthly": round(total_savings, 6),
                    "error": None
                })
            except Exception as e:
                check_result.status = "failed"
                check_result.resources_found = 0
                check_result.error = str(e)
                execution.failed_checks += 1
                
                results.append({
                    "check_id": check.check_id,
                    "name": check.name,
                    "description": check.description,
                    "resource_type": check.resource_type,
                    "status": "failed",
                    "resources_found": 0,
                    "error": str(e)
                })
            
            db.commit()
        
        # Update execution status
        execution.status = "completed"
        execution.completed_at = datetime.now(timezone.utc)
        execution.results_json = {
            "total_checks": len(all_checks),
            "completed": execution.completed_checks,
            "failed": execution.failed_checks,
            "results": results
        }
        db.commit()
        
    except Exception as e:
        execution.status = "failed"
        execution.error_message = str(e)
        execution.completed_at = datetime.now(timezone.utc)
        db.commit()
        raise
    
    return {
        "execution_id": execution.id,
        "total_checks": len(all_checks),
        "completed": execution.completed_checks,
        "failed": execution.failed_checks,
        "results": results
    }


@router.get("/executions")
def get_onboarding_executions(
    limit: int = 10,
    db: Session = Depends(get_db)
):
    """Get onboarding execution history."""
    executions = db.query(OnboardingExecution).order_by(desc(OnboardingExecution.started_at)).limit(limit).all()
    
    return [
        {
            "id": exec.id,
            "status": exec.status,
            "total_checks": exec.total_checks,
            "completed_checks": exec.completed_checks,
            "failed_checks": exec.failed_checks,
            "started_at": exec.started_at.isoformat() if exec.started_at else None,
            "completed_at": exec.completed_at.isoformat() if exec.completed_at else None,
        }
        for exec in executions
    ]




@router.post("/save-results")
def save_onboarding_results(
    results: List[Dict[str, Any]],
    db: Session = Depends(get_db)
):
    """
    Save onboarding check execution results from frontend.
    
    This endpoint allows the frontend to save results after parallel execution.
    """
    # Get user settings
    settings = ensure_account_settings(db)
    if not settings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Onboarding not completed. Please complete onboarding first."
        )
    
    # Create execution record
    execution = OnboardingExecution(
        settings_id=settings.id,
        status="completed",
        total_checks=len(results),
        completed_checks=sum(1 for r in results if r.get("status") == "completed"),
        failed_checks=sum(1 for r in results if r.get("status") == "failed"),
        completed_at=datetime.now(timezone.utc),
        results_json={"results": results}
    )
    db.add(execution)
    db.flush()
    
    # Save individual check results
    for result in results:
        check_result = OnboardingCheckResult(
            execution_id=execution.id,
            check_id=result.get("check_id", ""),
            name=result.get("name", ""),
            description=result.get("description", ""),
            resource_type=result.get("resource_type", ""),
            status=result.get("status", "failed"),
            resources_found=result.get("resources_found", 0),
            error=result.get("error")
        )
        db.add(check_result)
    
    db.commit()
    
    return {
        "execution_id": execution.id,
        "total_checks": execution.total_checks,
        "completed_checks": execution.completed_checks,
        "failed_checks": execution.failed_checks,
    }


@router.get("/executions/{execution_id}")
def get_onboarding_execution(
    execution_id: int,
    db: Session = Depends(get_db)
):
    """Get detailed results for a specific onboarding execution."""
    execution = db.query(OnboardingExecution).filter(OnboardingExecution.id == execution_id).first()
    
    if not execution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Onboarding execution not found"
        )
    
    check_results = db.query(OnboardingCheckResult).filter(
        OnboardingCheckResult.execution_id == execution_id
    ).all()
    
    return {
        "id": execution.id,
        "status": execution.status,
        "total_checks": execution.total_checks,
        "completed_checks": execution.completed_checks,
        "failed_checks": execution.failed_checks,
        "started_at": execution.started_at.isoformat() if execution.started_at else None,
        "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
        "error_message": execution.error_message,
        "results": [
            {
                "check_id": cr.check_id,
                "name": cr.name,
                "description": cr.description,
                "resource_type": cr.resource_type,
                "status": cr.status,
                "resources_found": cr.resources_found,
                    "potential_savings_yearly": cr.metadata_json.get("potential_savings_yearly", 
                        cr.metadata_json.get("potential_savings_monthly", 0) * 12 if cr.metadata_json else 0) if cr.metadata_json else 0,
                "error": cr.error,
            }
            for cr in check_results
        ]
    }
