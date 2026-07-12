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
)
from ai_operations_automation.auth.verifier import AuthenticationFailure, KeyDiscoveryFailure
from ai_operations_automation.db.dependencies import get_session_factory
from ai_operations_automation.db.models.identity import (
    ApplicationActor,
    ApplicationActorRoleAssignment,
)
from ai_operations_automation.intake.errors import IntakeError

bearer = HTTPBearer(auto_error=False)


def authenticated_human(
    request: Request,
    _correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AuthenticatedHuman:
    if len(request.headers.getlist("authorization")) != 1:
        raise IntakeError(401, "AUTHENTICATION_REQUIRED", "Human authentication is required.")
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise IntakeError(401, "AUTHENTICATION_REQUIRED", "Human authentication is required.")
    try:
        subject = request.app.state.jwt_verifier.verify(credentials.credentials)
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


def require_service_request_reader(
    human: Annotated[AuthenticatedHuman, Depends(authenticated_human)],
) -> AuthenticatedHuman:
    return require_service_request_permission(human)
