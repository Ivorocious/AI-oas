"""Assigned WorkflowService AI-attempt result callbacks."""

import uuid
from copy import deepcopy
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ai_operations_automation.api.correlation import resolve_request_correlation
from ai_operations_automation.attempt_callback_auth.headers import (
    extract_attempt_callback_credential,
)
from ai_operations_automation.attempt_callbacks.models import (
    AiRetryableFailureCallbackRequest,
    AiRetryableFailureCallbackResponse,
    AiSuccessCallbackRequest,
    AiSuccessCallbackResponse,
    AiTerminalFailureCallbackRequest,
    AiTerminalFailureCallbackResponse,
    OutboundCallbackResponse,
    OutboundRetryableFailureCallbackRequest,
    OutboundSuccessCallbackRequest,
    OutboundTerminalFailureCallbackRequest,
)
from ai_operations_automation.attempt_callbacks.outbound_service import (
    OutboundAttemptCallbackService,
)
from ai_operations_automation.attempt_callbacks.parsing import (
    callback_idempotency_key,
    parse_ai_retryable_failure_callback,
    parse_ai_success_callback,
    parse_ai_terminal_failure_callback,
    validate_json_content_type,
)
from ai_operations_automation.attempt_callbacks.service import AiAttemptCallbackService
from ai_operations_automation.command_idempotency.canonicalization import canonical_command_hash
from ai_operations_automation.db.dependencies import get_session_factory
from ai_operations_automation.intake.errors import ErrorEnvelope, IntakeError
from ai_operations_automation.machine_auth.dependencies import authenticated_workflow_service
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService

router = APIRouter()


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


def _callback_schema(ai_model: type[BaseModel], outbound_model: type[BaseModel]) -> dict[str, Any]:
    return {"oneOf": [_dereferenced_schema(ai_model), _dereferenced_schema(outbound_model)]}


@router.post(
    "/api/v1/integration-attempts/{attempt_id}/callbacks/succeeded",
    response_model=AiSuccessCallbackResponse | OutboundCallbackResponse,
    responses={
        status: {"model": ErrorEnvelope} for status in (400, 401, 403, 404, 409, 415, 422, 500, 503)
    },
    openapi_extra={
        "security": [{"WorkflowServiceHmac": [], "AttemptCallbackCredential": []}],
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": _callback_schema(
                        AiSuccessCallbackRequest, OutboundSuccessCallbackRequest
                    )
                }
            },
        },
    },
)
async def complete_ai_success_callback(
    attempt_id: str,
    request: Request,
    correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
    machine: Annotated[AuthenticatedWorkflowService, Depends(authenticated_workflow_service)],
) -> JSONResponse:
    raw_key = callback_idempotency_key(request)
    validate_json_content_type(request)
    command = await parse_ai_success_callback(request)
    supplied_credential = extract_attempt_callback_credential(request.headers)
    try:
        parsed_attempt_id = uuid.UUID(attempt_id)
    except ValueError as exc:
        raise IntakeError(404, "ATTEMPT_NOT_FOUND", "The requested attempt was not found.") from exc
    service = (
        OutboundAttemptCallbackService(get_session_factory(request))
        if isinstance(command, OutboundSuccessCallbackRequest)
        else AiAttemptCallbackService(get_session_factory(request))
    )
    outcome = service.succeed(
        attempt_id=parsed_attempt_id,
        command=command,
        raw_idempotency_key=raw_key,
        canonical_body_hash=canonical_command_hash(command),
        correlation_id=correlation_id,
        machine=machine,
        supplied_credential=supplied_credential,
    )
    _raise_snapshot_error(outcome.logical_http_status, outcome.safe_snapshot)
    response_type = (
        OutboundCallbackResponse
        if isinstance(command, OutboundSuccessCallbackRequest)
        else AiSuccessCallbackResponse
    )
    response = response_type(
        correlation_id=correlation_id,
        command_id=outcome.command_id,
        **deepcopy(outcome.safe_snapshot),
    )
    return JSONResponse(
        status_code=200,
        content=response.model_dump(mode="json"),
        headers={"X-Correlation-ID": str(correlation_id)},
    )


