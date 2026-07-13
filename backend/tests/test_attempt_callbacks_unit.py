import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Request
from pydantic import ValidationError

from ai_operations_automation.attempt_callbacks.models import (
    AiRetryableFailureCallbackRequest,
    AiRetryableFailureCallbackResponse,
    AiSuccessCallbackRequest,
    AiSuccessCallbackResponse,
    AiTerminalFailureCallbackRequest,
    AiTerminalFailureCallbackResponse,
)
from ai_operations_automation.attempt_callbacks.parsing import (
    MAX_CALLBACK_BODY_BYTES,
    callback_idempotency_key,
    parse_ai_retryable_failure_callback,
    parse_ai_success_callback,
    parse_ai_terminal_failure_callback,
    validate_json_content_type,
)
from ai_operations_automation.intake.errors import IntakeError

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
UUIDS = [f"00000000-0000-0000-0000-{number:012d}" for number in range(1, 10)]


def success_payload() -> dict:
    return {
        "schema_version": "1.0",
        "expected_versions": {"integration_attempt": 2},
        "evidence": {
            "result_schema_version": "1.0",
            "prompt_version": "service-request-v1",
            "provider_name": "ExampleAI",
            "model_name": "example-model",
            "adapter_name": "WorkflowServiceAIAdapter",
            "adapter_version": "1.0",
            "safe_provider_correlation": "provider-request-123",
            "latency_ms": 750,
            "token_usage": {"input_tokens": 120, "output_tokens": 45},
            "interpretation": {
                "summary": "A bounded advisory summary.",
                "suggested_category": "Repair",
                "missing_information": ["MISSING_ACCESS_WINDOW"],
                "confidence": "0.8750",
                "warning_codes": ["POSSIBLE_SAFETY_SIGNAL"],
            },
        },
    }


def retryable_failure_payload() -> dict:
    return {
        "schema_version": "1.0",
        "expected_versions": {"integration_attempt": 2},
        "evidence": {
            "failure_code": "PROVIDER_TIMEOUT",
            "adapter_version": "1.0",
            "safe_provider_correlation": "provider-request-123",
            "safe_reason_codes": ["UPSTREAM_TIMEOUT"],
            "duration_ms": 30_000,
            "retry_after_seconds": 45,
        },
    }


def terminal_failure_payload() -> dict:
    return {
        "schema_version": "1.0",
        "expected_versions": {"integration_attempt": 2},
        "evidence": {
            "failure_code": "PROVIDER_CONFIGURATION_INVALID",
            "adapter_version": "1.0",
            "safe_reason_codes": ["MODEL_CONFIGURATION_UNAVAILABLE"],
        },
    }


@pytest.mark.parametrize(
    ("model_type", "payload_factory"),
    [
        (AiSuccessCallbackRequest, success_payload),
        (AiRetryableFailureCallbackRequest, retryable_failure_payload),
        (AiTerminalFailureCallbackRequest, terminal_failure_payload),
    ],
)
def test_callback_requests_accept_only_strict_positive_attempt_versions(
    model_type, payload_factory
) -> None:
    assert model_type.model_validate(payload_factory()).expected_versions.integration_attempt == 2
    for value in (0, -1, True, 1.0, "1"):
        payload = payload_factory()
        payload["expected_versions"]["integration_attempt"] = value
        with pytest.raises(ValidationError):
            model_type.model_validate(payload)
    payload = payload_factory()
    payload["expected_versions"]["service_request"] = 1
    with pytest.raises(ValidationError):
        model_type.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    [
        "status",
        "queue",
        "priority",
        "category",
        "recovery_disposition",
        "callback_credential",
        "callback_credential_id",
        "input_hash",
        "result_hash",
        "metadata",
    ],
)
@pytest.mark.parametrize(
    ("model_type", "payload_factory"),
    [
        (AiSuccessCallbackRequest, success_payload),
        (AiRetryableFailureCallbackRequest, retryable_failure_payload),
        (AiTerminalFailureCallbackRequest, terminal_failure_payload),
    ],
)
def test_callback_evidence_rejects_caller_owned_authority_or_arbitrary_metadata(
    model_type, payload_factory, field
) -> None:
    payload = payload_factory()
    payload["evidence"][field] = "caller-supplied"
    with pytest.raises(ValidationError):
        model_type.model_validate(payload)


