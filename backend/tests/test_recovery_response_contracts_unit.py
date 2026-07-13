from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ai_operations_automation.retry_ai.models import RetryAiResponse
from ai_operations_automation.terminal_failure.models import MarkTerminalFailureResponse

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
IDS = [f"00000000-0000-0000-0000-{number:012d}" for number in range(1, 10)]


def retry_response(*, delivery: str = "PlaintextIssued") -> dict:
    result = {
        "service_request_id": IDS[2],
        "logical_operation_id": IDS[3],
        "failed_attempt_id": IDS[4],
        "integration_attempt_id": IDS[5],
        "attempt_number": 2,
        "attempt_state": "Pending",
        "service_request_status": "TriagePending",
        "failure_policy_id": IDS[7],
        "callback_credential_id": IDS[6],
        "callback_credential_version": 1,
        "callback_credential_expires_at": NOW,
        "credential_delivery": delivery,
    }
    if delivery == "PlaintextIssued":
        result["callback_credential"] = "A" * 43
    return {
        "correlation_id": IDS[0],
        "command_id": IDS[1],
        "result": result,
        "versions": {
            "service_request": 6,
            "logical_operation": 3,
            "integration_attempt": 1,
        },
    }


@pytest.mark.parametrize("delivery", ["PlaintextIssued", "AlreadyIssued", "ReplacementRequired"])
def test_retry_ai_response_supports_each_one_time_delivery_outcome(delivery) -> None:
    response = RetryAiResponse.model_validate(retry_response(delivery=delivery))
    assert response.result.credential_delivery == delivery
    assert (response.result.callback_credential is not None) == (delivery == "PlaintextIssued")
    assert response.result.attempt_state == "Pending"
    assert response.result.service_request_status == "TriagePending"


@pytest.mark.parametrize(
    ("delivery", "plaintext"),
    [
        ("PlaintextIssued", None),
        ("AlreadyIssued", "A" * 43),
        ("ReplacementRequired", "A" * 43),
    ],
)
def test_retry_ai_response_rejects_inconsistent_plaintext_delivery(delivery, plaintext) -> None:
    value = retry_response(delivery=delivery)
    if plaintext is None:
        value["result"].pop("callback_credential", None)
    else:
        value["result"]["callback_credential"] = plaintext
    with pytest.raises(ValidationError):
        RetryAiResponse.model_validate(value)


@pytest.mark.parametrize(
    ("path", "field", "value"),
    [
        (("result",), "attempt_number", 1),
        (("result",), "attempt_number", 4),
        (("result",), "attempt_state", "Running"),
        (("result",), "service_request_status", "RetryableFailure"),
        (("result",), "callback_credential_version", 2),
        (("result",), "callback_credential", "." * 43),
        (("versions",), "integration_attempt", 2),
        (("versions",), "service_request", True),
        ((), "credential_hash", "0" * 64),
    ],
)
def test_retry_ai_response_is_closed_and_exact(path, field, value) -> None:
    payload = retry_response()
    target = payload
    for part in path:
        target = target[part]
    target[field] = value
    with pytest.raises(ValidationError):
        RetryAiResponse.model_validate(payload)


def terminal_response() -> dict:
    return {
        "correlation_id": IDS[0],
        "command_id": IDS[1],
        "result": {
            "service_request_id": IDS[2],
            "failed_attempt_id": IDS[3],
            "service_request_status": "TerminalFailure",
            "service_request_queue": None,
            "failure_code": "PROVIDER_TIMEOUT",
            "terminal_disposition_code": "MANAGER_TERMINAL_DISPOSITION",
            "terminal_at": NOW,
        },
        "versions": {"service_request": 7},
    }


@pytest.mark.parametrize(
    "disposition",
    ["MANAGER_TERMINAL_DISPOSITION", "ADMINISTRATOR_TERMINAL_DISPOSITION"],
)
def test_mark_terminal_failure_response_has_no_active_queue(disposition) -> None:
    value = terminal_response()
    value["result"]["terminal_disposition_code"] = disposition
    response = MarkTerminalFailureResponse.model_validate(value)
    assert response.result.service_request_status == "TerminalFailure"
    assert response.result.service_request_queue is None
    assert response.result.terminal_disposition_code == disposition


@pytest.mark.parametrize(
    ("path", "field", "value"),
    [
        (("result",), "service_request_status", "RetryableFailure"),
        (("result",), "service_request_queue", "FailedRetryRequired"),
        (("result",), "failure_code", "not-stable"),
        (("result",), "failure_code", "FAKE_FAILURE"),
        (("result",), "terminal_disposition_code", "OPERATIONS_TERMINAL_DISPOSITION"),
        (("result",), "terminal_at", "2026-07-13T12:00:00"),
        (("versions",), "service_request", 0),
        (("versions",), "service_request", True),
        ((), "rationale", "must not be projected"),
    ],
)
def test_mark_terminal_failure_response_is_closed_and_exact(path, field, value) -> None:
    payload = terminal_response()
    target = payload
    for part in path:
        target = target[part]
    target[field] = value
    with pytest.raises(ValidationError):
        MarkTerminalFailureResponse.model_validate(payload)
