"""Pydantic schemas for policy-related APIs."""
from pydantic import BaseModel, Field, model_validator
from typing import Optional, List, Dict, Any
from datetime import datetime


class PolicyBase(BaseModel):
    """Base policy schema."""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    check_id: Optional[str] = Field(None, min_length=1, max_length=100)  # Check function ID (new approach)
    parameters_json: Optional[Dict[str, Any]] = None  # Check-specific parameters (new approach)
    policy_yaml: Optional[str] = None  # DEPRECATED - kept for backward compatibility
    filters_json: Optional[List[Dict[str, Any]]] = None  # DEPRECATED - kept for backward compatibility
    resource_type: Optional[str] = Field(None, min_length=1, max_length=100)  # Auto-filled from check metadata
    status: str = Field(default="active", pattern="^(active|inactive)$")


class PolicyCreate(PolicyBase):
    """Schema for creating a new policy."""
    pass


class PolicyUpdate(BaseModel):
    """Schema for updating a policy."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    parameters_json: Optional[Dict[str, Any]] = None  # Check-specific parameters
    policy_yaml: Optional[str] = None  # DEPRECATED - kept for backward compatibility
    filters_json: Optional[List[Dict[str, Any]]] = None  # DEPRECATED - kept for backward compatibility
    status: Optional[str] = Field(None, pattern="^(active|inactive)$")


class PolicyResponse(PolicyBase):
    """Schema for policy response."""
    id: int
    policy_code: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class PolicyExecuteRequest(BaseModel):
    """Schema for policy execution request (scan only, no actions)."""
    filters: Optional[Dict[str, Any]] = None  # Additional runtime filters


class PolicyBatchExecuteRequest(BaseModel):
    """Schema for batch policy execution request (scan only, no actions)."""
    policy_ids: List[int] = Field(..., min_items=1)


class PolicyExecutionResultResponse(BaseModel):
    """Schema for individual policy execution result."""
    id: int
    resource_id: str
    resource_type: str
    resource_name: Optional[str]
    region: Optional[str]
    account_id: Optional[str]
    reason: Optional[str]
    metadata_json: Optional[Dict[str, Any]]
    monthly_cost: Optional[float] = None  # Monthly cost in USD
    cost_breakdown: Optional[Dict[str, Any]] = None  # Detailed cost breakdown
    
    @model_validator(mode='after')
    def extract_cost_from_metadata(self):
        """Extract cost data from metadata_json if not already set."""
        if self.metadata_json and self.monthly_cost is None:
            self.monthly_cost = self.metadata_json.get("monthly_cost")
            self.cost_breakdown = self.metadata_json.get("cost_breakdown")
        return self
    
    class Config:
        from_attributes = True


class PolicyExecutionResponse(BaseModel):
    """Schema for policy execution response."""
    id: int
    policy_id: int
    execution_type: str  # Always "scan" now, kept for backward compatibility
    status: str
    resources_found: int
    results_json: Optional[Dict[str, Any]]
    error_message: Optional[str]
    started_at: datetime
    completed_at: Optional[datetime]
    results: List[PolicyExecutionResultResponse] = []
    # Cost aggregation fields
    total_monthly_cost: Optional[float] = None  # Total cost of all resources
    cost_by_resource_type: Optional[Dict[str, float]] = None  # Cost grouped by type
    cost_by_region: Optional[Dict[str, float]] = None  # Cost grouped by region
    resource_count_by_type: Optional[Dict[str, int]] = None  # Count by type
    
    @model_validator(mode='after')
    def extract_cost_data(self):
        """Extract cost data from results_json if not already set."""
        if self.results_json and not self.total_monthly_cost:
            self.total_monthly_cost = self.results_json.get("total_monthly_cost")
            self.cost_by_resource_type = self.results_json.get("cost_by_resource_type")
            self.cost_by_region = self.results_json.get("cost_by_region")
            self.resource_count_by_type = self.results_json.get("resource_count_by_type")
        return self
    
    class Config:
        from_attributes = True


class PolicyValidationResponse(BaseModel):
    """Schema for policy validation response."""
    valid: bool
    errors: List[str] = []
    warnings: List[str] = []


class PolicyCostSavingsResponse(BaseModel):
    """Schema for policy cost savings response."""
    id: int
    policy_id: int
    execution_id: Optional[int]
    date: datetime
    cost_saved: float
    resources_fixed: int
    notes: Optional[str]
    
    class Config:
        from_attributes = True

