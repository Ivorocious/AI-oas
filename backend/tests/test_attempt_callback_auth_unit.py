import uuid
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.datastructures import Headers

from ai_operations_automation.attempt_callback_auth.headers import (
    ATTEMPT_CALLBACK_CREDENTIAL_HEADER,
    extract_attempt_callback_credential,
)
from ai_operations_automation.attempt_callback_auth.models import (
    VerifiedAttemptCallbackContext,
)
from ai_operations_automation.attempt_callback_auth.verifier import (
    AttemptCallbackCredentialVerifier,
    matching_callback_credentials,
)
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService

TOKEN = "A" * 43


def headers(*values: str) -> Headers:
    return Headers(
        raw=[
            (ATTEMPT_CALLBACK_CREDENTIAL_HEADER.lower().encode(), value.encode("utf-8"))
            for value in values
        ]
    )


def test_exactly_one_minimum_production_credential_is_accepted() -> None:
    assert extract_attempt_callback_credential(headers(TOKEN)) == TOKEN


@pytest.mark.parametrize(
    "values",
    [
        (),
        (TOKEN, TOKEN),
        ("",),
        (" " + TOKEN,),
        (TOKEN + " ",),
        ("é" * 43,),
        (TOKEN + "=",),
        ("." * 43,),
        ("A" * 42,),
        ("A" * 257,),
    ],
)
def test_unusable_callback_headers_are_generic_forbidden(values: tuple[str, ...]) -> None:
    with pytest.raises(IntakeError) as caught:
        extract_attempt_callback_credential(headers(*values))
    assert caught.value.status_code == 403
    assert caught.value.code == "CALLBACK_FORBIDDEN"
    assert all(value not in str(caught.value) for value in values if value)


def context(session: Session) -> VerifiedAttemptCallbackContext:
    transaction = session.get_transaction()
    assert transaction is not None
    return VerifiedAttemptCallbackContext(
        machine_identity_id=uuid.UUID(int=1),
        stable_service_id="workflow.test",
        workflow_environment="test",
        integration_attempt_id=uuid.UUID(int=2),
        integration_attempt_version=2,
        logical_operation_id=uuid.UUID(int=3),
        service_request_id=uuid.UUID(int=4),
        operation_kind="AIInterpretation",
        callback_credential_id=uuid.UUID(int=5),
        callback_credential_version=1,
        callback_credential_expires_at=datetime(2026, 7, 14, tzinfo=UTC),
        _session=session,
        _transaction=transaction,
    )


def test_context_is_immutable_and_hides_transaction_fields_from_repr_and_equality() -> None:
    first_session = Session()
    second_session = Session()
    try:
        first_session.begin()
        second_session.begin()
        first = context(first_session)
        second = context(second_session)
        assert first == second
        assert "_session" not in repr(first)
        assert "_transaction" not in repr(first)
        assert TOKEN not in repr(first)
        with pytest.raises(TypeError):
            __import__("json").dumps(first)
        with pytest.raises(FrozenInstanceError):
            first.integration_attempt_version = 3  # type: ignore[misc]
    finally:
        first_session.rollback()
        second_session.rollback()


def test_context_accepts_only_original_active_session_and_transaction() -> None:
    first_session = Session()
    other_session = Session()
    try:
        first_session.begin()
        verified = context(first_session)
        verified.assert_transaction_bound(first_session)
        with pytest.raises(RuntimeError):
            verified.assert_transaction_bound(other_session)
        first_session.commit()
        with pytest.raises(RuntimeError):
            verified.assert_transaction_bound(first_session)
        first_session.begin()
        with pytest.raises(RuntimeError):
            verified.assert_transaction_bound(first_session)
    finally:
        first_session.close()
        other_session.close()


def test_context_rejects_use_after_rollback() -> None:
    session = Session()
    session.begin()
    verified = context(session)
    session.rollback()
    with pytest.raises(RuntimeError):
        verified.assert_transaction_bound(session)


def test_verifier_rejects_missing_or_implicit_transaction_before_sql() -> None:
    session = Session(create_engine("sqlite+pysqlite:///:memory:"))
    with pytest.raises(IntakeError) as absent:
        AttemptCallbackCredentialVerifier(session).verify(
            attempt_id=uuid.uuid4(),
            machine=None,  # type: ignore[arg-type]
            supplied_credential=TOKEN,
            expected_operation_kind="AIInterpretation",
        )
    assert absent.value.code == "INTERNAL_ERROR"

    session.connection()  # starts AUTOBEGIN without issuing verification SQL
    with pytest.raises(IntakeError) as implicit:
        AttemptCallbackCredentialVerifier(session).verify(
            attempt_id=uuid.uuid4(),
            machine=None,  # type: ignore[arg-type]
            supplied_credential=TOKEN,
            expected_operation_kind="AIInterpretation",
        )
    assert implicit.value.code == "INTERNAL_ERROR"
    session.rollback()


def test_constant_time_proof_compares_every_loaded_candidate(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def compare(left: str, right: str) -> bool:
        calls.append((left, right))
        return left == right

    monkeypatch.setattr(
        "ai_operations_automation.attempt_callback_auth.verifier.hmac.compare_digest", compare
    )
    digest = __import__("hashlib").sha256(TOKEN.encode("ascii")).hexdigest()
    candidates = [
        SimpleNamespace(credential_hash=digest),
        SimpleNamespace(credential_hash="0" * 64),
        SimpleNamespace(credential_hash="1" * 64),
    ]
    matches = matching_callback_credentials(candidates, TOKEN)  # type: ignore[arg-type]
    assert matches == [candidates[0]]
    assert len(calls) == len(candidates)
    assert all(right == digest for _, right in calls)


def test_database_infrastructure_failure_maps_to_safe_dependency_error() -> None:
    session = Session(create_engine("sqlite+pysqlite:///:memory:"))
    machine = AuthenticatedWorkflowService(
        machine_identity_id=uuid.uuid4(),
        stable_service_id="workflow.test",
        environment="test",
        service_type="WorkflowService",
        credential_id=uuid.uuid4(),
        credential_version=1,
    )
    with session.begin(), pytest.raises(IntakeError) as caught:
        AttemptCallbackCredentialVerifier(session).verify(
            attempt_id=uuid.uuid4(),
            machine=machine,
            supplied_credential=TOKEN,
            expected_operation_kind="AIInterpretation",
        )
    assert caught.value.status_code == 503
    assert caught.value.code == "DEPENDENCY_UNAVAILABLE"
    assert TOKEN not in str(caught.value)
