import asyncio
import json

import pytest
from fastapi import Request
from pydantic import ValidationError

from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.terminal_failure.models import MarkTerminalFailureRequest
from ai_operations_automation.terminal_failure.parsing import (
    MAX_COMMAND_BODY_BYTES,
    command_idempotency_key,
    parse_mark_terminal_failure_command,
    validate_json_content_type,
)

ATTEMPT_ID = "00000000-0000-0000-0000-000000000001"
RATIONALE = "The documented recovery path is no longer operationally appropriate."


def terminal_payload() -> dict:
    return {
        "schema_version": "1.0",
        "expected_versions": {"service_request": 4},
        "command": {
            "failed_attempt_id": ATTEMPT_ID,
            "rationale": RATIONALE,
        },
    }


def test_terminal_failure_accepts_exact_attempt_and_bounded_rationale() -> None:
    command = MarkTerminalFailureRequest.model_validate(terminal_payload())
    assert str(command.command.failed_attempt_id) == ATTEMPT_ID
    assert command.command.rationale == RATIONALE

    payload = terminal_payload()
    payload["command"]["rationale"] = f"  {RATIONALE}  "
    assert MarkTerminalFailureRequest.model_validate(payload).command.rationale == RATIONALE


@pytest.mark.parametrize("version", [0, -1, True, 1.0, "1"])
def test_terminal_failure_requires_a_strict_positive_request_version(version) -> None:
    payload = terminal_payload()
    payload["expected_versions"]["service_request"] = version
    with pytest.raises(ValidationError):
        MarkTerminalFailureRequest.model_validate(payload)


@pytest.mark.parametrize("rationale", ["x" * 19, " " + "x" * 19 + " ", "x" * 1001, "", None])
def test_terminal_failure_rationale_is_trimmed_and_bounded(rationale) -> None:
    payload = terminal_payload()
    payload["command"]["rationale"] = rationale
    with pytest.raises(ValidationError):
        MarkTerminalFailureRequest.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "field"),
    [
        ((), "status"),
        (("expected_versions",), "integration_attempt"),
        (("command",), "queue"),
        (("command",), "priority"),
        (("command",), "recovery_disposition"),
        (("command",), "terminal_reason"),
        (("command",), "metadata"),
    ],
)
def test_terminal_failure_envelope_rejects_canonical_or_arbitrary_fields(path, field) -> None:
    payload = terminal_payload()
    target = payload
    for part in path:
        target = target[part]
    target[field] = "forbidden"
    with pytest.raises(ValidationError):
        MarkTerminalFailureRequest.model_validate(payload)


def raw_request(
    content_type: str,
    body: bytes = b"{}",
    *,
    keys: tuple[str, ...] = ("terminal-key-123",),
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


def test_terminal_failure_parser_and_transport_helpers_accept_the_exact_command() -> None:
    request = raw_request(
        "application/json; charset=utf-8", json.dumps(terminal_payload()).encode()
    )
    assert command_idempotency_key(request) == "terminal-key-123"
    validate_json_content_type(request)
    parsed = asyncio.run(parse_mark_terminal_failure_command(request))
    assert type(parsed) is MarkTerminalFailureRequest


@pytest.mark.parametrize("body", [b"", b"{", b"\xff", b" " * (MAX_COMMAND_BODY_BYTES + 1)])
def test_terminal_failure_parser_rejects_invalid_transport_as_safe_400(body) -> None:
    with pytest.raises(IntakeError) as caught:
        asyncio.run(parse_mark_terminal_failure_command(raw_request("application/json", body)))
    assert (caught.value.status_code, caught.value.code) == (400, "INVALID_COMMAND")


@pytest.mark.parametrize("content_type", ["", "text/plain", "application/json; charset=utf-16"])
def test_terminal_failure_content_type_is_strict(content_type) -> None:
    with pytest.raises(IntakeError) as caught:
        validate_json_content_type(raw_request(content_type))
    assert (caught.value.status_code, caught.value.code) == (415, "UNSUPPORTED_MEDIA_TYPE")


@pytest.mark.parametrize("keys", [(), ("short",), ("key-one-123", "key-two-123")])
def test_terminal_failure_requires_one_usable_command_key(keys) -> None:
    with pytest.raises(IntakeError) as caught:
        command_idempotency_key(raw_request("application/json", keys=keys))
    assert (caught.value.status_code, caught.value.code) == (400, "MISSING_IDEMPOTENCY_KEY")


def test_terminal_failure_parser_returns_safe_unknown_field_details() -> None:
    payload = terminal_payload()
    payload["command"]["metadata"] = {"unrestricted": "customer data"}
    with pytest.raises(IntakeError) as caught:
        asyncio.run(
            parse_mark_terminal_failure_command(
                raw_request("application/json", json.dumps(payload).encode())
            )
        )
    assert caught.value.status_code == 422
    assert caught.value.details == [{"field": "command.metadata", "issue_code": "UNKNOWN_FIELD"}]
    assert "customer data" not in str(caught.value.details)
