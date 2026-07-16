"""Strict Start Outbound parsing."""

import json

from pydantic import ValidationError

from ai_operations_automation.intake.errors import IntakeError, safe_validation_details
from ai_operations_automation.start_ai.parsing import (
    MAX_COMMAND_BODY_BYTES,
    command_idempotency_key,
    validate_json_content_type,
)
from ai_operations_automation.start_outbound.models import StartOutboundRequest


async def parse_start_outbound_command(request):
    raw_body = await request.body()
    if not raw_body or len(raw_body) > MAX_COMMAND_BODY_BYTES:
        raise IntakeError(400, "INVALID_COMMAND", "The command body is invalid.")
    try:
        parsed = json.loads(raw_body.decode("utf-8"))
        return StartOutboundRequest.model_validate(parsed)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise IntakeError(400, "INVALID_COMMAND", "The command body is invalid.") from None
    except ValidationError as exc:
        details = safe_validation_details(exc.errors(include_input=False, include_url=False))
        raise IntakeError(
            422, "VALIDATION_FAILED", "The command failed validation.", details=details
        ) from exc


__all__ = [
    "command_idempotency_key",
    "parse_start_outbound_command",
    "validate_json_content_type",
]
