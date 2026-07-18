import uuid
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ai_operations_automation.attempt_callbacks.models import OutboundCallbackResponse
from ai_operations_automation.retry_outbound.models import RetryOutboundResponse

IDS = {
    name: uuid.uuid5(uuid.NAMESPACE_URL, f"https://example.com/cp5b/{name}")
    for name in (
        "approval",
        "attempt",
        "command",
        "correlation",
        "failed-attempt",
        "operation",
        "proposal",
        "request",
        "series",
        "credential",
    )
}


def outbound_callback_payload(**result_overrides):
    result = {
        "service_request_id": IDS["request"],
        "proposed_action_id": IDS["proposal"],
        "proposal_series_id": IDS["series"],
        "proposal_number": 1,
        "proposal_payload_digest": "a" * 64,
        "approval_decision_id": IDS["approval"],
        "logical_operation_id": IDS["operation"],
        "integration_attempt_id": IDS["attempt"],
        "attempt_number": 1,
        "attempt_state": "RetryableFailure",
        "proposal_state": "RetryableExecutionFailure",
        "service_request_status": "RetryableFailure",
        "service_request_queue": "FailedRetryRequired",
        "previous_service_request_queue": "StandardRequests",
        "failure_code": "WORKFLOW_FAILED_BEFORE_PROVIDER_INVOCATION",
        "recovery_disposition": "RetrySameOperation",
        "customer_side_effect": "KnownNotApplied",
        "maximum_attempts": 3,
        "remaining_attempts": 2,
        "next_eligible_at": datetime(2026, 7, 19, tzinfo=UTC),
    }
    result.update(result_overrides)
    return {
        "correlation_id": IDS["correlation"],
        "command_id": IDS["command"],
        "result": result,
        "versions": {
            "service_request": 8,
            "proposed_action": 6,
            "logical_operation": 3,
            "integration_attempt": 3,
        },
    }


def retry_outbound_payload():
    return {
        "correlation_id": IDS["correlation"],
        "command_id": IDS["command"],
        "result": {
            "service_request_id": IDS["request"],
            "proposed_action_id": IDS["proposal"],
            "proposal_series_id": IDS["series"],
            "proposal_number": 1,
            "proposal_payload_digest": "a" * 64,
            "approval_decision_id": IDS["approval"],
            "logical_operation_id": IDS["operation"],
            "failed_attempt_id": IDS["failed-attempt"],
            "integration_attempt_id": IDS["attempt"],
            "attempt_number": 2,
            "attempt_state": "Pending",
            "proposal_state": "PendingExecution",
            "service_request_status": "ActionPendingExecution",
            "service_request_queue": "StandardRequests",
            "previous_service_request_queue": "FailedRetryRequired",
            "stable_outbound_key_scope": "mock-outbound-operation-v1",
            "stable_outbound_key_reference": "operation:synthetic-safe-reference",
            "callback_credential_id": IDS["credential"],
            "callback_credential_version": 1,
            "callback_credential_expires_at": datetime(2026, 7, 19, 1, tzinfo=UTC),
            "credential_delivery": "AlreadyIssued",
        },
        "versions": {
            "service_request": 9,
            "proposed_action": 7,
            "logical_operation": 4,
            "integration_attempt": 1,
        },
    }


def test_outbound_callback_accepts_conditional_safe_previous_queue() -> None:
    response = OutboundCallbackResponse.model_validate(outbound_callback_payload())
    assert response.result.previous_service_request_queue == "StandardRequests"

    without_transition = outbound_callback_payload()
    del without_transition["result"]["previous_service_request_queue"]
    response_without_transition = OutboundCallbackResponse.model_validate(without_transition)
    assert response_without_transition.result.previous_service_request_queue is None

    unrelated = outbound_callback_payload(unrelated_customer_text="must stay closed")
    with pytest.raises(ValidationError, match="extra_forbidden"):
        OutboundCallbackResponse.model_validate(unrelated)


@pytest.mark.parametrize(
    "result_overrides",
    [
        {
            "attempt_state": "Succeeded",
            "proposal_state": "Executed",
            "service_request_status": "Completed",
            "service_request_queue": None,
            "previous_service_request_queue": "StandardRequests",
            "failure_code": None,
            "recovery_disposition": None,
            "customer_side_effect": None,
            "maximum_attempts": None,
            "remaining_attempts": None,
            "next_eligible_at": None,
            "completed_at": datetime(2026, 7, 19, tzinfo=UTC),
            "simulated_outcome": "Applied",
        },
        {},
        {
            "attempt_state": "TerminalFailure",
            "proposal_state": "TerminalExecutionFailure",
            "service_request_status": "TerminalFailure",
            "service_request_queue": "TerminalFailures",
            "recovery_disposition": "Terminal",
            "remaining_attempts": 0,
            "next_eligible_at": None,
        },
        {
            "attempt_state": "Running",
            "proposal_state": "PendingExecution",
            "service_request_status": "ActionPendingExecution",
            "service_request_queue": "StandardRequests",
            "previous_service_request_queue": None,
            "recovery_disposition": "ReconcileBeforeRetry",
            "customer_side_effect": "Unknown",
            "next_eligible_at": None,
            "reconciliation_deadline": datetime(2026, 7, 20, tzinfo=UTC),
        },
    ],
    ids=("success", "retryable-failure", "terminal-failure", "uncertainty"),
)
def test_existing_outbound_callback_shapes_remain_valid(result_overrides) -> None:
    response = OutboundCallbackResponse.model_validate(
        outbound_callback_payload(**result_overrides)
    )
    assert response.schema_version == "1.0"


def test_retry_outbound_accepts_queue_transition_replay_and_remains_closed() -> None:
    payload = retry_outbound_payload()
    first = RetryOutboundResponse.model_validate(payload)
    replay = RetryOutboundResponse.model_validate(deepcopy(payload))
    assert first.model_dump(mode="json") == replay.model_dump(mode="json")
    assert first.result.service_request_queue == "StandardRequests"
    assert first.result.previous_service_request_queue == "FailedRetryRequired"

    serialized = first.model_dump(mode="json", exclude_none=True)
    queue_evidence = {
        key: serialized["result"][key]
        for key in ("service_request_queue", "previous_service_request_queue")
    }
    assert queue_evidence == {
        "service_request_queue": "StandardRequests",
        "previous_service_request_queue": "FailedRetryRequired",
    }
    assert all(
        term not in str(queue_evidence).lower()
        for term in ("credential", "idempotency", "hmac", "provider", "customer")
    )

    unrelated = retry_outbound_payload()
    unrelated["result"]["provider_payload"] = {"unsafe": True}
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RetryOutboundResponse.model_validate(unrelated)
