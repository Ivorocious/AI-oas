"""Authenticated route-local transport parsing for claim/start attempt."""

import json
from email.message import Message

from fastapi import Request
from pydantic import ValidationError

from ai_operations_automation.attempt_start.models import AttemptStartRequest
from ai_operations_automation.intake.errors import IntakeError, safe_validation_details

MAX_COMMAND_BODY_BYTES = 16 * 1024


def command_idempotency_key(request: Request) -> str:
    values = request.headers.getlist("Idempotency-Key")
    if len(values) != 1:
        raise IntakeError(400, "MISSING_IDEMPOTENCY_KEY", "A usable Idempotency-Key is required.")
    value = values[0]
    if not 8 <= len(value) <= 128 or value != value.strip():
        raise IntakeError(400, "MISSING_IDEMPOTENCY_KEY", "A usable Idempotency-Key is required.")
    if any(not 0x21 <= ord(character) <= 0x7E for character in value):
        raise IntakeError(400, "MISSING_IDEMPOTENCY_KEY", "A usable Idempotency-Key is required.")
    return value


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


async def parse_attempt_start_command(request: Request) -> AttemptStartRequest:
    raw_body = await request.body()
    if not raw_body or len(raw_body) > MAX_COMMAND_BODY_BYTES:
        raise IntakeError(400, "INVALID_COMMAND", "The command body is invalid.")
    try:
        parsed = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise IntakeError(400, "INVALID_COMMAND", "The command body is invalid.") from None
    try:
        return AttemptStartRequest.model_validate(parsed)
    except ValidationError as exc:
        details = safe_validation_details(exc.errors(include_input=False, include_url=False))
        raise IntakeError(
            422,
            "VALIDATION_FAILED",
            "The command failed validation.",
            details=details,
        ) from exc
