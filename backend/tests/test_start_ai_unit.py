import asyncio
import base64
import hashlib
import json

import pytest
from fastapi import Request
from pydantic import ValidationError

from ai_operations_automation.config import Settings
from ai_operations_automation.db.models.intake import ServiceRequest
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.start_ai.credentials import (
    callback_credential_hash,
    generate_callback_credential,
)
from ai_operations_automation.start_ai.hashing import (
    ai_configuration_hash,
    ai_input_hash,
    ai_input_material,
)
from ai_operations_automation.start_ai.models import (
    StartAiInterpretationRequest,
    StartAiInterpretationResponse,
)
from ai_operations_automation.start_ai.parsing import (
    MAX_COMMAND_BODY_BYTES,
    parse_start_ai_command,
    validate_json_content_type,
)


def request_model(**overrides):
    value = {
        "schema_version": "1.0",
        "expected_versions": {"service_request": 1},
        "command": {},
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize(
    "payload",
    [
        request_model(extra=True),
        request_model(schema_version="2.0"),
        request_model(expected_versions={"service_request": 0}),
        request_model(expected_versions={"service_request": True}),
        request_model(expected_versions={"service_request": 1, "extra": 2}),
        request_model(command={"provider": "forbidden"}),
    ],
)
def test_command_schema_is_closed_exact_and_positive(payload) -> None:
    with pytest.raises(ValidationError):
        StartAiInterpretationRequest.model_validate(payload)


def test_command_requires_empty_command_object() -> None:
    assert StartAiInterpretationRequest.model_validate(request_model()).command.model_dump() == {}
    for command in (None, [], "", {"prompt": "forbidden"}):
        with pytest.raises(ValidationError):
            StartAiInterpretationRequest.model_validate(request_model(command=command))


def raw_request(content_type: str, body: bytes = b"{}") -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"content-type", content_type.encode("ascii"))],
        },
        receive,
    )


@pytest.mark.parametrize(
    "content_type",
    ["application/json", "application/json; charset=utf-8", "Application/JSON; charset=UTF-8"],
)
def test_valid_json_content_types(content_type) -> None:
    validate_json_content_type(raw_request(content_type))


@pytest.mark.parametrize(
    "content_type",
    ["", "text/plain", "application/json; charset=utf-16", "application/json; profile=x"],
)
def test_unsupported_content_types(content_type) -> None:
    with pytest.raises(IntakeError) as captured:
        validate_json_content_type(raw_request(content_type))
    assert (captured.value.status_code, captured.value.code) == (415, "UNSUPPORTED_MEDIA_TYPE")


@pytest.mark.parametrize("body", [b"", b"{", b"\xff", b" " * (MAX_COMMAND_BODY_BYTES + 1)])
def test_invalid_command_transport_is_safe_400(body) -> None:
    with pytest.raises(IntakeError) as captured:
        asyncio.run(parse_start_ai_command(raw_request("application/json", body)))
    assert (captured.value.status_code, captured.value.code) == (400, "INVALID_COMMAND")


def test_validation_details_are_safe() -> None:
    body = json.dumps(request_model(command={"api_key": "secret-value"})).encode()
    with pytest.raises(IntakeError) as captured:
        asyncio.run(parse_start_ai_command(raw_request("application/json", body)))
    assert captured.value.status_code == 422
    assert captured.value.details == [{"field": "command.api_key", "issue_code": "UNKNOWN_FIELD"}]
    assert "secret-value" not in str(captured.value.details)


def service_request(**overrides) -> ServiceRequest:
    values = {
        "normalized_request_description": "Repair the leaking kitchen pipe",
        "location_context": None,
        "timing_preference": None,
    }
    values.update(overrides)
    return ServiceRequest(**values)


def test_input_hash_is_deterministic_preserves_nulls_and_excludes_contact() -> None:
    request = service_request()
    assert ai_input_hash(request) == ai_input_hash(request)
    material = ai_input_material(request)
    assert material["location_context"] is None
    assert material["timing_preference"] is None
    assert set(material) == {
        "input_schema_version",
        "location_context",
        "request_description",
        "timing_preference",
    }
    assert all(term not in material for term in ("contact", "email", "phone"))
    assert ai_input_hash(request) != ai_input_hash(service_request(location_context="Second floor"))


def test_configuration_hash_is_deterministic_and_every_identity_participates() -> None:
    settings = Settings(_env_file=None)
    baseline = ai_configuration_hash(settings)
    assert baseline == ai_configuration_hash(settings)
    fields = {
        "ai_interpretation_prompt_version": "prompt-v2",
        "ai_interpretation_result_schema_version": "schema-v2",
        "ai_provider_name": "ProviderTwo",
        "ai_model_name": "model-v2",
        "ai_adapter_name": "AdapterTwo",
        "ai_adapter_version": "2.0",
    }
    for field, value in fields.items():
        assert ai_configuration_hash(settings.model_copy(update={field: value})) != baseline


def test_secure_callback_credential_shape_entropy_and_hash() -> None:
    token = generate_callback_credential()
    assert len(base64.urlsafe_b64decode(token + "=")) == 32
    assert token.isascii() and "=" not in token
    assert callback_credential_hash(token) == hashlib.sha256(token.encode("ascii")).hexdigest()
    assert callback_credential_hash(token) == callback_credential_hash(token)


def test_replay_response_can_exclude_plaintext() -> None:
    response = StartAiInterpretationResponse.model_validate(
        {
            "correlation_id": "00000000-0000-0000-0000-000000000001",
            "command_id": "00000000-0000-0000-0000-000000000002",
            "result": {
                "service_request_id": "00000000-0000-0000-0000-000000000003",
                "logical_operation_id": "00000000-0000-0000-0000-000000000004",
                "integration_attempt_id": "00000000-0000-0000-0000-000000000005",
                "attempt_number": 1,
                "attempt_state": "Pending",
                "callback_credential_id": "00000000-0000-0000-0000-000000000006",
                "callback_credential_version": 1,
                "callback_credential_expires_at": "2026-07-13T12:30:00Z",
                "credential_delivery": "AlreadyIssued",
            },
            "versions": {
                "service_request": 2,
                "logical_operation": 1,
                "integration_attempt": 1,
            },
        }
    )
    dumped = response.model_dump(mode="json", exclude_none=True)
    assert "callback_credential" not in dumped["result"]
