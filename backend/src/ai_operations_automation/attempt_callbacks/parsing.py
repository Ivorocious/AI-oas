"""Bounded route-local parsing for AI attempt callbacks."""

import json
from email.message import Message

from fastapi import Request
from pydantic import BaseModel, ValidationError

from ai_operations_automation.attempt_callbacks.models import (
    AiRetryableFailureCallbackRequest,
    AiSuccessCallbackRequest,
    AiTerminalFailureCallbackRequest,
    OutboundRetryableFailureCallbackRequest,
    OutboundSuccessCallbackRequest,
    OutboundTerminalFailureCallbackRequest,
)
from ai_operations_automation.command_idempotency.keys import resolve_command_idempotency_key
from ai_operations_automation.intake.errors import IntakeError, safe_validation_details

MAX_CALLBACK_BODY_BYTES = 16 * 1024


def callback_idempotency_key(request: Request) -> str:
    """Apply the shared exact command-key rules at the callback boundary."""

    return resolve_command_idempotency_key(request)


def validate_json_content_type(request: Request) -> None:
    value = request.headers.get("Content-Type", "")
    message = Message()
    message["content-type"] = value
    if message.get_content_type().lower() != "application/json":
        raise IntakeError(415, "UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json.")
    parameters = message.get_params(header="content-type", failobj=[])[1:]
    if any(name.lower() != "charset" for name, _ in parameters):
        raise IntakeError(415, "UNSUPPORTED_MEDIA_TYPE", "Only UTF-8 JSON is supported.")
    charsets = [item for name, item in parameters if name.lower() == "charset"]
    if len(charsets) > 1 or (charsets and str(charsets[0]).lower() != "utf-8"):
        raise IntakeError(415, "UNSUPPORTED_MEDIA_TYPE", "Only UTF-8 JSON is supported.")


async def _parse_callback[CallbackRequest: BaseModel](
    request: Request, model_type: type[CallbackRequest]
) -> CallbackRequest:
    raw_body = await request.body()
    if not raw_body or len(raw_body) > MAX_CALLBACK_BODY_BYTES:
        raise IntakeError(400, "INVALID_COMMAND", "The callback body is invalid.")
    try:
        parsed = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise IntakeError(400, "INVALID_COMMAND", "The callback body is invalid.") from None
    try:
        return model_type.model_validate(parsed)
    except ValidationError as exc:
        details = safe_validation_details(exc.errors(include_input=False, include_url=False))
        raise IntakeError(
            422,
            "VALIDATION_FAILED",
            "The callback failed validation.",
            details=details,
        ) from exc


async def _parse_callback_choice(request: Request, ai_model, outbound_model, marker: str):
    raw_body = await request.body()
    if not raw_body or len(raw_body) > MAX_CALLBACK_BODY_BYTES:
        raise IntakeError(400, "INVALID_COMMAND", "The callback body is invalid.")
    try:
        parsed = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise IntakeError(400, "INVALID_COMMAND", "The callback body is invalid.") from None
    try:
        evidence = parsed.get("evidence") if isinstance(parsed, dict) else None
        model_type = (
            outbound_model if isinstance(evidence, dict) and marker in evidence else ai_model
        )
        return model_type.model_validate(parsed)
    except ValidationError as exc:
        details = safe_validation_details(exc.errors(include_input=False, include_url=False))
        raise IntakeError(
            422, "VALIDATION_FAILED", "The callback failed validation.", details=details
        ) from exc


async def parse_ai_success_callback(
    request: Request,
) -> AiSuccessCallbackRequest | OutboundSuccessCallbackRequest:
    return await _parse_callback_choice(
        request,
        AiSuccessCallbackRequest,
        OutboundSuccessCallbackRequest,
        "simulated_outcome",
    )


async def parse_ai_retryable_failure_callback(
    request: Request,
) -> AiRetryableFailureCallbackRequest | OutboundRetryableFailureCallbackRequest:
    return await _parse_callback_choice(
        request,
        AiRetryableFailureCallbackRequest,
        OutboundRetryableFailureCallbackRequest,
        "customer_side_effect",
    )


async def parse_ai_terminal_failure_callback(
    request: Request,
) -> AiTerminalFailureCallbackRequest | OutboundTerminalFailureCallbackRequest:
    return await _parse_callback_choice(
        request,
        AiTerminalFailureCallbackRequest,
        OutboundTerminalFailureCallbackRequest,
        "customer_side_effect",
    )
