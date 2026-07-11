"""FastAPI application factory."""

from fastapi import FastAPI

from ai_operations_automation.api.health import router as health_router
from ai_operations_automation.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an application without network or database side effects."""
    active_settings = settings or get_settings()
    application = FastAPI(title=active_settings.app_name)
    application.state.settings = active_settings
    application.include_router(health_router)
    return application
