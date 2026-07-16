"""Strict retry-outbound parsing."""

import json

from pydantic import ValidationError

from ai_operations_automation.intake.errors import IntakeError, safe_validation_details
from ai_operations_automation.retry_outbound.models import RetryOutboundRequest
from ai_operations_automation.start_ai.parsing import (
    MAX_COMMAND_BODY_BYTES,
    command_idempotency_key,
    validate_json_content_type,
)


async def parse_retry_outbound_command(request) -> RetryOutboundRequest:
    raw = await request.body()
    if not raw or len(raw) > MAX_COMMAND_BODY_BYTES:
        raise IntakeError(400, "INVALID_COMMAND", "The command body is invalid.")
    try:
        parsed = json.loads(raw.decode("utf-8"))
        return RetryOutboundRequest.model_validate(parsed)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise IntakeError(400, "INVALID_COMMAND", "The command body is invalid.") from None
    except ValidationError as exc:
        raise IntakeError(
            422,
            "VALIDATION_FAILED",
            "The command failed validation.",
            details=safe_validation_details(exc.errors(include_input=False, include_url=False)),
        ) from exc


__all__ = [
    "command_idempotency_key",
    "parse_retry_outbound_command",
    "validate_json_content_type",
]
