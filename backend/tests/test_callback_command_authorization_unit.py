import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ai_operations_automation.attempt_callbacks.authorization import CallbackCommandAuthorizer
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
TOKEN = "A" * 43


def machine() -> AuthenticatedWorkflowService:
    return AuthenticatedWorkflowService(
        machine_identity_id=uuid.uuid4(),
        stable_service_id="workflow.test",
        environment="test",
        service_type="WorkflowService",
        credential_id=uuid.uuid4(),
        credential_version=1,
    )


def authorize(session: Session) -> None:
    CallbackCommandAuthorizer(session).authorize(
        attempt_id=uuid.uuid4(),
        machine=machine(),
        supplied_credential=TOKEN,
        raw_idempotency_key="callback-command-key",
        canonical_body_hash="0" * 64,
        correlation_id=uuid.uuid4(),
        command_intent="RecordAiSuccess",
        route_template="/api/v1/integration-attempts/{attempt_id}/callbacks/succeeded",
        expected_operation_kind="AIInterpretation",
    )


def test_callback_authorizer_requires_a_caller_owned_explicit_transaction_before_sql() -> None:
    session = Session()
    with pytest.raises(IntakeError) as caught:
        authorize(session)
    assert (caught.value.status_code, caught.value.code) == (500, "INTERNAL_ERROR")


def test_callback_authorizer_rejects_implicit_autobegin_before_verification_sql() -> None:
    session = Session(create_engine("sqlite+pysqlite:///:memory:"))
    try:
        session.connection()
        with pytest.raises(IntakeError) as caught:
            authorize(session)
        assert (caught.value.status_code, caught.value.code) == (500, "INTERNAL_ERROR")
    finally:
        session.rollback()
        session.close()


def callback_scope(*, credential_state: str = "Active", attempt_state: str = "Running"):
    attempt_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    attempt = SimpleNamespace(
        id=attempt_id,
        state=attempt_state,
        operation_kind="AIInterpretation",
        assigned_workflow_service="workflow.test",
        workflow_environment="test",
        callback_authorization_deadline=NOW + timedelta(minutes=5),
    )
    credential = SimpleNamespace(
        id=credential_id,
        integration_attempt_id=attempt_id,
        operation_kind="AIInterpretation",
        workflow_service_identity="workflow.test",
        workflow_environment="test",
        credential_version=1,
        credential_hash=hashlib.sha256(TOKEN.encode("ascii")).hexdigest(),
        state=credential_state,
        issued_at=NOW - timedelta(minutes=1),
        expires_at=attempt.callback_authorization_deadline,
        consumed_at=NOW if credential_state == "Consumed" else None,
        replaced_at=None,
        revoked_at=None,
        replacement_credential_id=None,
    )
    return attempt, credential


def test_callback_credential_proof_accepts_only_exact_active_running_scope() -> None:
    attempt, credential = callback_scope()
    matched = CallbackCommandAuthorizer._prove_credential(
        attempt=attempt,
        credentials=[credential],
        supplied_credential=TOKEN,
        machine=machine(),
        database_now=NOW,
        expected_operation_kind="AIInterpretation",
    )
    assert matched is credential

    with pytest.raises(IntakeError) as caught:
        CallbackCommandAuthorizer._prove_credential(
            attempt=attempt,
            credentials=[credential],
            supplied_credential="B" * 43,
            machine=machine(),
            database_now=NOW,
            expected_operation_kind="AIInterpretation",
        )
    assert (caught.value.status_code, caught.value.code) == (403, "CALLBACK_FORBIDDEN")


@pytest.mark.parametrize(
    ("credential_state", "attempt_state"),
    [("Active", "Succeeded"), ("Consumed", "Running"), ("Replaced", "Running")],
)
def test_callback_credential_proof_rejects_wrong_state_pairings(
    credential_state: str, attempt_state: str
) -> None:
    attempt, credential = callback_scope(
        credential_state=credential_state, attempt_state=attempt_state
    )
    if credential_state == "Replaced":
        replacement = SimpleNamespace(credential_version=2)
        credential.replaced_at = NOW
        credential.replacement_credential_id = uuid.uuid4()
        credentials = [credential]
        # A missing replacement row is itself an inconsistent persisted graph.
        del replacement
    else:
        credentials = [credential]
    expected_error = RuntimeError if credential_state == "Replaced" else IntakeError
    with pytest.raises(expected_error):
        CallbackCommandAuthorizer._prove_credential(
            attempt=attempt,
            credentials=credentials,
            supplied_credential=TOKEN,
            machine=machine(),
            database_now=NOW,
            expected_operation_kind="AIInterpretation",
        )
