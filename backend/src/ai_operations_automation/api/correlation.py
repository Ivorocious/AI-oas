"""Narrow request-correlation resolution for protected routes."""

import uuid

from fastapi import Request

from ai_operations_automation.intake.errors import IntakeError


def resolve_request_correlation(request: Request) -> uuid.UUID:
    """Validate once before authentication and retain the trusted request value."""
    existing = getattr(request.state, "correlation_id", None)
    if isinstance(existing, uuid.UUID):
        return existing
    value = request.headers.get("X-Correlation-ID")
    try:
        correlation_id = uuid.UUID(value) if value is not None else uuid.uuid4()
    except ValueError as exc:
        raise IntakeError(
            400, "INVALID_TRANSPORT_IDENTIFIER", "X-Correlation-ID must be a UUID."
        ) from exc
    request.state.correlation_id = correlation_id
    return correlation_id
