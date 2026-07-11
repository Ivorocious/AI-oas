from fastapi import FastAPI

from ai_operations_automation.app import create_app
from ai_operations_automation.config import Settings


def test_create_app_returns_fastapi_application() -> None:
    application = create_app(Settings(_env_file=None))

    assert isinstance(application, FastAPI)


def test_explicit_settings_are_reflected_by_application() -> None:
    settings = Settings(app_name="Test Operations API", app_environment="test", _env_file=None)

    application = create_app(settings)

    assert application.title == "Test Operations API"
    assert application.state.settings is settings
