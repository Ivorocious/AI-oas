"""Human-authenticated duplicate-candidate resolution transport."""

import uuid
from copy import deepcopy
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ai_operations_automation.api.correlation import resolve_request_correlation
from ai_operations_automation.auth.dependencies import require_service_request_reader
from ai_operations_automation.auth.models import AuthenticatedHuman
from ai_operations_automation.command_idempotency.canonicalization import canonical_command_hash
from ai_operations_automation.duplicate_resolution.contracts import DuplicateResolutionService
from ai_operations_automation.duplicate_resolution.models import (
    ResolveDuplicateRequest,
    ResolveDuplicateResponse,
)
from ai_operations_automation.duplicate_resolution.parsing import (
    command_idempotency_key,
    parse_resolve_duplicate_command,
    validate_json_content_type,
)
from ai_operations_automation.intake.errors import ErrorEnvelope, IntakeError

router = APIRouter()


def get_duplicate_resolution_service(request: Request) -> DuplicateResolutionService:
    """Resolve the domain service explicitly wired by the application factory."""
    service = getattr(request.app.state, "duplicate_resolution_service", None)
    if service is None:
        raise IntakeError(
            503,
            "DEPENDENCY_UNAVAILABLE",
            "A required dependency is unavailable.",
            True,
        )
    return cast(DuplicateResolutionService, service)


def _dereferenced_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def resolve(value: Any) -> Any:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                return resolve(deepcopy(definitions[reference.rsplit("/", 1)[-1]]))
            return {key: resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [resolve(item) for item in value]
        return value

    return resolve(schema)


def _raise_snapshot_error(status: int, snapshot: dict[str, Any]) -> None:
    if "error" not in snapshot:
        return
    error: dict[str, Any] = snapshot["error"]
    raise IntakeError(
        status,
        error["code"],
        error["message"],
        bool(error.get("retryable", False)),
        details=error.get("details", []),
        current_versions=error.get("current_versions", {}),
    )


@router.post(
    "/api/v1/service-requests/{request_id}/duplicate-candidates/{candidate_id}/commands/resolve",
    operation_id="resolve_duplicate_candidate",
    summary="Resolve one current duplicate candidate",
    description=(
        "Records a bounded human disposition. The backend derives request status and queue; "
        "the authenticated actor and current application role are never accepted from JSON."
    ),
    response_model=ResolveDuplicateResponse,
    responses={
        status: {"model": ErrorEnvelope} for status in (400, 401, 403, 404, 409, 415, 422, 500, 503)
    },
    openapi_extra={
        "parameters": [
            {
                "name": "Idempotency-Key",
                "in": "header",
                "required": True,
                "description": "Opaque command key scoped to this actor, route, and target.",
                "schema": {"type": "string", "minLength": 8, "maxLength": 128},
            },
            {
                "name": "X-Correlation-ID",
                "in": "header",
                "required": False,
                "description": "Optional UUID correlation identifier echoed by the API.",
                "schema": {"type": "string", "format": "uuid"},
            },
        ],
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": _dereferenced_schema(ResolveDuplicateRequest)}
            },
        },
    },
)
async def resolve_duplicate_candidate(
    request_id: str,
    candidate_id: str,
    request: Request,
    correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
    actor: Annotated[AuthenticatedHuman, Depends(require_service_request_reader)],
    service: Annotated[
        DuplicateResolutionService,
        Depends(get_duplicate_resolution_service),
    ],
) -> JSONResponse:
    raw_key = command_idempotency_key(request)
    validate_json_content_type(request)
    command = await parse_resolve_duplicate_command(request)
    try:
        parsed_request_id = uuid.UUID(request_id)
        parsed_candidate_id = uuid.UUID(candidate_id)
    except ValueError as exc:
        raise IntakeError(
            404,
            "RESOURCE_NOT_FOUND",
            "The requested resource was not found.",
        ) from exc
    outcome = service.execute(
        request_id=parsed_request_id,
        candidate_id=parsed_candidate_id,
        command=command,
        raw_idempotency_key=raw_key,
        canonical_body_hash=canonical_command_hash(command),
        correlation_id=correlation_id,
        actor=actor,
    )
    _raise_snapshot_error(outcome.logical_http_status, outcome.safe_snapshot)
    response = ResolveDuplicateResponse(
        correlation_id=correlation_id,
        command_id=outcome.command_id,
        **deepcopy(outcome.safe_snapshot),
    )
    return JSONResponse(
        status_code=outcome.logical_http_status,
        content=response.model_dump(mode="json"),
        headers={"X-Correlation-ID": str(correlation_id)},
    )
