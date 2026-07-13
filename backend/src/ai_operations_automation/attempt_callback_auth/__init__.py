"""Attempt-scoped callback authentication primitives."""

from ai_operations_automation.attempt_callback_auth.headers import (
    ATTEMPT_CALLBACK_CREDENTIAL_HEADER,
    extract_attempt_callback_credential,
)
from ai_operations_automation.attempt_callback_auth.models import (
    VerifiedAttemptCallbackContext,
)
from ai_operations_automation.attempt_callback_auth.verifier import (
    AttemptCallbackCredentialVerifier,
)

__all__ = [
    "ATTEMPT_CALLBACK_CREDENTIAL_HEADER",
    "AttemptCallbackCredentialVerifier",
    "VerifiedAttemptCallbackContext",
    "extract_attempt_callback_credential",
]