@router.post(
    "/api/v1/integration-attempts/{attempt_id}/callbacks/retryable-failure",
    response_model=AiRetryableFailureCallbackResponse | OutboundCallbackResponse,
    responses={
        status: {"model": ErrorEnvelope} for status in (400, 401, 403, 404, 409, 415, 422, 500, 503)
    },
    openapi_extra={
        "security": [{"WorkflowServiceHmac": [], "AttemptCallbackCredential": []}],
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": _callback_schema(
                        AiRetryableFailureCallbackRequest,
                        OutboundRetryableFailureCallbackRequest,
                    )
                }
            },
        },
    },
)
async def complete_ai_retryable_failure_callback(
    attempt_id: str,
    request: Request,
    correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
    machine: Annotated[AuthenticatedWorkflowService, Depends(authenticated_workflow_service)],
) -> JSONResponse:
    raw_key = callback_idempotency_key(request)
    validate_json_content_type(request)
    command = await parse_ai_retryable_failure_callback(request)
    supplied_credential = extract_attempt_callback_credential(request.headers)
    try:
        parsed_attempt_id = uuid.UUID(attempt_id)
    except ValueError as exc:
        raise IntakeError(404, "ATTEMPT_NOT_FOUND", "The requested attempt was not found.") from exc
    service = (
        OutboundAttemptCallbackService(get_session_factory(request))
        if isinstance(command, OutboundRetryableFailureCallbackRequest)
        else AiAttemptCallbackService(get_session_factory(request))
    )
    outcome = service.retryable_failure(
        attempt_id=parsed_attempt_id,
        command=command,
        raw_idempotency_key=raw_key,
        canonical_body_hash=canonical_command_hash(command),
        correlation_id=correlation_id,
        machine=machine,
        supplied_credential=supplied_credential,
    )
    _raise_snapshot_error(outcome.logical_http_status, outcome.safe_snapshot)
    response_type = (
        OutboundCallbackResponse
        if isinstance(command, OutboundRetryableFailureCallbackRequest)
        else AiRetryableFailureCallbackResponse
    )
    response = response_type(
        correlation_id=correlation_id,
        command_id=outcome.command_id,
        **deepcopy(outcome.safe_snapshot),
    )
    return JSONResponse(
        status_code=200,
        content=response.model_dump(mode="json"),
        headers={"X-Correlation-ID": str(correlation_id)},
    )


@router.post(
    "/api/v1/integration-attempts/{attempt_id}/callbacks/terminal-failure",
    response_model=AiTerminalFailureCallbackResponse | OutboundCallbackResponse,
    responses={
        status: {"model": ErrorEnvelope} for status in (400, 401, 403, 404, 409, 415, 422, 500, 503)
    },
    openapi_extra={
        "security": [{"WorkflowServiceHmac": [], "AttemptCallbackCredential": []}],
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": _callback_schema(
                        AiTerminalFailureCallbackRequest,
                        OutboundTerminalFailureCallbackRequest,
                    )
                }
            },
        },
    },
)
async def complete_ai_terminal_failure_callback(
    attempt_id: str,
    request: Request,
    correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
    machine: Annotated[AuthenticatedWorkflowService, Depends(authenticated_workflow_service)],
) -> JSONResponse:
    raw_key = callback_idempotency_key(request)
    validate_json_content_type(request)
    command = await parse_ai_terminal_failure_callback(request)
    supplied_credential = extract_attempt_callback_credential(request.headers)
    try:
        parsed_attempt_id = uuid.UUID(attempt_id)
    except ValueError as exc:
        raise IntakeError(404, "ATTEMPT_NOT_FOUND", "The requested attempt was not found.") from exc
    service = (
        OutboundAttemptCallbackService(get_session_factory(request))
        if isinstance(command, OutboundTerminalFailureCallbackRequest)
        else AiAttemptCallbackService(get_session_factory(request))
    )
    outcome = service.terminal_failure(
        attempt_id=parsed_attempt_id,
        command=command,
        raw_idempotency_key=raw_key,
        canonical_body_hash=canonical_command_hash(command),
        correlation_id=correlation_id,
        machine=machine,
        supplied_credential=supplied_credential,
    )
    _raise_snapshot_error(outcome.logical_http_status, outcome.safe_snapshot)
    response_type = (
        OutboundCallbackResponse
        if isinstance(command, OutboundTerminalFailureCallbackRequest)
        else AiTerminalFailureCallbackResponse
    )
    response = response_type(
        correlation_id=correlation_id,
        command_id=outcome.command_id,
        **deepcopy(outcome.safe_snapshot),
    )
    return JSONResponse(
        status_code=200,
        content=response.model_dump(mode="json"),
        headers={"X-Correlation-ID": str(correlation_id)},
    )
