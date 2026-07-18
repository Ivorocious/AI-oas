"""Protected service-request detail query."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from ai_operations_automation.api.correlation import resolve_request_correlation
from ai_operations_automation.auth.dependencies import (
    CommandAuthority,
    authenticated_query_principal,
    bearer,
)
from ai_operations_automation.db.dependencies import get_session_factory
from ai_operations_automation.db.models.ai_execution import IntegrationAttempt
from ai_operations_automation.db.models.intake import ServiceRequest
from ai_operations_automation.intake.errors import ErrorEnvelope, IntakeError
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService
from ai_operations_automation.service_requests.models import (
    ServiceRequestResponse,
    WorkflowAiServiceRequestResponse,
    WorkflowAiServiceRequestView,
    WorkflowOutboundServiceRequestResponse,
    WorkflowOutboundServiceRequestView,
)
from ai_operations_automation.service_requests.query import query_service_request

router = APIRouter()


@router.get(
    "/api/v1/service-requests/{request_id}",
    response_model=(
        ServiceRequestResponse
        | WorkflowAiServiceRequestResponse
        | WorkflowOutboundServiceRequestResponse
    ),
    dependencies=[Depends(bearer)],
    responses={
        400: {"model": ErrorEnvelope},
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        500: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)
def get_service_request(
    request_id: str,
    request: Request,
    correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
    principal: Annotated[CommandAuthority, Depends(authenticated_query_principal)],
) -> JSONResponse:
    try:
        parsed_id = uuid.UUID(request_id)
    except ValueError as exc:
        raise IntakeError(
            404, "RESOURCE_NOT_FOUND", "The requested resource was not found."
        ) from exc
    try:
        session_factory = get_session_factory(request)
        if isinstance(principal, AuthenticatedWorkflowService):
            with session_factory() as session:
                row = session.execute(
                    select(ServiceRequest, IntegrationAttempt)
                    .join(
                        IntegrationAttempt,
                        IntegrationAttempt.service_request_id == ServiceRequest.id,
                    )
                    .where(
                        ServiceRequest.id == parsed_id,
                        IntegrationAttempt.assigned_workflow_service == principal.stable_service_id,
                        IntegrationAttempt.workflow_environment == principal.environment,
                    )
                    .order_by(IntegrationAttempt.created_at.desc(), IntegrationAttempt.id.desc())
                    .limit(1)
                ).one_or_none()
            if row is None:
                raise IntakeError(
                    404, "RESOURCE_NOT_FOUND", "The requested resource was not found."
                )
            service_request, attempt = row
            if attempt.operation_kind == "AIInterpretation":
                result = WorkflowAiServiceRequestResponse(
                    correlation_id=correlation_id,
                    result=WorkflowAiServiceRequestView(
                        attempt_id=attempt.id,
                        logical_operation_id=attempt.logical_operation_id,
                        id=service_request.id,
                        status=service_request.status,
                        description=service_request.normalized_request_description,
                        location_context=service_request.location_context,
                        timing_preference=service_request.timing_preference,
                        current_interpretation_id=service_request.current_interpretation_id,
                        version=service_request.version,
                    ),
                )
            else:
                result = WorkflowOutboundServiceRequestResponse(
                    correlation_id=correlation_id,
                    result=WorkflowOutboundServiceRequestView(
                        attempt_id=attempt.id,
                        logical_operation_id=attempt.logical_operation_id,
                        id=service_request.id,
                        status=service_request.status,
                        current_proposed_action_id=service_request.current_proposed_action_id,
                        version=service_request.version,
                    ),
                )
        else:
            result = query_service_request(session_factory, parsed_id, correlation_id)
    except IntakeError:
        raise
    except SQLAlchemyError as exc:
        raise IntakeError(
            503, "DEPENDENCY_UNAVAILABLE", "A required dependency is unavailable.", True
        ) from exc
    except Exception as exc:
        raise IntakeError(
            500, "INTERNAL_ERROR", "The request could not be completed safely."
        ) from exc
    if result is None:
        raise IntakeError(404, "RESOURCE_NOT_FOUND", "The requested resource was not found.")
    return JSONResponse(
        content=result.model_dump(mode="json"),
        headers={"X-Correlation-ID": str(correlation_id)},
    )
