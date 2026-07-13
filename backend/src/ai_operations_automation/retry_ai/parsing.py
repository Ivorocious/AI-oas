"""Bounded route-local parsing for the retry-AI command."""

import json

from fastapi import Request
from pydantic import ValidationError

from ai_operations_automation.intake.errors import IntakeError, safe_validation_details
from ai_operations_automation.retry_ai.models import RetryAiRequest
from ai_operations_automation.start_ai.parsing import (
    MAX_COMMAND_BODY_BYTES,
    command_idempotency_key,
    validate_json_content_type,
)

__all__ = [
    "MAX_COMMAND_BODY_BYTES",
    "command_idempotency_key",
    "parse_retry_ai_command",
    "validate_json_content_type",
]


async def parse_retry_ai_command(request: Request) -> RetryAiRequest:
    raw_body = await request.body()
    if not raw_body or len(raw_body) > MAX_COMMAND_BODY_BYTES:
        raise IntakeError(400, "INVALID_COMMAND", "The command body is invalid.")
    try:
        parsed = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise IntakeError(400, "INVALID_COMMAND", "The command body is invalid.") from None
    try:
        return RetryAiRequest.model_validate(parsed)
    except ValidationError as exc:
        raise IntakeError(
            422,
            "VALIDATION_FAILED",
            "The command failed validation.",
            details=safe_validation_details(exc.errors(include_input=False, include_url=False)),
        ) from exc
