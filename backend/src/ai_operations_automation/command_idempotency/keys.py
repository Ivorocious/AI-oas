"""Strict transport validation and digesting for non-intake command keys."""

import hashlib

from fastapi import Request

from ai_operations_automation.intake.errors import IntakeError

KEY_ERROR = "A usable Idempotency-Key is required."


def validate_command_idempotency_key(value: str) -> str:
    """Accept 8-128 non-whitespace visible ASCII characters only."""
    if not 8 <= len(value) <= 128 or any(not 0x21 <= ord(character) <= 0x7E for character in value):
        raise IntakeError(400, "MISSING_IDEMPOTENCY_KEY", KEY_ERROR)
    return value


def resolve_command_idempotency_key(request: Request) -> str:
    """Resolve exactly one command key without changing public-intake behavior."""
    values = request.headers.getlist("Idempotency-Key")
    if len(values) != 1:
        raise IntakeError(400, "MISSING_IDEMPOTENCY_KEY", KEY_ERROR)
    return validate_command_idempotency_key(values[0])


def command_key_digest(value: str) -> str:
    """Digest the exact validated ASCII bytes; the raw value is never retained."""
    return hashlib.sha256(validate_command_idempotency_key(value).encode("ascii")).hexdigest()
