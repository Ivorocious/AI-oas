"""Authenticated Start AI interpretation production command."""

import uuid
from copy import deepcopy
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ai_operations_automation.api.correlation import resolve_request_correlation
from ai_operations_automation.command_idempotency.canonicalization import canonical_command_hash
from ai_operations_automation.db.dependencies import get_session_factory
from ai_operations_automation.intake.errors import ErrorEnvelope, IntakeError
from ai_operations_automation.machine_auth.dependencies import authenticated_workflow_service
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService
from ai_operations_automation.start_ai.models import (
    StartAiInterpretationRequest,
    StartAiInterpretationResponse,
)
from ai_operations_automation.start_ai.parsing import (
    command_idempotency_key,
    parse_start_ai_command,
    validate_json_content_type,
)
from ai_operations_automation.start_ai.service import StartAiInterpretationService

router = APIRouter()


def _dereferenced_command_schema() -> dict[str, Any]:
    schema = StartAiInterpretationRequest.model_json_schema()
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


@router.post(
    "/api/v1/service-requests/{request_id}/commands/start-ai-interpretation",
    status_code=202,
    response_model=StartAiInterpretationResponse,
    responses={
        200: {"model": StartAiInterpretationResponse},
        400: {"model": ErrorEnvelope},
        401: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        415: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
        500: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": _dereferenced_command_schema()}},
        }
    },
)
async def start_ai_interpretation(
    request_id: str,
    request: Request,
    correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
    machine: Annotated[AuthenticatedWorkflowService, Depends(authenticated_workflow_service)],
) -> JSONResponse:
    raw_key = command_idempotency_key(request)
    validate_json_content_type(request)
    command = await parse_start_ai_command(request)
    try:
        parsed_request_id = uuid.UUID(request_id)
    except ValueError as exc:
        raise IntakeError(
            404, "RESOURCE_NOT_FOUND", "The requested resource was not found."
        ) from exc
    service = StartAiInterpretationService(
        get_session_factory(request),
        request.app.state.settings,
        request.app.state.callback_credential_generator,
    )
    outcome = service.execute(
        request_id=parsed_request_id,
        expected_request_version=command.expected_versions.service_request,
        raw_idempotency_key=raw_key,
        canonical_body_hash=canonical_command_hash(command),
        correlation_id=correlation_id,
        machine=machine,
    )
    if "error" in outcome.safe_snapshot:
        error: dict[str, Any] = outcome.safe_snapshot["error"]
        raise IntakeError(
            outcome.logical_http_status,
            error["code"],
            error["message"],
            bool(error.get("retryable", False)),
            details=error.get("details", []),
            current_versions=error.get("current_versions", {}),
        )
    snapshot = deepcopy(outcome.safe_snapshot)
    snapshot["result"]["credential_delivery"] = (
        "AlreadyIssued" if outcome.is_replay else "PlaintextIssued"
    )
    if outcome.callback_plaintext is not None:
        snapshot["result"]["callback_credential"] = outcome.callback_plaintext
    response = StartAiInterpretationResponse(
        correlation_id=correlation_id,
        command_id=outcome.command_id,
        **snapshot,
    )
    return JSONResponse(
        status_code=200 if outcome.is_replay else 202,
        content=response.model_dump(mode="json", exclude_none=True),
        headers={"X-Correlation-ID": str(correlation_id)},
    )
