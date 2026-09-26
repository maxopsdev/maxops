"""Configuration for the MaxOps MCP server."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed MCP settings."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    maxops_api_base_url: str = Field(default="http://localhost:8000/api/v1")
    maxops_api_token: str | None = Field(default=None)
    maxops_mcp_write_enabled: bool = Field(default=False)
    maxops_mcp_request_timeout: float = Field(default=30.0)


settings = Settings()

