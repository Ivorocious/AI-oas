"""FastAPI application factory."""

import uuid
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from ai_operations_automation.api.attempt_callbacks import router as attempt_callbacks_router
from ai_operations_automation.api.attempt_start import router as attempt_start_router
from ai_operations_automation.api.callback_credentials import router as callback_credentials_router
from ai_operations_automation.api.duplicate_resolution import router as duplicate_resolution_router
from ai_operations_automation.api.health import router as health_router
from ai_operations_automation.api.human_review import router as human_review_router
from ai_operations_automation.api.intake import router as intake_router
from ai_operations_automation.api.proposals import router as proposals_router
from ai_operations_automation.api.protected_queries import router as protected_queries_router
from ai_operations_automation.api.retry_ai import router as retry_ai_router
from ai_operations_automation.api.retry_outbound import router as retry_outbound_router
from ai_operations_automation.api.service_requests import router as service_requests_router
from ai_operations_automation.api.start_ai_interpretation import (
    router as start_ai_interpretation_router,
)
from ai_operations_automation.api.start_outbound import router as start_outbound_router
from ai_operations_automation.api.terminal_failure import router as terminal_failure_router
from ai_operations_automation.auth.verifier import SupabaseJwtVerifier, url_jwks_loader
from ai_operations_automation.config import Settings, get_settings
from ai_operations_automation.db import create_database_engine, create_session_factory
from ai_operations_automation.duplicate_resolution.service import ResolveDuplicateService
from ai_operations_automation.human_review.service import CompleteHumanReviewService
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.machine_auth.secrets import (
    MachineSecretResolver,
    UnavailableMachineSecretResolver,
)
from ai_operations_automation.proposal.service import ProposalLifecycleService
from ai_operations_automation.start_ai.credentials import generate_callback_credential


def create_app(
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] | None = None,
    jwt_verifier: object | None = None,
    machine_secret_resolver: MachineSecretResolver | None = None,
    machine_clock: object | None = None,
    callback_credential_generator: object | None = None,
    duplicate_resolution_service: object | None = None,
    human_review_service: object | None = None,
    proposal_service: object | None = None,
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
    application.state.callback_credential_generator = (
        callback_credential_generator or generate_callback_credential
    )
    application.state.duplicate_resolution_service = (
        duplicate_resolution_service or ResolveDuplicateService(session_factory)
    )
    application.state.human_review_service = human_review_service or CompleteHumanReviewService(
        session_factory
    )
    application.state.proposal_service = proposal_service or ProposalLifecycleService(
        session_factory
    )

    def documented_openapi() -> dict:
        if application.openapi_schema is not None:
            return application.openapi_schema
        schema = get_openapi(
            title=application.title,
            version=application.version,
            routes=application.routes,
        )
        security_schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
        security_schemes["WorkflowServiceHmac"] = {
            "type": "apiKey",
            "in": "header",
            "name": "X-Service-Signature",
            "description": (
                "WorkflowService HMAC proof used with service ID, timestamp, and nonce headers."
            ),
        }
        security_schemes["AttemptCallbackCredential"] = {
            "type": "apiKey",
            "in": "header",
            "name": "X-Attempt-Callback-Credential",
            "description": "Exact attempt-scoped callback authorization credential.",
        }
        application.openapi_schema = schema
        return schema

    application.openapi = documented_openapi

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
    application.include_router(attempt_start_router)
    application.include_router(attempt_callbacks_router)
    application.include_router(callback_credentials_router)
    application.include_router(duplicate_resolution_router)
    application.include_router(intake_router)
    application.include_router(human_review_router)
    application.include_router(proposals_router)
    application.include_router(protected_queries_router)
    application.include_router(retry_ai_router)
    application.include_router(retry_outbound_router)
    application.include_router(service_requests_router)
    application.include_router(start_ai_interpretation_router)
    application.include_router(start_outbound_router)
    application.include_router(terminal_failure_router)
    return application
