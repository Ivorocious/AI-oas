"""Unattached FastAPI dependency for authenticated WorkflowService requests."""

import uuid
from typing import Annotated

from fastapi import Depends, Request

from ai_operations_automation.api.correlation import resolve_request_correlation
from ai_operations_automation.machine_auth.authenticator import WorkflowServiceAuthenticator
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService


async def authenticated_workflow_service(
    request: Request,
    _correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
) -> AuthenticatedWorkflowService:
    authenticator = WorkflowServiceAuthenticator(
        settings=request.app.state.settings,
        session_factory=request.app.state.session_factory,
        secret_resolver=request.app.state.machine_secret_resolver,
        clock=request.app.state.machine_clock,
    )
    return await authenticator.authenticate(request)
