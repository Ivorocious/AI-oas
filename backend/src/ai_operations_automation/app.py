"""FastAPI application factory."""

import uuid
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from ai_operations_automation.api.health import router as health_router
from ai_operations_automation.api.intake import router as intake_router
from ai_operations_automation.api.service_requests import router as service_requests_router
from ai_operations_automation.auth.verifier import SupabaseJwtVerifier, url_jwks_loader
from ai_operations_automation.config import Settings, get_settings
from ai_operations_automation.db import create_database_engine, create_session_factory
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.machine_auth.secrets import (
    MachineSecretResolver,
    UnavailableMachineSecretResolver,
)


def create_app(
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] | None = None,
    jwt_verifier: object | None = None,
    machine_secret_resolver: MachineSecretResolver | None = None,
    machine_clock: object | None = None,
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
    application.state.jwt_verifier = jwt_verifier or SupabaseJwtVerifier(
        issuer=str(active_settings.supabase_issuer),
        audience=active_settings.supabase_audience,
        loader=url_jwks_loader(str(active_settings.supabase_jwks_url)),
        cache_seconds=active_settings.jwks_cache_seconds,
    )
    application.state.machine_secret_resolver = (
        machine_secret_resolver or UnavailableMachineSecretResolver()
    )
    application.state.machine_clock = machine_clock or (lambda: datetime.now(UTC))

    @application.exception_handler(IntakeError)
    async def safe_api_error(_request: Request, error: IntakeError) -> JSONResponse:
        headers = {"X-Correlation-ID": str(uuid.uuid4())}
        if error.status_code == 401 and error.code == "AUTHENTICATION_REQUIRED":
            headers["WWW-Authenticate"] = "Bearer"
        correlation_id = getattr(_request.state, "correlation_id", uuid.uuid4())
        headers["X-Correlation-ID"] = str(correlation_id)
        return JSONResponse(
            error.response(correlation_id), status_code=error.status_code, headers=headers
        )

    application.include_router(health_router)
    application.include_router(intake_router)
    application.include_router(service_requests_router)
    return application
