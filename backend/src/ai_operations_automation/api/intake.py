"""Controlled raw-body public intake endpoint."""

import json
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from ai_operations_automation.db.dependencies import get_session_factory
from ai_operations_automation.intake.canonicalization import (
    idempotency_key_digest,
    raw_body_fingerprint,
)
from ai_operations_automation.intake.errors import (
    ErrorEnvelope,
    IntakeError,
    safe_validation_details,
)
from ai_operations_automation.intake.models import IntakeRequest, IntakeResponse
from ai_operations_automation.intake.service import IntakeService

router = APIRouter()

ERROR_RESPONSES = {
    200: {"model": IntakeResponse},
    400: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
    415: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope},
    500: {"model": ErrorEnvelope},
    503: {"model": ErrorEnvelope},
}


def _correlation_id(request: Request) -> uuid.UUID:
    value = request.headers.get("X-Correlation-ID")
    if value is None:
        return uuid.uuid4()
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise IntakeError(
            400, "INVALID_TRANSPORT_IDENTIFIER", "X-Correlation-ID must be a UUID."
        ) from exc


def _idempotency_key(request: Request) -> str:
    value = request.headers.get("Idempotency-Key")
    if value is None or not 8 <= len(value) <= 128:
        raise IntakeError(400, "MISSING_IDEMPOTENCY_KEY", "A usable Idempotency-Key is required.")
    if value != value.strip() or any(not 0x21 <= ord(character) <= 0x7E for character in value):
        raise IntakeError(400, "MISSING_IDEMPOTENCY_KEY", "A usable Idempotency-Key is required.")
    return value


def _validate_content_type(request: Request) -> None:
    value = request.headers.get("Content-Type", "")
    parts = [part.strip().lower() for part in value.split(";")]
    if not parts or parts[0] != "application/json":
        raise IntakeError(415, "UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json.")
    if len(parts) > 1 and parts[1:] != ["charset=utf-8"]:
        raise IntakeError(415, "UNSUPPORTED_MEDIA_TYPE", "Only UTF-8 JSON is supported.")


def _error_response(error: IntakeError, correlation_id: uuid.UUID) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content=error.response(correlation_id),
        headers={"X-Correlation-ID": str(correlation_id)},
    )


@router.post(
    "/api/v1/intake/service-requests",
    status_code=201,
    response_model=IntakeResponse,
    responses=ERROR_RESPONSES,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": IntakeRequest.model_json_schema()}},
        }
    },
)
async def create_service_request_intake(request: Request) -> JSONResponse:
    correlation_id = uuid.uuid4()
    try:
        key = _idempotency_key(request)
        correlation_id = _correlation_id(request)
        _validate_content_type(request)
        key_digest = idempotency_key_digest(key)
        raw_body = await request.body()
        service = IntakeService(get_session_factory(request))
        try:
            parsed = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            try:
                service.process_rejected(
                    key_digest=key_digest,
                    correlation_id=correlation_id,
                    schema_version="unknown",
                    error_code="MALFORMED_JSON",
                    issues=[],
                    raw_body_fingerprint=raw_body_fingerprint(raw_body),
                )
            except IntakeError as error:
                return _error_response(error, correlation_id)
            return _error_response(
                IntakeError(400, "MALFORMED_JSON", "The request body is not valid JSON."),
                correlation_id,
            )

        try:
            payload = IntakeRequest.model_validate(parsed)
        except ValidationError as exc:
            details = safe_validation_details(exc.errors(include_input=False, include_url=False))
            schema_version = (
                parsed.get("schema_version", "unknown") if isinstance(parsed, dict) else "unknown"
            )
            try:
                service.process_rejected(
                    key_digest=key_digest,
                    correlation_id=correlation_id,
                    schema_version=str(schema_version)[:32],
                    error_code="INTAKE_VALIDATION_FAILED",
                    issues=details,
                )
            except IntakeError as error:
                return _error_response(error, correlation_id)
            return _error_response(
                IntakeError(
                    422,
                    "INTAKE_VALIDATION_FAILED",
                    "The intake request failed validation.",
                    details=details,
                ),
                correlation_id,
            )

        try:
            outcome = service.process_valid(payload, key_digest, correlation_id)
        except IntakeError as error:
            return _error_response(error, correlation_id)
        headers = {"X-Correlation-ID": str(correlation_id)}
        if outcome.location is not None:
            headers["Location"] = outcome.location
        return JSONResponse(
            status_code=outcome.status_code,
            content=outcome.response.model_dump(mode="json", exclude_none=True),
            headers=headers,
        )
    except IntakeError as error:
        return _error_response(error, correlation_id)
    except OperationalError:
        return _error_response(
            IntakeError(
                503, "DEPENDENCY_UNAVAILABLE", "A required dependency is unavailable.", True
            ),
            correlation_id,
        )
    except SQLAlchemyError:
        return _error_response(
            IntakeError(500, "INTERNAL_ERROR", "The request could not be completed safely."),
            correlation_id,
        )
    except Exception:
        return _error_response(
            IntakeError(500, "INTERNAL_ERROR", "The request could not be completed safely."),
            correlation_id,
        )
