"""Assigned WorkflowService callback-credential replacement route."""

import uuid
from copy import deepcopy
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ai_operations_automation.api.correlation import resolve_request_correlation
from ai_operations_automation.callback_credentials.models import (
    ReplaceCallbackCredentialRequest,
    ReplaceCallbackCredentialResponse,
)
from ai_operations_automation.callback_credentials.parsing import (
    parse_replace_callback_credential,
)
from ai_operations_automation.callback_credentials.service import (
    ReplaceCallbackCredentialService,
)
from ai_operations_automation.command_idempotency.canonicalization import canonical_command_hash
from ai_operations_automation.db.dependencies import get_session_factory
from ai_operations_automation.intake.errors import ErrorEnvelope, IntakeError
from ai_operations_automation.machine_auth.dependencies import authenticated_workflow_service
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService
from ai_operations_automation.start_ai.parsing import (
    command_idempotency_key,
    validate_json_content_type,
)

router = APIRouter()


def _dereferenced_command_schema() -> dict[str, Any]:
    schema = ReplaceCallbackCredentialRequest.model_json_schema()
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
    "/api/v1/integration-attempts/{attempt_id}/commands/replace-callback-credential",
    response_model=ReplaceCallbackCredentialResponse,
    responses={
        status: {"model": ErrorEnvelope} for status in (400, 401, 404, 409, 415, 422, 500, 503)
    },
    openapi_extra={
        "security": [{"WorkflowServiceHmac": []}],
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": _dereferenced_command_schema()}},
        },
    },
)
async def replace_callback_credential(
    attempt_id: str,
    request: Request,
    correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
    machine: Annotated[AuthenticatedWorkflowService, Depends(authenticated_workflow_service)],
) -> JSONResponse:
    raw_key = command_idempotency_key(request)
    validate_json_content_type(request)
    command = await parse_replace_callback_credential(request)
    try:
        parsed_attempt_id = uuid.UUID(attempt_id)
    except ValueError as exc:
        raise IntakeError(404, "ATTEMPT_NOT_FOUND", "The requested attempt was not found.") from exc
    outcome = ReplaceCallbackCredentialService(
        get_session_factory(request), request.app.state.callback_credential_generator
    ).execute(
        attempt_id=parsed_attempt_id,
        expected_attempt_version=command.expected_versions.integration_attempt,
        expected_credential_version=command.expected_versions.callback_credential,
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
    response = ReplaceCallbackCredentialResponse(
        correlation_id=correlation_id,
        command_id=outcome.command_id,
        **snapshot,
    )
    return JSONResponse(
        status_code=200,
        content=response.model_dump(mode="json", exclude_none=True),
        headers={"X-Correlation-ID": str(correlation_id)},
    )
