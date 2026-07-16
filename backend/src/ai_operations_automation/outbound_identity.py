"""Backend-owned stable identity for the simulated outbound side effect."""

import hashlib
import uuid

OUTBOUND_KEY_SCOPE = "mock-outbound-operation-v1"


def outbound_key_reference(operation_id: uuid.UUID) -> str:
    """Derive a safe, non-secret stable reference from immutable backend identity."""
    return f"mock-outbound:v1:{operation_id}"


def outbound_key_digest(operation_id: uuid.UUID) -> str:
    return hashlib.sha256(outbound_key_reference(operation_id).encode("ascii")).hexdigest()


def outbound_binding_matches(
    operation_id: uuid.UUID, scope: str | None, digest: str | None
) -> bool:
    return scope == OUTBOUND_KEY_SCOPE and digest == outbound_key_digest(operation_id)
