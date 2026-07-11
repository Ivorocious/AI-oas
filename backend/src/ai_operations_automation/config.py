"""Typed, nonsecret application configuration."""

from functools import lru_cache
from typing import Literal

from pydantic import PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration loaded from safe defaults, `.env`, and project-prefixed variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="AI_OPS_",
        extra="forbid",
    )

    app_name: str = "AI Operations Automation API"
    app_environment: Literal["local", "test", "development", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    api_v1_prefix: str = "/api/v1"
    database_url: PostgresDsn = PostgresDsn(
        "postgresql+psycopg://ai_ops_local:ai_ops_local_password@127.0.0.1:55432/ai_ops_local"
    )

    @field_validator("app_name")
    @classmethod
    def app_name_must_not_be_blank(cls, value: str) -> str:
        """Reject a blank application title."""
        if not value.strip():
            raise ValueError("app_name must not be blank")
        return value

    @field_validator("api_v1_prefix")
    @classmethod
    def validate_api_v1_prefix(cls, value: str) -> str:
        """Keep the future domain API prefix predictable without creating its routes."""
        if not value.startswith("/") or value == "/" or value.endswith("/"):
            raise ValueError("api_v1_prefix must start with '/' and must not end with '/'")
        return value


@lru_cache
def get_settings() -> Settings:
    """Load and cache process configuration at the application composition boundary."""
    return Settings()
