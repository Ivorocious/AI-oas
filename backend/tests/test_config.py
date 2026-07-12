from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_operations_automation.config import Settings


def test_environment_variable_overrides_safe_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_OPS_APP_NAME", "Environment Operations API")

    settings = Settings(_env_file=None)

    assert settings.app_name == "Environment Operations API"


def test_database_url_environment_variable_override(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = "postgresql+psycopg://local_user:local_pass@localhost:6543/local_db"
    monkeypatch.setenv("AI_OPS_DATABASE_URL", database_url)

    settings = Settings(_env_file=None)

    assert str(settings.database_url) == database_url


def test_supabase_verifier_settings_are_project_prefixed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_OPS_SUPABASE_AUDIENCE", "local-human-audience")
    monkeypatch.setenv("AI_OPS_JWKS_CACHE_SECONDS", "600")

    settings = Settings(_env_file=None)

    assert settings.supabase_audience == "local-human-audience"
    assert settings.jwks_cache_seconds == 600


def test_unknown_dotenv_configuration_is_rejected(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("AI_OPS_UNKNOWN_SETTING=unexpected\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        Settings(_env_file=env_file)
