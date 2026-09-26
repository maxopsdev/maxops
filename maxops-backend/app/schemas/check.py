"""Pydantic schemas for check-related APIs."""
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional


class CheckMetadataResponse(BaseModel):
    """Schema for check metadata response."""
    check_id: str = Field(..., description="Unique check identifier")
    name: str = Field(..., description="Human-readable check name")
    description: str = Field(..., description="Detailed description of what the check does")
    resource_type: str = Field(..., description="AWS resource type (ec2, rds, ebs, snapshot)")
    default_action: str = Field(..., description="Default action to take (stop, terminate, delete)")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Default parameters for the check")


class CheckTestRequest(BaseModel):
    """Schema for testing a check without creating a policy."""
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Parameters to override defaults")


class CheckTestResponse(BaseModel):
    """Schema for check test response."""
    check_id: str = Field(..., description="Check identifier that was tested")
    resources_found: int = Field(..., description="Number of resources that matched the check")
    resources: list = Field(default_factory=list, description="List of resources that matched")
    potential_savings_yearly: float = Field(0.0, description="Total potential savings per year in USD")


class CheckActionRequest(BaseModel):
    """Schema for running an action."""
    action: str = Field(..., description="Action identifier to run")
    account_id: str = Field(..., description="AWS account ID")
    region: str = Field(..., description="AWS region")
    resource_id: str = Field(..., description="Resource identifier (e.g., EC2 instance id)")
    parameters: Optional[Dict[str, Any]] = Field(default_factory=dict)


class CheckActionResponse(BaseModel):
    """Schema for action response."""
    check_id: str
    action: str
    status: str
    message: str
    details: Optional[Dict[str, Any]] = None
