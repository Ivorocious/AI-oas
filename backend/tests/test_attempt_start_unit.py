import asyncio
import json

import pytest
from fastapi import Request
from pydantic import ValidationError

from ai_operations_automation.attempt_start.models import (
    AttemptStartRequest,
    AttemptStartResponse,
)
from ai_operations_automation.attempt_start.parsing import (
    MAX_COMMAND_BODY_BYTES,
    parse_attempt_start_command,
    validate_json_content_type,
)
from ai_operations_automation.intake.errors import IntakeError


def request_model(**overrides):
    value = {
        "schema_version": "1.0",
        "expected_versions": {"integration_attempt": 1},
        "command": {},
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize(
    "payload",
    [
        request_model(extra=True),
        request_model(schema_version="2.0"),
        request_model(expected_versions={"integration_attempt": 0}),
        request_model(expected_versions={"integration_attempt": True}),
        request_model(expected_versions={"integration_attempt": 1, "extra": 2}),
        request_model(command={"state": "Running"}),
    ],
)
def test_attempt_start_schema_is_closed_exact_and_strictly_positive(payload) -> None:
    with pytest.raises(ValidationError):
        AttemptStartRequest.model_validate(payload)


def test_attempt_start_requires_empty_command_object() -> None:
    assert AttemptStartRequest.model_validate(request_model()).command.model_dump() == {}
    for command in (None, [], "", {"provider": "forbidden"}):
        with pytest.raises(ValidationError):
            AttemptStartRequest.model_validate(request_model(command=command))


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
def test_attempt_start_accepts_only_supported_json_content_types(content_type) -> None:
    validate_json_content_type(raw_request(content_type))


@pytest.mark.parametrize(
    "content_type",
    ["", "text/plain", "application/json; charset=utf-16", "application/json; profile=x"],
)
def test_attempt_start_rejects_unsupported_content_types(content_type) -> None:
    with pytest.raises(IntakeError) as captured:
        validate_json_content_type(raw_request(content_type))
    assert (captured.value.status_code, captured.value.code) == (415, "UNSUPPORTED_MEDIA_TYPE")


@pytest.mark.parametrize("body", [b"", b"{", b"\xff", b" " * (MAX_COMMAND_BODY_BYTES + 1)])
def test_attempt_start_invalid_transport_is_safe_400(body) -> None:
    with pytest.raises(IntakeError) as captured:
        asyncio.run(parse_attempt_start_command(raw_request("application/json", body)))
    assert (captured.value.status_code, captured.value.code) == (400, "INVALID_COMMAND")


def test_attempt_start_validation_details_are_safe() -> None:
    body = json.dumps(request_model(command={"callback_credential": "secret"})).encode()
    with pytest.raises(IntakeError) as captured:
        asyncio.run(parse_attempt_start_command(raw_request("application/json", body)))
    assert captured.value.status_code == 422
    assert captured.value.details == [
        {"field": "command.callback_credential", "issue_code": "UNKNOWN_FIELD"}
    ]
    assert "secret" not in str(captured.value.details)


def test_attempt_start_response_is_closed() -> None:
    value = {
        "correlation_id": "00000000-0000-0000-0000-000000000001",
        "command_id": "00000000-0000-0000-0000-000000000002",
        "result": {
            "service_request_id": "00000000-0000-0000-0000-000000000003",
            "logical_operation_id": "00000000-0000-0000-0000-000000000004",
            "integration_attempt_id": "00000000-0000-0000-0000-000000000005",
            "attempt_number": 1,
            "operation_kind": "AIInterpretation",
            "attempt_state": "Running",
            "started_at": "2026-07-13T12:00:00Z",
            "adapter_name": "WorkflowServiceAIAdapter",
            "adapter_version": "1.0",
        },
        "versions": {"integration_attempt": 2},
    }
    response = AttemptStartResponse.model_validate(value)
    assert response.result.attempt_state == "Running"
    with pytest.raises(ValidationError):
        AttemptStartResponse.model_validate({**value, "callback_credential": "forbidden"})
