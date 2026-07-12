"""Protected service-request detail query."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from ai_operations_automation.api.correlation import resolve_request_correlation
from ai_operations_automation.auth.dependencies import require_service_request_reader
from ai_operations_automation.auth.models import AuthenticatedHuman
from ai_operations_automation.db.dependencies import get_session_factory
from ai_operations_automation.intake.errors import ErrorEnvelope, IntakeError
from ai_operations_automation.service_requests.models import ServiceRequestResponse
from ai_operations_automation.service_requests.query import query_service_request

router = APIRouter()


@router.get(
    "/api/v1/service-requests/{request_id}",
    response_model=ServiceRequestResponse,
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
    _human: Annotated[AuthenticatedHuman, Depends(require_service_request_reader)],
) -> JSONResponse:
    try:
        parsed_id = uuid.UUID(request_id)
    except ValueError as exc:
        raise IntakeError(
            404, "RESOURCE_NOT_FOUND", "The requested resource was not found."
        ) from exc
    try:
        result = query_service_request(get_session_factory(request), parsed_id, correlation_id)
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
