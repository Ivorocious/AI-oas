"""Bounded route-local parsing for complete-human-review."""

import json

from fastapi import Request
from pydantic import ValidationError

from ai_operations_automation.human_review.models import CompleteHumanReviewRequest
from ai_operations_automation.intake.errors import IntakeError, safe_validation_details
from ai_operations_automation.start_ai.parsing import (
    MAX_COMMAND_BODY_BYTES,
    command_idempotency_key,
    validate_json_content_type,
)

__all__ = [
    "MAX_COMMAND_BODY_BYTES",
    "command_idempotency_key",
    "parse_complete_human_review_command",
    "validate_json_content_type",
]


async def parse_complete_human_review_command(request: Request) -> CompleteHumanReviewRequest:
    raw_body = await request.body()
    if not raw_body or len(raw_body) > MAX_COMMAND_BODY_BYTES:
        raise IntakeError(400, "INVALID_COMMAND", "The command body is invalid.")
    try:
        parsed = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise IntakeError(400, "INVALID_COMMAND", "The command body is invalid.") from None
    try:
        return CompleteHumanReviewRequest.model_validate(parsed)
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_url=False)
        unsupported_fact = any(
            error.get("type") == "extra_forbidden"
            and tuple(error.get("loc", ()))[:1] == ("reviewed_facts",)
            for error in errors
        )
        raise IntakeError(
            422,
            "REVIEW_FACT_NOT_ALLOWED" if unsupported_fact else "VALIDATION_FAILED",
            (
                "One or more reviewed facts are not allowed."
                if unsupported_fact
                else "The command failed validation."
            ),
            details=safe_validation_details(errors),
        ) from exc
