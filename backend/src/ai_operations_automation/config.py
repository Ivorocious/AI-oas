"""Typed, nonsecret application configuration."""

from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, PostgresDsn, SecretStr, field_validator
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
    supabase_issuer: AnyHttpUrl = AnyHttpUrl("https://example.supabase.co/auth/v1")
    supabase_audience: str = "authenticated"
    supabase_jwks_url: AnyHttpUrl = AnyHttpUrl(
        "https://example.supabase.co/auth/v1/.well-known/jwks.json"
    )
    jwks_cache_seconds: int = Field(default=300, ge=30, le=3600)
    machine_clock_skew_seconds: int = Field(default=300, ge=30, le=900)
    machine_nonce_retention_seconds: int = Field(default=600, ge=60, le=3600)
    ai_interpretation_prompt_version: str = Field(
        default="service-request-interpretation-v1", max_length=100
    )
    ai_interpretation_result_schema_version: str = Field(
        default="service-request-interpretation-v1", max_length=100
    )
    ai_provider_name: str = Field(default="DemoAIProvider", max_length=100)
    ai_model_name: str = Field(default="demo-ai-model-v1", max_length=100)
    ai_adapter_name: str = Field(default="WorkflowServiceAIAdapter", max_length=100)
    ai_adapter_version: str = Field(default="1.0", max_length=100)
    ai_callback_authorization_seconds: int = Field(default=1800, ge=300, le=86400)
    protected_query_cursor_signing_key: SecretStr | None = Field(default=None)
    demo_auth_enabled: bool = False

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

    @field_validator("machine_nonce_retention_seconds")
    @classmethod
    def nonce_retention_exceeds_clock_skew(cls, value: int, info) -> int:
        skew = info.data.get("machine_clock_skew_seconds", 300)
        if value <= skew:
            raise ValueError("machine nonce retention must exceed clock skew")
        return value

    @field_validator(
        "ai_interpretation_prompt_version",
        "ai_interpretation_result_schema_version",
        "ai_provider_name",
        "ai_model_name",
        "ai_adapter_name",
        "ai_adapter_version",
    )
    @classmethod
    def ai_configuration_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("AI configuration identity must not be blank")
        return value

    @field_validator("protected_query_cursor_signing_key")
    @classmethod
    def cursor_key_must_be_safe_when_configured(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value().encode()) < 32:
            raise ValueError("protected query cursor signing key must be at least 32 bytes")
        return value

    @field_validator("demo_auth_enabled")
    @classmethod
    def demo_auth_is_local_only(cls, value: bool, info) -> bool:
        """Prevent the portfolio-only issuer from being enabled outside local mode."""
        if value and info.data.get("app_environment") != "local":
            raise ValueError("demo auth is permitted only when app_environment is local")
        return value


@lru_cache
def get_settings() -> Settings:
    """Load and cache process configuration at the application composition boundary."""
    return Settings()
