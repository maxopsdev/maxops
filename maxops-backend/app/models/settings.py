"""Settings and onboarding models."""
from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, JSON, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class UserSettings(Base):
    """Legacy onboarding data kept for migration compatibility."""
    __tablename__ = "user_settings"
    
    id = Column(Integer, primary_key=True, index=True)
    environment = Column(String(100), nullable=False)
    # Note: account and region are nullable in DB for SQLite compatibility,
    # but enforced as required by Pydantic schema and application logic
    account = Column(String(100), nullable=True)  # AWS account ID or name
    region = Column(String(50), nullable=True)  # AWS region
    accounts = Column(JSON, nullable=True)  # Selected AWS accounts
    regions = Column(JSON, nullable=True)  # Selected AWS regions
    idle_days = Column(Integer, nullable=False)
    a_days = Column(Integer, nullable=False)  # First threshold days
    b_days = Column(Integer, nullable=False)  # Second threshold days
    onboarding_completed = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    def __repr__(self):
        return f"<UserSettings(id={self.id}, environment='{self.environment}', onboarding_completed={self.onboarding_completed})>"


class AccountSettings(Base):
    """OSS account-level settings."""
    __tablename__ = "account_settings"

    id = Column(Integer, primary_key=True, index=True)
    environment = Column(String(100), nullable=False)
    account = Column(String(100), nullable=False)
    environment_options = Column(JSON, nullable=False, default=list)
    regions = Column(JSON, nullable=False, default=list)
    onboarding_completed = Column(Boolean, default=False, nullable=False)
    onboarding_step = Column(String(50), nullable=False, default="information")
    onboarding_data = Column(JSON, nullable=True)
    # Price findings from the CUR cost cache instead of list prices.
    # NULL means "not chosen here" and falls back to the environment default.
    cur_pricing_enabled = Column(Boolean, nullable=True)
    # AWS profile used for privileged setup work only — creating exports,
    # running Athena queries. Scans deliberately keep using the read-only scan
    # role; this is never used for them. NULL means no elevated profile is
    # configured and setup falls back to the runtime credentials.
    setup_aws_profile = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    check_settings = relationship(
        "CheckSetting",
        back_populates="account_settings",
        cascade="all, delete-orphan",
    )
    action_settings = relationship(
        "ActionSetting",
        back_populates="account_settings",
        cascade="all, delete-orphan",
    )
    onboarding_executions = relationship(
        "OnboardingExecution",
        back_populates="account_settings",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<AccountSettings(id={self.id}, environment='{self.environment}', account='{self.account}')>"


class CheckSetting(Base):
    """Saved per-check preset and parameter overrides for OSS account settings."""
    __tablename__ = "check_settings"

    id = Column(Integer, primary_key=True, index=True)
    account_settings_id = Column(Integer, ForeignKey("account_settings.id"), nullable=False, index=True)
    check_id = Column(String(255), nullable=False, index=True)
    resource_type = Column(String(100), nullable=False, index=True)
    preset = Column(String(32), nullable=False, default="normal")
    parameters_json = Column(JSON, nullable=False, default=dict)
    is_customized = Column(Boolean, nullable=False, default=False)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    account_settings = relationship("AccountSettings", back_populates="check_settings")

    def __repr__(self):
        return (
            f"<CheckSetting(id={self.id}, check_id='{self.check_id}', "
            f"preset='{self.preset}', customized={self.is_customized}, enabled={self.enabled})>"
        )


class ActionSetting(Base):
    """Saved per-action enablement for OSS account settings."""
    __tablename__ = "action_settings"

    id = Column(Integer, primary_key=True, index=True)
    account_settings_id = Column(Integer, ForeignKey("account_settings.id"), nullable=False, index=True)
    action_key = Column(String(255), nullable=False, index=True)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    account_settings = relationship("AccountSettings", back_populates="action_settings")

    def __repr__(self):
        return (
            f"<ActionSetting(id={self.id}, action_key='{self.action_key}', "
            f"enabled={self.enabled})>"
        )


class OnboardingExecution(Base):
    """Onboarding check execution record."""
    __tablename__ = "onboarding_executions"
    
    id = Column(Integer, primary_key=True, index=True)
    settings_id = Column(Integer, ForeignKey("account_settings.id"), nullable=False)
    status = Column(String(20), default="running", nullable=False)  # running, completed, failed, canceled
    total_checks = Column(Integer, default=0)
    completed_checks = Column(Integer, default=0)
    failed_checks = Column(Integer, default=0)
    results_json = Column(JSON, nullable=True)  # Full results array
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    account_settings = relationship("AccountSettings", back_populates="onboarding_executions")
    check_results = relationship("OnboardingCheckResult", back_populates="execution", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<OnboardingExecution(id={self.id}, status='{self.status}', total_checks={self.total_checks})>"


class OnboardingCheckResult(Base):
    """Individual check result within an onboarding execution."""
    __tablename__ = "onboarding_check_results"
    
    id = Column(Integer, primary_key=True, index=True)
    execution_id = Column(Integer, ForeignKey("onboarding_executions.id"), nullable=False)
    check_id = Column(String(255), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    resource_type = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False)  # completed, failed
    resources_found = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)  # Additional check metadata
    
    # Relationships
    execution = relationship("OnboardingExecution", back_populates="check_results")
    
    def __repr__(self):
        return f"<OnboardingCheckResult(id={self.id}, check_id='{self.check_id}', status='{self.status}')>"


class CheckFilter(Base):
    """Filters configured for a specific check."""
    __tablename__ = "check_filters"
    
    id = Column(Integer, primary_key=True, index=True)
    check_id = Column(String(255), nullable=False, index=True, unique=True)
    filters_json = Column(JSON, nullable=False)  # List of filter conditions
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    def __repr__(self):
        return f"<CheckFilter(id={self.id}, check_id='{self.check_id}')>"


class ResourceExemption(Base):
    """Resource exemptions and snoozes for checks."""
    __tablename__ = "resource_exemptions"
    
    id = Column(Integer, primary_key=True, index=True)
    check_id = Column(String(255), nullable=False, index=True)
    resource_id = Column(String(255), nullable=False, index=True)
    resource_type = Column(String(100), nullable=False)
    exempted = Column(Boolean, default=False, nullable=False)  # Permanently exempt
    snoozed_until = Column(DateTime(timezone=True), nullable=True)  # Snoozed until this date
    snooze_days = Column(Integer, nullable=True)  # Number of days snoozed
    snooze_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Unique constraint: one exemption record per check+resource combination
    __table_args__ = (
        {'sqlite_autoincrement': True},
    )
    
    def __repr__(self):
        return f"<ResourceExemption(id={self.id}, check_id='{self.check_id}', resource_id='{self.resource_id}', exempted={self.exempted})>"


class ResourceSnoozeAudit(Base):
    """Audit trail for global resource snooze lifecycle changes."""
    __tablename__ = "resource_snooze_audits"

    id = Column(Integer, primary_key=True, index=True)
    action = Column(String(32), nullable=False, index=True)
    resource_id = Column(String(255), nullable=False, index=True)
    resource_type = Column(String(100), nullable=False, index=True)
    resource_name = Column(String(255), nullable=True)
    account_id = Column(String(100), nullable=True)
    region = Column(String(50), nullable=True)
    previous_snoozed_until = Column(DateTime(timezone=True), nullable=True)
    new_snoozed_until = Column(DateTime(timezone=True), nullable=True)
    previous_reason = Column(Text, nullable=True)
    new_reason = Column(Text, nullable=True)
    actor = Column(String(255), nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    def __repr__(self):
        return f"<ResourceSnoozeAudit(id={self.id}, action='{self.action}', resource_id='{self.resource_id}')>"
