"""Strict extraction of the opaque attempt callback credential."""

import re

from starlette.datastructures import Headers

from ai_operations_automation.intake.errors import IntakeError

ATTEMPT_CALLBACK_CREDENTIAL_HEADER = "X-Attempt-Callback-Credential"
_PRODUCTION_CREDENTIAL = re.compile(r"^[A-Za-z0-9_-]{43,256}$", re.ASCII)


def callback_forbidden() -> IntakeError:
    return IntakeError(403, "CALLBACK_FORBIDDEN", "Callback authorization failed.")


def extract_attempt_callback_credential(headers: Headers) -> str:
    """Return one exact production-shaped value without normalization."""
    values = headers.getlist(ATTEMPT_CALLBACK_CREDENTIAL_HEADER)
    if len(values) != 1:
        raise callback_forbidden()
    supplied = values[0]
    if not supplied.isascii() or _PRODUCTION_CREDENTIAL.fullmatch(supplied) is None:
        raise callback_forbidden()
    return supplied
