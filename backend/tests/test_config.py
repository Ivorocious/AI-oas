from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_operations_automation.config import Settings


def test_environment_variable_overrides_safe_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_OPS_APP_NAME", "Environment Operations API")

    settings = Settings(_env_file=None)

    assert settings.app_name == "Environment Operations API"


def test_unknown_dotenv_configuration_is_rejected(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("AI_OPS_UNKNOWN_SETTING=unexpected\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        Settings(_env_file=env_file)
