import asyncio
import json

import pytest
from fastapi import Request
from pydantic import ValidationError

from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.retry_ai.models import RetryAiRequest
from ai_operations_automation.retry_ai.parsing import (
    MAX_COMMAND_BODY_BYTES,
    command_idempotency_key,
    parse_retry_ai_command,
    validate_json_content_type,
)

ATTEMPT_ID = "00000000-0000-0000-0000-000000000001"
POLICY_ID = "00000000-0000-0000-0000-000000000002"
DIGEST = "a" * 64


def retry_payload() -> dict:
    return {
        "schema_version": "1.0",
        "expected_versions": {"service_request": 4},
        "command": {
            "failed_attempt_id": ATTEMPT_ID,
            "expected_failure_policy": {
                "policy_id": POLICY_ID,
                "semantic_version": "1.0.0",
                "revision": 1,
                "content_digest": DIGEST,
            },
        },
    }


def test_retry_ai_accepts_exact_failed_attempt_and_policy_identity() -> None:
    command = RetryAiRequest.model_validate(retry_payload())
    assert str(command.command.failed_attempt_id) == ATTEMPT_ID
    assert command.command.expected_failure_policy.semantic_version == "1.0.0"
    assert command.command.expected_failure_policy.content_digest == DIGEST


@pytest.mark.parametrize("version", [0, -1, True, 1.0, "1"])
def test_retry_ai_requires_a_strict_positive_request_version(version) -> None:
    payload = retry_payload()
    payload["expected_versions"]["service_request"] = version
    with pytest.raises(ValidationError):
        RetryAiRequest.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("policy_id", "not-a-uuid"),
        ("semantic_version", "1.0"),
        ("semantic_version", "v1.0.0"),
        ("semantic_version", "1.0.0-beta"),
        ("revision", 0),
        ("revision", True),
        ("content_digest", "A" * 64),
        ("content_digest", "a" * 63),
        ("content_digest", "g" * 64),
    ],
)
def test_retry_ai_policy_identity_is_exact_and_bounded(field, value) -> None:
    payload = retry_payload()
    payload["command"]["expected_failure_policy"][field] = value
    with pytest.raises(ValidationError):
        RetryAiRequest.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "field"),
    [
        ((), "status"),
        (("expected_versions",), "integration_attempt"),
        (("command",), "recovery_disposition"),
        (("command",), "queue"),
        (("command",), "callback_credential"),
        (("command", "expected_failure_policy"), "policy_key"),
        (("command", "expected_failure_policy"), "metadata"),
    ],
)
def test_retry_ai_envelope_rejects_unapproved_fields(path, field) -> None:
    payload = retry_payload()
    target = payload
    for part in path:
        target = target[part]
    target[field] = "forbidden"
    with pytest.raises(ValidationError):
        RetryAiRequest.model_validate(payload)


def raw_request(
    content_type: str,
    body: bytes = b"{}",
    *,
    keys: tuple[str, ...] = ("retry-ai-key-123",),
) -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    headers = [(b"content-type", content_type.encode("ascii"))]
    headers.extend((b"idempotency-key", key.encode("ascii")) for key in keys)
    return Request(
        {"type": "http", "method": "POST", "path": "/", "headers": headers},
        receive,
    )


def test_retry_ai_parser_and_transport_helpers_accept_the_exact_command() -> None:
    request = raw_request("application/json; charset=utf-8", json.dumps(retry_payload()).encode())
    assert command_idempotency_key(request) == "retry-ai-key-123"
    validate_json_content_type(request)
    parsed = asyncio.run(parse_retry_ai_command(request))
    assert type(parsed) is RetryAiRequest


@pytest.mark.parametrize("body", [b"", b"{", b"\xff", b" " * (MAX_COMMAND_BODY_BYTES + 1)])
def test_retry_ai_parser_rejects_invalid_transport_as_safe_400(body) -> None:
    with pytest.raises(IntakeError) as caught:
        asyncio.run(parse_retry_ai_command(raw_request("application/json", body)))
    assert (caught.value.status_code, caught.value.code) == (400, "INVALID_COMMAND")


@pytest.mark.parametrize("content_type", ["", "text/plain", "application/json; charset=utf-16"])
def test_retry_ai_content_type_is_strict(content_type) -> None:
    with pytest.raises(IntakeError) as caught:
        validate_json_content_type(raw_request(content_type))
    assert (caught.value.status_code, caught.value.code) == (415, "UNSUPPORTED_MEDIA_TYPE")


@pytest.mark.parametrize("keys", [(), ("short",), ("key-one-123", "key-two-123")])
def test_retry_ai_requires_one_usable_command_key(keys) -> None:
    with pytest.raises(IntakeError) as caught:
        command_idempotency_key(raw_request("application/json", keys=keys))
    assert (caught.value.status_code, caught.value.code) == (400, "MISSING_IDEMPOTENCY_KEY")


def test_retry_ai_parser_returns_safe_unknown_field_details() -> None:
    payload = retry_payload()
    payload["command"]["callback_credential"] = "secret-that-must-not-echo"
    with pytest.raises(IntakeError) as caught:
        asyncio.run(
            parse_retry_ai_command(raw_request("application/json", json.dumps(payload).encode()))
        )
    assert caught.value.status_code == 422
    assert caught.value.details == [
        {"field": "command.callback_credential", "issue_code": "UNKNOWN_FIELD"}
    ]
    assert "secret-that-must-not-echo" not in str(caught.value.details)
