"""Application configuration."""
from pydantic_settings import BaseSettings
from pydantic import Field, AliasChoices, field_validator
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Application
    app_name: str = "MaxOps API"
    debug: bool = False
    log_level: str = "INFO"
    
    # Database
    database_url: str = "sqlite:///./maxops.db"
    pricing_database_path: str = "./maxops_pricing.db"
    
    # AWS
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    aws_region: str = "us-east-1"
    aws_use_iam_role: bool = False  # Use IAM role (for EC2/ECS deployment)
    aws_role_arn: Optional[str] = None  # IAM role ARN to assume (for local development)
    aws_role_session_name: str = "maxops-backend"  # Session name for assumed role
    aws_profile: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "aws_profile"),
    )  # AWS CLI profile name to use

    # Feature flags
    test_action: bool = False  # Enable test actions from UI (env-controlled)
    actions_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("MAXOPS_ENABLE_ACTIONS", "actions_enabled"),
    )  # Global gate for real (non-test) action execution; off by default
    s3_optimizer_enabled: bool = True
    asg_instance_optimization_enabled: bool = False
    asg_instance_optimization_min_coremark_coverage: float = 0.70

    # Cost Data Settings (optional)
    cost_explorer_lookback_days: int = 90

    # AWS profile for privileged setup work (creating a CUR export, running
    # the Athena queries that summarise it). Scans always keep using the
    # read-only scan role; this is never used for them. The value chosen in
    # Settings takes precedence over this default.
    setup_aws_profile: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("MAXOPS_SETUP_AWS_PROFILE", "setup_aws_profile"),
    )

    # Cost and Usage Report (CUR) pricing.
    # When a local CUR aggregate cache is present, actual billed cost takes
    # precedence over list-price estimates. Off by default: the cache only
    # exists once an operator has set up a CUR export and refreshed it.
    cur_pricing_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("MAXOPS_CUR_PRICING_ENABLED", "cur_pricing_enabled"),
    )
    cur_cache_root: str = Field(
        default="data/cur_cache",
        validation_alias=AliasChoices("MAXOPS_CUR_CACHE_ROOT", "cur_cache_root"),
    )
    # Reload the cached CUR cost map at most this often (seconds).
    cur_pricing_refresh_seconds: int = 900
    
    # Pricing Service
    pricing_service_url: str = "http://localhost:8003"  # Pricing service endpoint
    pricing_timeout: int = 30  # Timeout for pricing API calls in seconds

    @field_validator("debug", mode="before")
    @classmethod
    def normalize_debug_value(cls, value):
        """Accept common non-boolean DEBUG values from shell environments."""
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on", "debug", "dev", "development"}:
                return True
            if normalized in {"0", "false", "no", "off", "release", "prod", "production"}:
                return False
        return value
    
    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"  # Ignore extra fields in .env file


settings = Settings()