def test_success_interpretation_is_bounded_and_closed() -> None:
    assert (
        AiSuccessCallbackRequest.model_validate(
            success_payload()
        ).evidence.interpretation.suggested_category
        == "Repair"
    )
    for field, value in (
        ("summary", " "),
        ("summary", "x" * 2001),
        ("confidence", "-0.0001"),
        ("confidence", "1.0001"),
        ("suggested_category", "CallerDefined"),
        ("missing_information", ["CODE"] * 33),
        ("warning_codes", ["not-stable"]),
    ):
        payload = success_payload()
        payload["evidence"]["interpretation"][field] = value
        with pytest.raises(ValidationError):
            AiSuccessCallbackRequest.model_validate(payload)
    payload = success_payload()
    payload["evidence"]["interpretation"]["priority"] = "Urgent"
    with pytest.raises(ValidationError):
        AiSuccessCallbackRequest.model_validate(payload)


def test_failure_endpoints_accept_only_their_allowlisted_stable_codes() -> None:
    retryable = retryable_failure_payload()
    retryable["evidence"]["failure_code"] = "PROVIDER_CONFIGURATION_INVALID"
    with pytest.raises(ValidationError):
        AiRetryableFailureCallbackRequest.model_validate(retryable)

    terminal = terminal_failure_payload()
    terminal["evidence"]["failure_code"] = "PROVIDER_TIMEOUT"
    with pytest.raises(ValidationError):
        AiTerminalFailureCallbackRequest.model_validate(terminal)

    terminal = terminal_failure_payload()
    terminal["evidence"]["retry_after_seconds"] = 30
    with pytest.raises(ValidationError):
        AiTerminalFailureCallbackRequest.model_validate(terminal)


def common_response(result: dict, versions: dict | None = None) -> dict:
    return {
        "correlation_id": UUIDS[0],
        "command_id": UUIDS[1],
        "result": result,
        "versions": versions
        or {"service_request": 3, "logical_operation": 2, "integration_attempt": 3},
    }


def test_success_response_is_closed_and_carries_only_backend_derived_state() -> None:
    value = common_response(
        {
            "service_request_id": UUIDS[2],
            "logical_operation_id": UUIDS[3],
            "integration_attempt_id": UUIDS[4],
            "interpretation_id": UUIDS[5],
            "attempt_number": 1,
            "attempt_state": "Succeeded",
            "service_request_status": "TriagePending",
            "completed_at": NOW,
        }
    )
    assert AiSuccessCallbackResponse.model_validate(value).result.attempt_state == "Succeeded"
    with pytest.raises(ValidationError):
        AiSuccessCallbackResponse.model_validate({**value, "callback_credential": "forbidden"})


def test_retryable_response_constrains_derived_recovery_combinations() -> None:
    result = {
        "service_request_id": UUIDS[2],
        "logical_operation_id": UUIDS[3],
        "integration_attempt_id": UUIDS[4],
        "attempt_state": "RetryableFailure",
        "service_request_status": "RetryableFailure",
        "service_request_queue": "FailedRetryRequired",
        "failure_code": "PROVIDER_TIMEOUT",
        "recovery_disposition": "RetrySameOperation",
        "attempt_number": 1,
        "maximum_attempts": 3,
        "remaining_attempts": 2,
        "next_eligible_at": NOW + timedelta(seconds=30),
        "completed_at": NOW,
    }
    response = AiRetryableFailureCallbackResponse.model_validate(common_response(result))
    assert response.result.remaining_attempts == 2

    inconsistent = copy.deepcopy(result)
    inconsistent["service_request_queue"] = None
    with pytest.raises(ValidationError):
        AiRetryableFailureCallbackResponse.model_validate(common_response(inconsistent))

    exhausted = copy.deepcopy(result)
    exhausted.update(
        attempt_state="TerminalFailure",
        service_request_status="TerminalFailure",
        service_request_queue=None,
        recovery_disposition="Terminal",
        remaining_attempts=0,
        next_eligible_at=None,
    )
    assert (
        AiRetryableFailureCallbackResponse.model_validate(
            common_response(exhausted)
        ).result.attempt_state
        == "TerminalFailure"
    )


