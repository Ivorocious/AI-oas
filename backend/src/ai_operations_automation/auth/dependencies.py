"""Per-request token verification and current actor/role lookup."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError

from ai_operations_automation.api.correlation import resolve_request_correlation
from ai_operations_automation.auth.models import AuthenticatedHuman, HumanRole
from ai_operations_automation.auth.permissions import (
    SERVICE_REQUEST_READERS,
    require_service_request_permission,
    require_terminal_disposition_permission,
)
from ai_operations_automation.auth.verifier import AuthenticationFailure, KeyDiscoveryFailure
from ai_operations_automation.db.dependencies import get_session_factory
from ai_operations_automation.db.models.identity import (
    ApplicationActor,
    ApplicationActorRoleAssignment,
)
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.machine_auth.authenticator import WorkflowServiceAuthenticator
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService

bearer = HTTPBearer(auto_error=False)

MACHINE_AUTH_HEADERS = (
    "x-service-id",
    "x-service-timestamp",
    "x-service-nonce",
    "x-service-signature",
)

CommandAuthority = AuthenticatedHuman | AuthenticatedWorkflowService


def _resolve_application_human(request: Request, token: str) -> AuthenticatedHuman:
    """Verify one token and load the one current application role."""
    try:
        subject = request.app.state.jwt_verifier.verify(token)
    except KeyDiscoveryFailure as exc:
        raise IntakeError(
            503, "DEPENDENCY_UNAVAILABLE", "A required dependency is unavailable.", True
        ) from exc
    except AuthenticationFailure as exc:
        raise IntakeError(
            401, "AUTHENTICATION_REQUIRED", "Human authentication is required."
        ) from exc

    now = datetime.now(UTC)
    try:
        with get_session_factory(request)() as session:
            actor = session.scalar(
                select(ApplicationActor).where(
                    ApplicationActor.supabase_subject == subject,
                    ApplicationActor.status == "Active",
                )
            )
            if actor is None:
                raise IntakeError(403, "FORBIDDEN", "The requested operation is not permitted.")
            assignments = session.scalars(
                select(ApplicationActorRoleAssignment)
                .where(
                    ApplicationActorRoleAssignment.actor_id == actor.id,
                    ApplicationActorRoleAssignment.effective_from <= now,
                    or_(
                        ApplicationActorRoleAssignment.effective_to.is_(None),
                        ApplicationActorRoleAssignment.effective_to > now,
                    ),
                )
                .limit(2)
            ).all()
    except IntakeError:
        raise
    except SQLAlchemyError as exc:
        raise IntakeError(
            503, "DEPENDENCY_UNAVAILABLE", "A required dependency is unavailable.", True
        ) from exc
    if len(assignments) != 1 or assignments[0].role not in SERVICE_REQUEST_READERS:
        raise IntakeError(403, "FORBIDDEN", "The requested operation is not permitted.")
    assignment = assignments[0]
    return AuthenticatedHuman(actor.id, subject, cast(HumanRole, assignment.role))


def authenticated_human(
    request: Request,
    _correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AuthenticatedHuman:
    if len(request.headers.getlist("authorization")) != 1:
        raise IntakeError(401, "AUTHENTICATION_REQUIRED", "Human authentication is required.")
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise IntakeError(401, "AUTHENTICATION_REQUIRED", "Human authentication is required.")
    return _resolve_application_human(request, credentials.credentials)


async def authenticated_retry_authority(
    request: Request,
    _correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
) -> CommandAuthority:
    """Resolve exactly one human or WorkflowService credential family."""
    authorization_values = request.headers.getlist("authorization")
    has_human = bool(authorization_values)
    has_machine = any(request.headers.getlist(name) for name in MACHINE_AUTH_HEADERS)
    if has_human == has_machine:
        raise IntakeError(401, "AUTHENTICATION_REQUIRED", "Authentication is required.")
    if has_human:
        if len(authorization_values) != 1:
            raise IntakeError(401, "AUTHENTICATION_REQUIRED", "Human authentication is required.")
        scheme, separator, token = authorization_values[0].partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not token or token != token.strip():
            raise IntakeError(401, "AUTHENTICATION_REQUIRED", "Human authentication is required.")
        return _resolve_application_human(request, token)

    authenticator = WorkflowServiceAuthenticator(
        settings=request.app.state.settings,
        session_factory=request.app.state.session_factory,
        secret_resolver=request.app.state.machine_secret_resolver,
        clock=request.app.state.machine_clock,
    )
    return await authenticator.authenticate(request)


async def authenticated_query_principal(
    request: Request,
    _correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
) -> CommandAuthority:
    """Resolve the only two external identities permitted at protected query edges."""
    return await authenticated_retry_authority(request, _correlation_id)


def require_human_query(
    authority: Annotated[CommandAuthority, Depends(authenticated_query_principal)],
) -> AuthenticatedHuman:
    """Deny an authenticated WorkflowService where the query matrix is human-only."""
    if not isinstance(authority, AuthenticatedHuman):
        raise IntakeError(403, "FORBIDDEN", "The requested operation is not permitted.")
    return authority


def require_service_request_reader(
    human: Annotated[AuthenticatedHuman, Depends(authenticated_human)],
) -> AuthenticatedHuman:
    return require_service_request_permission(human)


def require_terminal_disposition_actor(
    human: Annotated[AuthenticatedHuman, Depends(authenticated_human)],
) -> AuthenticatedHuman:
    return require_terminal_disposition_permission(human)
