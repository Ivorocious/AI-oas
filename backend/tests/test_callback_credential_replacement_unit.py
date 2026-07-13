import asyncio
import json

import pytest
from fastapi import Request
from pydantic import ValidationError

from ai_operations_automation.callback_credentials.models import (
    ReplaceCallbackCredentialRequest,
    ReplaceCallbackCredentialResponse,
)
from ai_operations_automation.callback_credentials.parsing import (
    parse_replace_callback_credential,
)
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.start_ai.parsing import MAX_COMMAND_BODY_BYTES


def replacement_payload(**overrides) -> dict:
    value = {
        "schema_version": "1.0",
        "expected_versions": {
            "integration_attempt": 2,
            "callback_credential": 1,
        },
        "command": {},
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize(
    "payload",
    [
        replacement_payload(extra=True),
        replacement_payload(schema_version="2.0"),
        replacement_payload(expected_versions={"integration_attempt": 2}),
        replacement_payload(
            expected_versions={"integration_attempt": 2, "callback_credential": 1, "extra": 1}
        ),
        replacement_payload(expected_versions={"integration_attempt": 0, "callback_credential": 1}),
        replacement_payload(expected_versions={"integration_attempt": 2, "callback_credential": 0}),
        replacement_payload(
            expected_versions={"integration_attempt": True, "callback_credential": 1}
        ),
        replacement_payload(
            expected_versions={"integration_attempt": 2, "callback_credential": True}
        ),
        replacement_payload(command={"callback_credential": "forbidden"}),
        replacement_payload(command={"state": "Running"}),
        replacement_payload(command={"credential_version": 2}),
        replacement_payload(command={"expires_at": "2099-01-01T00:00:00Z"}),
    ],
)
def test_replacement_request_is_closed_and_versions_are_strictly_positive(payload) -> None:
    with pytest.raises(ValidationError):
        ReplaceCallbackCredentialRequest.model_validate(payload)


def test_replacement_request_contains_only_expected_versions_and_empty_command() -> None:
    command = ReplaceCallbackCredentialRequest.model_validate(replacement_payload())
    assert command.model_dump(mode="json") == replacement_payload()
    assert command.command.model_dump() == {}


def raw_request(body: bytes) -> Request:
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
            "path": "/api/v1/integration-attempts/example/commands/replace-callback-credential",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )


def test_replacement_parser_returns_the_exact_closed_request() -> None:
    body = json.dumps(replacement_payload()).encode()
    parsed = asyncio.run(parse_replace_callback_credential(raw_request(body)))
    assert type(parsed) is ReplaceCallbackCredentialRequest
    assert parsed.expected_versions.integration_attempt == 2
    assert parsed.expected_versions.callback_credential == 1


@pytest.mark.parametrize("body", [b"", b"{", b"\xff", b" " * (MAX_COMMAND_BODY_BYTES + 1)])
def test_replacement_parser_rejects_invalid_transport_without_echoing_input(body) -> None:
    with pytest.raises(IntakeError) as caught:
        asyncio.run(parse_replace_callback_credential(raw_request(body)))
    assert (caught.value.status_code, caught.value.code) == (400, "INVALID_COMMAND")
    sample = body[:32].decode("utf-8", errors="ignore").strip()
    if sample:
        assert sample not in str(caught.value)


def test_replacement_parser_reports_safe_unknown_field_details() -> None:
    body = json.dumps(
        replacement_payload(command={"callback_credential": "secret-that-must-not-echo"})
    ).encode()
    with pytest.raises(IntakeError) as caught:
        asyncio.run(parse_replace_callback_credential(raw_request(body)))
    assert caught.value.status_code == 422
    assert caught.value.details == [
        {"field": "command.callback_credential", "issue_code": "UNKNOWN_FIELD"}
    ]
    assert "secret-that-must-not-echo" not in str(caught.value.details)


def response_payload(*, replay: bool) -> dict:
    result = {
        "integration_attempt_id": "00000000-0000-0000-0000-000000000003",
        "attempt_state": "Running",
        "callback_credential_id": "00000000-0000-0000-0000-000000000004",
        "callback_credential_version": 2,
        "callback_credential_expires_at": "2026-07-14T12:00:00Z",
        "credential_delivery": "AlreadyIssued" if replay else "PlaintextIssued",
    }
    if not replay:
        result["callback_credential"] = "A" * 43
    return {
        "correlation_id": "00000000-0000-0000-0000-000000000001",
        "command_id": "00000000-0000-0000-0000-000000000002",
        "result": result,
        "versions": {"integration_attempt": 2, "callback_credential": 2},
    }


def test_replacement_response_supports_plaintext_once_and_safe_replay_shapes() -> None:
    first = ReplaceCallbackCredentialResponse.model_validate(response_payload(replay=False))
    replay = ReplaceCallbackCredentialResponse.model_validate(response_payload(replay=True))
    assert first.result.credential_delivery == "PlaintextIssued"
    assert first.result.callback_credential == "A" * 43
    assert replay.result.credential_delivery == "AlreadyIssued"
    assert replay.result.callback_credential is None


def test_replacement_response_is_closed_and_versioned() -> None:
    value = response_payload(replay=True)
    with pytest.raises(ValidationError):
        ReplaceCallbackCredentialResponse.model_validate({**value, "credential_hash": "0" * 64})
    with pytest.raises(ValidationError):
        ReplaceCallbackCredentialResponse.model_validate(
            {**value, "versions": {"integration_attempt": 2, "callback_credential": 1}}
        )
