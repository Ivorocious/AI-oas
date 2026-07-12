"""FastAPI application factory."""

from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from ai_operations_automation.api.health import router as health_router
from ai_operations_automation.api.intake import router as intake_router
from ai_operations_automation.config import Settings, get_settings
from ai_operations_automation.db import create_database_engine, create_session_factory


def create_app(
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> FastAPI:
    """Create an application without network or database side effects."""
    active_settings = settings or get_settings()
    application = FastAPI(title=active_settings.app_name)
    application.state.settings = active_settings
    if session_factory is None:
        engine = create_database_engine(active_settings.database_url)
        session_factory = create_session_factory(engine)
        application.state.database_engine = engine
    application.state.session_factory = session_factory
    application.include_router(health_router)
    application.include_router(intake_router)
    return application
