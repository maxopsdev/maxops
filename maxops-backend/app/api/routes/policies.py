"""Policy management API routes."""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas.policy import (
    PolicyCreate,
    PolicyUpdate,
    PolicyResponse,
    PolicyExecutionResponse,
    PolicyExecuteRequest,
    PolicyBatchExecuteRequest,
    PolicyValidationResponse,
    PolicyCostSavingsResponse,
)
from app.services.policy_service import PolicyService

router = APIRouter(prefix="/policies", tags=["policies"])

# Register /executions route FIRST to avoid route matching conflicts
@router.get("/executions", response_model=List[PolicyExecutionResponse], include_in_schema=True)
def list_all_executions(
    skip: int = 0,
    limit: int = 100,
    policy_id: Optional[int] = None,
    status: Optional[str] = None,
    execution_type: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get all executions across all policies with optional filters."""
    service = PolicyService(db)
    executions = service.get_all_executions(
        limit=limit,
        skip=skip,
        policy_id=policy_id,
        status=status,
        execution_type=execution_type
    )
    return executions


@router.post("", response_model=PolicyResponse, status_code=status.HTTP_201_CREATED)
def create_policy(
    policy_data: PolicyCreate,
    db: Session = Depends(get_db)
):
    """Create a new policy."""
    service = PolicyService(db)
    try:
        policy = service.create_policy(policy_data)
        return policy
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("", response_model=List[PolicyResponse])
def list_policies(
    skip: int = 0,
    limit: int = 100,
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """List all policies."""
    service = PolicyService(db)
    policies = service.get_policies(skip=skip, limit=limit, status=status)
    return policies


@router.get("/code/{policy_code}", response_model=PolicyResponse)
def get_policy_by_code(
    policy_code: str,
    db: Session = Depends(get_db)
):
    """Get a policy by policy_code."""
    service = PolicyService(db)
    policy = service.get_policy_by_code(policy_code)
    if not policy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Policy with code {policy_code} not found"
        )
    return policy


@router.get("/{policy_id}", response_model=PolicyResponse)
def get_policy(
    policy_id: int,
    db: Session = Depends(get_db)
):
    """Get a policy by ID."""
    service = PolicyService(db)
    policy = service.get_policy(policy_id)
    if not policy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Policy {policy_id} not found"
        )
    return policy


@router.put("/{policy_id}", response_model=PolicyResponse)
def update_policy(
    policy_id: int,
    policy_data: PolicyUpdate,
    db: Session = Depends(get_db)
):
    """Update a policy."""
    service = PolicyService(db)
    try:
        policy = service.update_policy(policy_id, policy_data)
        if not policy:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Policy {policy_id} not found"
            )
        return policy
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_policy(
    policy_id: int,
    db: Session = Depends(get_db)
):
    """Archive a policy (no permanent delete)."""
    service = PolicyService(db)
    success = service.delete_policy(policy_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Policy {policy_id} not found"
        )


@router.post("/{policy_id}/validate", response_model=PolicyValidationResponse, deprecated=True)
def validate_policy(
    policy_id: int,
    db: Session = Depends(get_db)
):
    """
    DEPRECATED: Validate a policy's YAML definition.

    This endpoint is deprecated. New policies use check_id instead of YAML.
    Please use GET /checks/{check_id} to view check parameters instead.
    """
    service = PolicyService(db)
    policy = service.get_policy(policy_id)
    if not policy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Policy {policy_id} not found"
        )

    # For new check-based policies, validation is not applicable
    if policy.check_id:
        return PolicyValidationResponse(
            valid=True,
            errors=[],
            warnings=["This policy uses check-based approach. No YAML validation needed."]
        )

    # Legacy YAML validation
    if policy.policy_yaml and hasattr(service, 'validator') and service.validator:
        return service.validate_policy(policy.policy_yaml)

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Policy has no YAML to validate"
    )


@router.post("/validate", response_model=PolicyValidationResponse, deprecated=True)
def validate_policy_yaml(
    policy_data: dict,
    db: Session = Depends(get_db)
):
    """
    DEPRECATED: Validate a policy definition (YAML or filters).

    This endpoint is deprecated. New policies use check_id from the registry.
    Please use GET /checks to list available checks and their parameters.
    """
    service = PolicyService(db)

    # Return deprecation notice for new approach
    if "check_id" in policy_data:
        return PolicyValidationResponse(
            valid=True,
            errors=[],
            warnings=["Using new check-based approach. Use GET /checks/{check_id} to view parameters."]
        )

    # Legacy filter validation
    if "filters_json" in policy_data and policy_data.get("filters_json") is not None:
        if hasattr(service, 'validate_filters'):
            resource_type = policy_data.get("resource_type")
            filters = policy_data.get("filters_json", [])

            if not resource_type:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="resource_type is required when validating filters"
                )

            return service.validate_filters(resource_type, filters)

    # Legacy YAML validation
    yaml_str = policy_data.get("policy_yaml", "")
    if yaml_str and hasattr(service, 'validator') and service.validator:
        return service.validate_policy(yaml_str)

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Either check_id (new) or policy_yaml/filters_json (legacy) is required"
    )


@router.post("/{policy_id}/execute", response_model=PolicyExecutionResponse)
def execute_policy(
    policy_id: int,
    request: PolicyExecuteRequest,
    db: Session = Depends(get_db)
):
    """Execute a policy scan by ID (returns resources with cost information)."""
    service = PolicyService(db)
    try:
        execution = service.execute_policy(
            policy_id=policy_id,
            additional_filters=request.filters
        )
        return execution
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/code/{policy_code}/execute", response_model=PolicyExecutionResponse)
def execute_policy_by_code(
    policy_code: str,
    request: PolicyExecuteRequest,
    db: Session = Depends(get_db)
):
    """Execute a policy scan by policy_code (returns resources with cost information)."""
    service = PolicyService(db)
    try:
        execution = service.execute_policy(
            policy_code=policy_code,
            additional_filters=request.filters
        )
        return execution
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("/{policy_id}/executions", response_model=List[PolicyExecutionResponse])
def list_executions(
    policy_id: int,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """Get execution history for a policy."""
    service = PolicyService(db)
    policy = service.get_policy(policy_id)
    if not policy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Policy {policy_id} not found"
        )
    
    executions = service.get_executions(policy_id, limit=limit)
    return executions


@router.get("/executions/{execution_id}", response_model=PolicyExecutionResponse)
def get_execution(
    execution_id: int,
    db: Session = Depends(get_db)
):
    """Get an execution by ID."""
    service = PolicyService(db)
    execution = service.get_execution(execution_id)
    if not execution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution {execution_id} not found"
        )
    return execution


@router.post("/execute-batch", response_model=List[PolicyExecutionResponse])
def execute_batch_policies(
    request: PolicyBatchExecuteRequest,
    db: Session = Depends(get_db)
):
    """Execute multiple policies in batch (scan only, returns resources with cost information)."""
    service = PolicyService(db)
    executions = []
    errors = []
    
    for policy_id in request.policy_ids:
        try:
            execution = service.execute_policy(
                policy_id=policy_id
            )
            executions.append(execution)
        except ValueError as e:
            # Continue with other policies even if one fails
            errors.append(f"Policy {policy_id}: {str(e)}")
            continue
    
    if not executions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No policies were executed successfully. Errors: {', '.join(errors)}"
        )
    
    return executions


@router.get("/{policy_id}/cost-savings", response_model=List[PolicyCostSavingsResponse])
def get_policy_cost_savings(
    policy_id: int,
    db: Session = Depends(get_db)
):
    """Get cost savings history for a policy."""
    from app.models.policy import PolicyCostSavings
    
    service = PolicyService(db)
    policy = service.get_policy(policy_id)
    if not policy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Policy {policy_id} not found"
        )
    
    cost_savings = (
        db.query(PolicyCostSavings)
        .filter(PolicyCostSavings.policy_id == policy_id)
        .order_by(PolicyCostSavings.date.asc())
        .all()
    )
    return cost_savings


@router.get("/executions/{execution_id}/cost-summary")
def get_execution_cost_summary(
    execution_id: int,
    db: Session = Depends(get_db)
):
    """Get cost summary for a specific execution."""
    service = PolicyService(db)
    execution = service.get_execution(execution_id)
    
    if not execution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution {execution_id} not found"
        )
    
    # Extract cost data from results_json
    results_json = execution.results_json or {}
    
    return {
        "execution_id": execution_id,
        "policy_id": execution.policy_id,
        "total_monthly_cost": results_json.get("total_monthly_cost"),
        "cost_by_resource_type": results_json.get("cost_by_resource_type", {}),
        "cost_by_region": results_json.get("cost_by_region", {}),
        "resource_count_by_type": results_json.get("resource_count_by_type", {}),
        "average_cost_per_resource": results_json.get("average_cost_per_resource"),
        "resources_with_cost": results_json.get("resources_with_cost", 0),
        "resources_without_cost": results_json.get("resources_without_cost", 0),
        "total_resources": execution.resources_found
    }


@router.delete("/executions/clear", status_code=status.HTTP_200_OK)
def clear_all_executions(
    db: Session = Depends(get_db)
):
    """
    Clear all historical execution data from the database.
    
    WARNING: This will permanently delete all execution records, execution results, and cost savings data.
    This action cannot be undone.
    """
    from app.models.policy import PolicyExecution, PolicyExecutionResult, PolicyCostSavings
    
    # Count records before deletion
    executions_count = db.query(PolicyExecution).count()
    
    if executions_count == 0:
        return {
            "message": "No execution data to clear",
            "executions_deleted": 0,
            "execution_results_deleted": 0,
            "cost_savings_deleted": 0
        }
    
    # Delete in order to respect foreign key constraints
    # 1. Delete cost savings (references executions)
    cost_savings_deleted = db.query(PolicyCostSavings).delete()
    
    # 2. Delete execution results (references executions)
    execution_results_deleted = db.query(PolicyExecutionResult).delete()
    
    # 3. Delete executions
    executions_deleted = db.query(PolicyExecution).delete()
    
    # Commit the changes
    db.commit()
    
    return {
        "message": "All execution data cleared successfully",
        "executions_deleted": executions_deleted,
        "execution_results_deleted": execution_results_deleted,
        "cost_savings_deleted": cost_savings_deleted
    }


@router.get("/cost-analytics")
def get_cost_analytics(
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    policy_id: Optional[int] = Query(None, description="Filter by policy ID"),
    db: Session = Depends(get_db)
):
    """Get cost analytics across all executions."""
    from datetime import datetime
    
    service = PolicyService(db)
    
    # Build query filters
    query_filters = {}
    if policy_id:
        query_filters['policy_id'] = policy_id
    
    # Get executions
    executions = service.get_all_executions(limit=1000, **query_filters)
    
    # Filter by date if provided
    if start_date:
        start_dt = datetime.fromisoformat(start_date)
        executions = [e for e in executions if e.started_at >= start_dt]
    
    if end_date:
        end_dt = datetime.fromisoformat(end_date)
        executions = [e for e in executions if e.started_at <= end_dt]
    
    # Aggregate cost data
    total_cost = 0.0
    cost_by_type = {}
    cost_by_region = {}
    execution_count = 0
    resource_count = 0
    
    for execution in executions:
        if execution.status != "completed":
            continue
        
        results_json = execution.results_json or {}
        exec_cost = results_json.get("total_monthly_cost", 0) or 0
        
        if exec_cost > 0:
            total_cost += exec_cost
            execution_count += 1
            resource_count += execution.resources_found
            
            # Aggregate by type
            for resource_type, cost in results_json.get("cost_by_resource_type", {}).items():
                cost_by_type[resource_type] = cost_by_type.get(resource_type, 0) + cost
            
            # Aggregate by region
            for region, cost in results_json.get("cost_by_region", {}).items():
                cost_by_region[region] = cost_by_region.get(region, 0) + cost
    
    return {
        "total_monthly_cost": round(total_cost, 2),
        "execution_count": execution_count,
        "total_resources": resource_count,
        "cost_by_resource_type": {k: round(v, 2) for k, v in cost_by_type.items()},
        "cost_by_region": {k: round(v, 2) for k, v in cost_by_region.items()},
        "average_cost_per_execution": round(total_cost / execution_count, 2) if execution_count > 0 else 0,
        "executions": [
            {
                "execution_id": e.id,
                "policy_id": e.policy_id,
                "started_at": e.started_at.isoformat(),
                "total_monthly_cost": (e.results_json or {}).get("total_monthly_cost", 0) or 0,
                "resources_found": e.resources_found
            }
            for e in executions[:50]  # Limit to recent 50
        ]
    }

