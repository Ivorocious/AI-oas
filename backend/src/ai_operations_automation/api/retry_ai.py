"""Authorized bounded retry of one failed AI operation."""

import uuid
from copy import deepcopy
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ai_operations_automation.api.correlation import resolve_request_correlation
from ai_operations_automation.auth.dependencies import (
    CommandAuthority,
    authenticated_retry_authority,
)
from ai_operations_automation.command_idempotency.canonicalization import canonical_command_hash
from ai_operations_automation.db.dependencies import get_session_factory
from ai_operations_automation.intake.errors import ErrorEnvelope, IntakeError
from ai_operations_automation.retry_ai.models import RetryAiRequest, RetryAiResponse
from ai_operations_automation.retry_ai.parsing import (
    command_idempotency_key,
    parse_retry_ai_command,
    validate_json_content_type,
)
from ai_operations_automation.retry_ai.service import RetryAiService

router = APIRouter()


def _dereferenced_command_schema() -> dict[str, Any]:
    schema = RetryAiRequest.model_json_schema()
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
    "/api/v1/service-requests/{request_id}/commands/retry-ai",
    status_code=202,
    response_model=RetryAiResponse,
    responses={
        200: {"model": RetryAiResponse},
        **{
            status: {"model": ErrorEnvelope}
            for status in (400, 401, 403, 404, 409, 415, 422, 500, 503)
        },
    },
    openapi_extra={
        "security": [{"HTTPBearer": []}, {"WorkflowServiceHmac": []}],
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": _dereferenced_command_schema()}},
        },
    },
)
async def retry_ai_interpretation(
    request_id: str,
    request: Request,
    correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
    authority: Annotated[CommandAuthority, Depends(authenticated_retry_authority)],
) -> JSONResponse:
    raw_key = command_idempotency_key(request)
    validate_json_content_type(request)
    command = await parse_retry_ai_command(request)
    try:
        parsed_request_id = uuid.UUID(request_id)
    except ValueError as exc:
        raise IntakeError(
            404, "RESOURCE_NOT_FOUND", "The requested resource was not found."
        ) from exc
    outcome = RetryAiService(
        get_session_factory(request),
        request.app.state.settings,
        request.app.state.callback_credential_generator,
    ).execute(
        request_id=parsed_request_id,
        command=command,
        raw_idempotency_key=raw_key,
        canonical_body_hash=canonical_command_hash(command),
        correlation_id=correlation_id,
        authority=authority,
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
    if outcome.callback_plaintext is not None:
        snapshot["result"]["credential_delivery"] = "PlaintextIssued"
        snapshot["result"]["callback_credential"] = outcome.callback_plaintext
    elif outcome.secret_was_issued:
        snapshot["result"]["credential_delivery"] = "AlreadyIssued"
    else:
        snapshot["result"]["credential_delivery"] = "ReplacementRequired"
    response = RetryAiResponse(
        correlation_id=correlation_id,
        command_id=outcome.command_id,
        **snapshot,
    )
    return JSONResponse(
        status_code=200 if outcome.is_replay else 202,
        content=response.model_dump(mode="json", exclude_none=True),
        headers={"X-Correlation-ID": str(correlation_id)},
    )