def test_terminal_response_has_no_active_queue_or_retry_time() -> None:
    result = {
        "service_request_id": UUIDS[2],
        "logical_operation_id": UUIDS[3],
        "integration_attempt_id": UUIDS[4],
        "attempt_state": "TerminalFailure",
        "service_request_status": "TerminalFailure",
        "failure_code": "PROVIDER_CONFIGURATION_INVALID",
        "recovery_disposition": "Terminal",
        "attempt_number": 1,
        "maximum_attempts": 3,
        "remaining_attempts": 2,
        "completed_at": NOW,
    }
    response = AiTerminalFailureCallbackResponse.model_validate(common_response(result))
    assert response.result.service_request_queue is None
    with pytest.raises(ValidationError):
        AiTerminalFailureCallbackResponse.model_validate(
            common_response({**result, "next_eligible_at": NOW})
        )


def raw_request(
    content_type: str,
    body: bytes = b"{}",
    *,
    idempotency_keys: tuple[str, ...] = ("callback-key-123",),
) -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    headers = [(b"content-type", content_type.encode("ascii"))]
    headers.extend((b"idempotency-key", value.encode("ascii")) for value in idempotency_keys)
    return Request(
        {"type": "http", "method": "POST", "path": "/", "headers": headers},
        receive,
    )


@pytest.mark.parametrize(
    "content_type",
    ["application/json", "application/json; charset=utf-8", "Application/JSON; charset=UTF-8"],
)
def test_callback_transport_accepts_only_supported_json_content_types(content_type) -> None:
    validate_json_content_type(raw_request(content_type))


@pytest.mark.parametrize(
    "content_type",
    ["", "text/plain", "application/json; charset=utf-16", "application/json; profile=x"],
)
def test_callback_transport_rejects_unsupported_content_types(content_type) -> None:
    with pytest.raises(IntakeError) as caught:
        validate_json_content_type(raw_request(content_type))
    assert (caught.value.status_code, caught.value.code) == (415, "UNSUPPORTED_MEDIA_TYPE")


def test_callback_transport_requires_one_bounded_idempotency_key() -> None:
    assert callback_idempotency_key(raw_request("application/json")) == "callback-key-123"
    for values in ((), ("short",), ("a" * 129,), ("key-one-123", "key-two-123")):
        with pytest.raises(IntakeError) as caught:
            callback_idempotency_key(raw_request("application/json", idempotency_keys=values))
        assert (caught.value.status_code, caught.value.code) == (400, "MISSING_IDEMPOTENCY_KEY")


@pytest.mark.parametrize(
    ("parser", "payload_factory", "model_type"),
    [
        (parse_ai_success_callback, success_payload, AiSuccessCallbackRequest),
        (
            parse_ai_retryable_failure_callback,
            retryable_failure_payload,
            AiRetryableFailureCallbackRequest,
        ),
        (
            parse_ai_terminal_failure_callback,
            terminal_failure_payload,
            AiTerminalFailureCallbackRequest,
        ),
    ],
)
def test_each_callback_parser_returns_its_exact_closed_model(
    parser, payload_factory, model_type
) -> None:
    body = json.dumps(payload_factory()).encode()
    parsed = asyncio.run(parser(raw_request("application/json", body)))
    assert type(parsed) is model_type


@pytest.mark.parametrize("body", [b"", b"{", b"\xff", b" " * (MAX_CALLBACK_BODY_BYTES + 1)])
def test_callback_invalid_transport_is_safe_400(body) -> None:
    with pytest.raises(IntakeError) as caught:
        asyncio.run(parse_ai_success_callback(raw_request("application/json", body)))
    assert (caught.value.status_code, caught.value.code) == (400, "INVALID_COMMAND")


def test_callback_validation_details_never_echo_forbidden_secret() -> None:
    payload = success_payload()
    payload["evidence"]["callback_credential"] = "secret-that-must-not-echo"
    body = json.dumps(payload).encode()
    with pytest.raises(IntakeError) as caught:
        asyncio.run(parse_ai_success_callback(raw_request("application/json", body)))
    assert caught.value.status_code == 422
    assert caught.value.details == [
        {"field": "evidence.callback_credential", "issue_code": "UNKNOWN_FIELD"}
    ]
    assert "secret-that-must-not-echo" not in str(caught.value.details)
