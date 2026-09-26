"""Filter registry API routes (DEPRECATED - use checks API instead)."""
from fastapi import APIRouter, HTTPException, status

router = APIRouter(prefix="/policy-filters", tags=["filters"])

# The legacy filter_registry was removed when checks replaced policy filters;
# import defensively so an older tree still starts.
try:
    from app.utils.filter_registry import get_available_filters, get_filter_definition, validate_filter, COMMON_FILTERS
    _FILTER_REGISTRY_AVAILABLE = True
except ImportError:
    _FILTER_REGISTRY_AVAILABLE = False
    get_available_filters = None
    get_filter_definition = None
    validate_filter = None
    COMMON_FILTERS = []


@router.get("/common")
def get_common_filters():
    """Get common filters available for all resource types (DEPRECATED - use /api/v1/checks instead)."""
    if not _FILTER_REGISTRY_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Filter registry API is deprecated. Please use /api/v1/checks endpoint instead."
        )
    return {
        "filters": COMMON_FILTERS,
        "description": "Common filters available for all resource types",
        "deprecated": True,
        "migration_note": "Use /api/v1/checks endpoint for the new check-based approach"
    }


@router.get("/{resource_type}")
def get_filters_for_resource_type(resource_type: str):
    """
    Get all available filters for a specific resource type (DEPRECATED - use /api/v1/checks?resource_type=X instead).
    
    Args:
        resource_type: Resource type (e.g., 'ec2', 'rds')
    """
    if not _FILTER_REGISTRY_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=f"Filter registry API is deprecated. Please use /api/v1/checks?resource_type={resource_type} endpoint instead."
        )
    
    filters = get_available_filters(resource_type)
    
    if not filters:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No filters found for resource type: {resource_type}"
        )
    
    return {
        "resource_type": resource_type,
        "filters": filters,
        "common_count": len([f for f in filters.keys() if f in ["tags", "state", "region", "name"]]),
        "specific_count": len(filters) - len([f for f in filters.keys() if f in ["tags", "state", "region", "name"]]),
        "deprecated": True,
        "migration_note": f"Use /api/v1/checks?resource_type={resource_type} endpoint for the new check-based approach"
    }


@router.get("/{resource_type}/{filter_type}")
def get_filter_definition_endpoint(resource_type: str, filter_type: str):
    """
    Get definition for a specific filter type (DEPRECATED - use /api/v1/checks/{check_id} instead).
    
    Args:
        resource_type: Resource type
        filter_type: Filter type (e.g., 'idle', 'tags')
    """
    if not _FILTER_REGISTRY_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Filter registry API is deprecated. Please use /api/v1/checks endpoint instead."
        )
    
    filter_def = get_filter_definition(resource_type, filter_type)
    
    if not filter_def:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Filter '{filter_type}' not found for resource type '{resource_type}'"
        )
    
    return {
        "resource_type": resource_type,
        "filter_type": filter_type,
        "definition": filter_def,
        "deprecated": True,
        "migration_note": "Use /api/v1/checks endpoint for the new check-based approach"
    }


@router.post("/validate")
def validate_filter_condition(
    resource_type: str,
    filter_data: dict
):
    """
    Validate a filter condition (DEPRECATED - use check-based policies instead).
    
    Body:
        resource_type: Resource type
        filter_data: Filter condition with 'type', 'operator', 'value'
    """
    if not _FILTER_REGISTRY_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Filter validation API is deprecated. Please use check-based policies with /api/v1/policies endpoint instead."
        )
    
    is_valid, error_message = validate_filter(resource_type, filter_data)
    
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_message or "Invalid filter condition"
        )
    
    return {
        "valid": True,
        "message": "Filter condition is valid",
        "deprecated": True,
        "migration_note": "Use check-based policies with /api/v1/policies endpoint instead"
    }

