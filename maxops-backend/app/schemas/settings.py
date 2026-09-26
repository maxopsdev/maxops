"""Pydantic schemas for OSS account settings."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class GlobalSettingsPayload(BaseModel):
    environment: str = Field(..., min_length=1, max_length=100)
    account: str = Field(..., min_length=1, max_length=100)
    environment_options: List[str] = Field(default_factory=list)
    region: Optional[str] = Field(default=None, min_length=1)
    regions: List[str] = Field(default_factory=list)


class OnboardingRequest(BaseModel):
    environment: str = Field(..., min_length=1, max_length=100)
    account: str = Field(..., min_length=1, max_length=100)
    region: Optional[str] = Field(default=None, min_length=1)
    regions: List[str] = Field(default_factory=list)
    idle_days: Optional[int] = Field(default=None, ge=1)
    a_days: Optional[int] = Field(default=None, ge=1)
    b_days: Optional[int] = Field(default=None, ge=1)


class OnboardingProgressRequest(BaseModel):
    onboarding_step: Optional[str] = Field(default=None, min_length=1, max_length=50)
    onboarding_data: Dict[str, Any] = Field(default_factory=dict)


class OnboardingDraftRequest(BaseModel):
    environment: Optional[str] = Field(default=None, max_length=100)
    account: Optional[str] = Field(default=None, max_length=100)
    region: Optional[str] = Field(default=None, min_length=1)
    regions: List[str] = Field(default_factory=list)


class SavedCheckSettingsPayload(BaseModel):
    check_id: str = Field(..., min_length=1)
    preset: str = Field(default="normal")
    parameters: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = Field(default=True)


class SavedActionSettingsPayload(BaseModel):
    action_key: str = Field(..., min_length=1)
    enabled: bool = Field(default=True)


class SettingsUpdateRequest(BaseModel):
    global_settings: GlobalSettingsPayload
    check_settings: List[SavedCheckSettingsPayload] = Field(default_factory=list)
    action_settings: List[SavedActionSettingsPayload] = Field(default_factory=list)


class AccountSettingsSummaryResponse(BaseModel):
    id: int
    environment: str
    account: str
    environment_options: List[str] = Field(default_factory=list)
    region: Optional[str] = None
    regions: List[str] = Field(default_factory=list)
    onboarding_completed: bool
    onboarding_step: str = "information"
    onboarding_data: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
