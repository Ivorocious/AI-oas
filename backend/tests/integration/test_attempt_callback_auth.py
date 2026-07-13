import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi import APIRouter, Depends, Request
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, func, insert, select, text, update

from ai_operations_automation.api.correlation import resolve_request_correlation
from ai_operations_automation.app import create_app
from ai_operations_automation.attempt_callback_auth import (
    AttemptCallbackCredentialVerifier,
    extract_attempt_callback_credential,
)
from ai_operations_automation.config import Settings
from ai_operations_automation.db import Base, create_database_engine, create_session_factory
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.machine_auth.authenticator import calculate_signature
from ai_operations_automation.machine_auth.canonicalization import canonical_signing_bytes
from ai_operations_automation.machine_auth.dependencies import authenticated_workflow_service
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService
from alembic import command

pytestmark = pytest.mark.integration
BACKEND_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime.now(UTC)
SECRET = b"synthetic-callback-auth-machine-secret"
CURRENT_TOKEN = "C" * 43
HISTORICAL_TOKEN = "H" * 43
TEST_ROUTE = "/test/integration-attempts/{attempt_id}/callback-auth"


class Resolver:
    def resolve(self, reference: str) -> bytes:
        if reference != "test/callback-auth-current":
            raise RuntimeError("unknown synthetic reference")
        return SECRET


@pytest.fixture(scope="module")
def engine() -> Engine:
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")
    value = create_database_engine(Settings(_env_file=None).database_url)
    yield value
    value.dispose()


@pytest.fixture
def callback_context(engine: Engine):
    tables = ", ".join(
        f'"{name}"' for name in Base.metadata.tables if name != "failure_recovery_policy_versions"
    )
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} CASCADE"))
    factory = create_session_factory(engine)
    identity_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            insert(Base.metadata.tables["machine_identities"]).values(
                id=identity_id,
                service_type="WorkflowService",
                environment="test",
                stable_service_id="workflow.callback.test",
                display_label="Synthetic callback workflow",
                status="Active",
                version=1,
            )
        )
        connection.execute(
            insert(Base.metadata.tables["machine_credential_versions"]).values(
                id=uuid.uuid4(),
                machine_identity_id=identity_id,
                credential_version=1,
                external_secret_reference="test/callback-auth-current",
                status="Current",
                activated_at=NOW - timedelta(days=1),
            )
        )
    machine = AuthenticatedWorkflowService(
        machine_identity_id=identity_id,
        stable_service_id="workflow.callback.test",
        environment="test",
        service_type="WorkflowService",
        credential_id=uuid.uuid4(),
        credential_version=1,
    )
    return engine, factory, machine


def seed_running_graph(callback_context):
    database, _, machine = callback_context
    tables = Base.metadata.tables
    delivery_id = uuid.uuid4()
    contact_id = uuid.uuid4()
    request_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    with database.begin() as connection:
        now = connection.scalar(select(func.now()))
        deadline = now + timedelta(minutes=30)
        connection.execute(
            insert(tables["inbound_deliveries"]).values(
                id=delivery_id,
                scope="PublicIntake",
                idempotency_key_digest=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                processing_status="Accepted",
                schema_version="1.0",
                version=1,
                correlation_id=uuid.uuid4(),
                intake_outcome="New",
            )
        )
        connection.execute(
            insert(tables["contacts"]).values(
                id=contact_id,
                display_label="Synthetic customer",
                normalized_email="private@example.test",
                version=1,
            )
        )
        connection.execute(
            insert(tables["service_requests"]).values(
                id=request_id,
                originating_delivery_id=delivery_id,
                contact_id=contact_id,
                normalized_request_description="Repair the leaking kitchen pipe",
                status="TriagePending",
                version=2,
            )
        )
        connection.execute(
            insert(tables["logical_operations"]).values(
                id=operation_id,
                service_request_id=request_id,
                operation_kind="AIInterpretation",
                input_hash="a" * 64,
                configuration_hash="b" * 64,
                prompt_version="prompt-v1",
                result_schema_version="result-v1",
                provider_name="DemoProvider",
                model_name="demo-model",
                adapter_name="WorkflowServiceAIAdapter",
                adapter_version="1.0",
                version=1,
            )
        )
        connection.execute(
            insert(tables["integration_attempts"]).values(
                id=attempt_id,
                logical_operation_id=operation_id,
                service_request_id=request_id,
                operation_kind="AIInterpretation",
                attempt_number=1,
                state="Running",
                version=2,
                adapter_name="WorkflowServiceAIAdapter",
                adapter_version="1.0",
                assigned_workflow_service=machine.stable_service_id,
                workflow_environment=machine.environment,
                callback_authorization_deadline=deadline,
                started_at=now,
            )
        )
        connection.execute(
            insert(tables["attempt_callback_credentials"]).values(
                id=credential_id,
                integration_attempt_id=attempt_id,
                operation_kind="AIInterpretation",
                workflow_service_identity=machine.stable_service_id,
                workflow_environment=machine.environment,
                credential_version=1,
                credential_hash=hashlib.sha256(CURRENT_TOKEN.encode()).hexdigest(),
                state="Active",
                expires_at=deadline,
            )
        )
    return attempt_id, operation_id, request_id, credential_id


def verify(callback_context, attempt_id, token=CURRENT_TOKEN, machine=None):
    _, factory, default_machine = callback_context
    with factory() as session:
        with session.begin():
            context = AttemptCallbackCredentialVerifier(session).verify(
                attempt_id=attempt_id,
                machine=machine or default_machine,
                supplied_credential=token,
                expected_operation_kind="AIInterpretation",
            )
            context.assert_transaction_bound(session)
            return context


def error_code(callback_context, attempt_id, token=CURRENT_TOKEN, machine=None):
    with pytest.raises(IntakeError) as caught:
        verify(callback_context, attempt_id, token, machine)
    return caught.value.status_code, caught.value.code, str(caught.value)


def rows(database, table_name):
    with database.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(select(Base.metadata.tables[table_name])).mappings()
        ]


def add_history(callback_context, attempt_id, state="Replaced") -> uuid.UUID:
    database, _, _ = callback_context
    table = Base.metadata.tables["attempt_callback_credentials"]
    history_id = uuid.uuid4()
    with database.begin() as connection:
        original = (
            connection.execute(select(table).where(table.c.integration_attempt_id == attempt_id))
            .mappings()
            .one()
        )
        active_id = original["id"]
        connection.execute(
            update(table).where(table.c.id == original["id"]).values(credential_version=2)
        )
        values = {
            "id": history_id,
            "integration_attempt_id": attempt_id,
            "operation_kind": "AIInterpretation",
            "workflow_service_identity": original["workflow_service_identity"],
            "workflow_environment": original["workflow_environment"],
            "credential_version": 1,
            "credential_hash": hashlib.sha256(HISTORICAL_TOKEN.encode()).hexdigest(),
            "state": state,
            "issued_at": original["issued_at"] - timedelta(minutes=1),
            "expires_at": original["expires_at"],
        }
        if state == "Replaced":
            values.update(replaced_at=original["issued_at"], replacement_credential_id=active_id)
        elif state == "Revoked":
            values["revoked_at"] = original["issued_at"]
        else:
            values["consumed_at"] = original["issued_at"]
        connection.execute(insert(table).values(**values))
    return active_id


def test_exact_running_attempt_verifies_safe_context_without_mutation(callback_context) -> None:
    database, _, machine = callback_context
    attempt_id, operation_id, request_id, credential_id = seed_running_graph(callback_context)
    before = {name: rows(database, name) for name in Base.metadata.tables}
    context = verify(callback_context, attempt_id)
    assert context.machine_identity_id == machine.machine_identity_id
    assert context.integration_attempt_id == attempt_id
    assert context.integration_attempt_version == 2
    assert context.logical_operation_id == operation_id
    assert context.service_request_id == request_id
    assert context.callback_credential_id == credential_id
    assert context.callback_credential_version == 1
    assert context.callback_credential_expires_at.tzinfo is not None
    assert "credential_hash" not in repr(context)
    assert "callback" not in context.__dict__ if hasattr(context, "__dict__") else True
    assert {name: rows(database, name) for name in Base.metadata.tables} == before


def test_hash_is_not_used_in_sql_predicate_and_plaintext_never_reaches_sql(
    callback_context,
) -> None:
    database, _, _ = callback_context
    attempt_id, *_ = seed_running_graph(callback_context)
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(database, "before_cursor_execute", capture)
    try:
        verify(callback_context, attempt_id)
    finally:
        event.remove(database, "before_cursor_execute", capture)
    normalized = "\n".join(statements).lower()
    assert CURRENT_TOKEN not in normalized
    assert not any(
        "credential_hash" in statement.lower().split(" where ", 1)[-1]
        for statement in statements
        if " where " in statement.lower()
    )


def test_wrong_plaintext_and_missing_active_are_generic_forbidden(callback_context) -> None:
    database, _, _ = callback_context
    attempt_id, *_ = seed_running_graph(callback_context)
    status, code, detail = error_code(callback_context, attempt_id, "W" * 43)
    assert (status, code) == (403, "CALLBACK_FORBIDDEN")
    assert "W" * 43 not in detail
    table = Base.metadata.tables["attempt_callback_credentials"]
    with database.begin() as connection:
        now = connection.scalar(select(func.now()))
        connection.execute(
            update(table)
            .where(table.c.integration_attempt_id == attempt_id)
            .values(state="Revoked", revoked_at=now)
        )
    assert error_code(callback_context, attempt_id)[:2] == (403, "CALLBACK_FORBIDDEN")


@pytest.mark.parametrize("state", ["Pending", "Succeeded", "RetryableFailure", "TerminalFailure"])
def test_non_running_lifecycle_is_forbidden(callback_context, state) -> None:
    database, _, _ = callback_context
    attempt_id, *_ = seed_running_graph(callback_context)
    table = Base.metadata.tables["integration_attempts"]
    values = {
        "state": state,
        "started_at": None,
        "completed_at": None,
        "result_hash": None,
        "sanitized_error_code": None,
    }
    if state == "Succeeded":
        values.update(started_at=NOW, completed_at=NOW, result_hash="d" * 64)
    elif state in ("RetryableFailure", "TerminalFailure"):
        values.update(completed_at=NOW, sanitized_error_code="SYNTHETIC_FAILURE")
    with database.begin() as connection:
        connection.execute(update(table).where(table.c.id == attempt_id).values(**values))
    assert error_code(callback_context, attempt_id)[:2] == (403, "CALLBACK_FORBIDDEN")


@pytest.mark.parametrize("case", ["expired", "future-issued"])
def test_postgresql_time_controls_callback_window(callback_context, case) -> None:
    database, _, _ = callback_context
    attempt_id, *_ = seed_running_graph(callback_context)
    attempts = Base.metadata.tables["integration_attempts"]
    credentials = Base.metadata.tables["attempt_callback_credentials"]
    with database.begin() as connection:
        now = connection.scalar(select(func.now()))
        if case == "expired":
            created, issued, deadline = (
                now - timedelta(hours=2),
                now - timedelta(hours=2),
                now - timedelta(hours=1),
            )
        else:
            created, issued, deadline = now, now + timedelta(minutes=5), now + timedelta(minutes=30)
        connection.execute(
            update(attempts)
            .where(attempts.c.id == attempt_id)
            .values(created_at=created, callback_authorization_deadline=deadline)
        )
        connection.execute(
            update(credentials)
            .where(credentials.c.integration_attempt_id == attempt_id)
            .values(issued_at=issued, expires_at=deadline)
        )
    assert error_code(callback_context, attempt_id)[:2] == (403, "CALLBACK_FORBIDDEN")


@pytest.mark.parametrize("state", ["Replaced", "Revoked", "Consumed"])
def test_historical_credentials_cannot_authenticate(callback_context, state) -> None:
    attempt_id, *_ = seed_running_graph(callback_context)
    add_history(callback_context, attempt_id, state)
    assert error_code(callback_context, attempt_id, HISTORICAL_TOKEN)[:2] == (
        403,
        "CALLBACK_FORBIDDEN",
    )
    assert verify(callback_context, attempt_id).callback_credential_version == 2


def test_replaced_history_plus_current_active_succeeds(callback_context) -> None:
    attempt_id, *_ = seed_running_graph(callback_context)
    add_history(callback_context, attempt_id)
    assert verify(callback_context, attempt_id).callback_credential_version == 2


def test_active_credential_must_be_highest_version(callback_context) -> None:
    database, _, _ = callback_context
    attempt_id, *_ = seed_running_graph(callback_context)
    table = Base.metadata.tables["attempt_callback_credentials"]
    current = rows(database, "attempt_callback_credentials")[0]
    with database.begin() as connection:
        connection.execute(
            insert(table).values(
                id=uuid.uuid4(),
                integration_attempt_id=attempt_id,
                operation_kind="AIInterpretation",
                workflow_service_identity=current["workflow_service_identity"],
                workflow_environment=current["workflow_environment"],
                credential_version=2,
                credential_hash=hashlib.sha256(HISTORICAL_TOKEN.encode()).hexdigest(),
                state="Revoked",
                expires_at=current["expires_at"],
                revoked_at=current["issued_at"],
            )
        )
    assert error_code(callback_context, attempt_id)[:2] == (500, "INTERNAL_ERROR")


@pytest.mark.parametrize(
    "field,value",
    [("assigned_workflow_service", "hidden.other"), ("workflow_environment", "hidden")],
)
def test_wrong_assignment_is_concealed(callback_context, field, value) -> None:
    database, _, _ = callback_context
    attempt_id, *_ = seed_running_graph(callback_context)
    table = Base.metadata.tables["integration_attempts"]
    with database.begin() as connection:
        connection.execute(update(table).where(table.c.id == attempt_id).values(**{field: value}))
    status, code, detail = error_code(callback_context, attempt_id)
    assert (status, code) == (404, "ATTEMPT_NOT_FOUND")
    assert value not in detail


def test_missing_attempt_is_concealed(callback_context) -> None:
    assert error_code(callback_context, uuid.uuid4())[:2] == (404, "ATTEMPT_NOT_FOUND")


@pytest.mark.parametrize(
    "case", ["adapter-name", "adapter-version", "owner", "credential-scope", "success-reference"]
)
def test_structural_contradictions_are_safe_internal_errors(callback_context, case) -> None:
    database, _, _ = callback_context
    attempt_id, operation_id, request_id, _ = seed_running_graph(callback_context)
    tables = Base.metadata.tables
    with database.begin() as connection:
        if case == "adapter-name":
            connection.execute(
                update(tables["integration_attempts"])
                .where(tables["integration_attempts"].c.id == attempt_id)
                .values(adapter_name="Mismatch")
            )
        elif case == "adapter-version":
            connection.execute(
                update(tables["integration_attempts"])
                .where(tables["integration_attempts"].c.id == attempt_id)
                .values(adapter_version="Mismatch")
            )
        elif case == "owner":
            other = uuid.uuid4()
            delivery = uuid.uuid4()
            contact = uuid.uuid4()
            connection.execute(
                insert(tables["inbound_deliveries"]).values(
                    id=delivery,
                    scope="PublicIntake",
                    idempotency_key_digest=hashlib.sha256(other.bytes).hexdigest(),
                    processing_status="Accepted",
                    schema_version="1.0",
                    version=1,
                    correlation_id=uuid.uuid4(),
                    intake_outcome="New",
                )
            )
            connection.execute(
                insert(tables["contacts"]).values(id=contact, display_label="Other", version=1)
            )
            connection.execute(
                insert(tables["service_requests"]).values(
                    id=other,
                    originating_delivery_id=delivery,
                    contact_id=contact,
                    normalized_request_description="Other",
                    status="TriagePending",
                    version=1,
                )
            )
            connection.execute(
                update(tables["integration_attempts"])
                .where(tables["integration_attempts"].c.id == attempt_id)
                .values(service_request_id=other)
            )
        elif case == "credential-scope":
            connection.execute(
                update(tables["attempt_callback_credentials"])
                .where(
                    tables["attempt_callback_credentials"].c.integration_attempt_id == attempt_id
                )
                .values(workflow_service_identity="hidden.other")
            )
        else:
            connection.execute(
                update(tables["logical_operations"])
                .where(tables["logical_operations"].c.id == operation_id)
                .values(succeeded_attempt_id=attempt_id)
            )
    status, code, detail = error_code(callback_context, attempt_id)
    assert (status, code) == (500, "INTERNAL_ERROR")
    assert (
        "Mismatch" not in detail and "hidden.other" not in detail and str(request_id) not in detail
    )


callback_test_router = APIRouter()


@callback_test_router.post(TEST_ROUTE)
async def callback_auth_test_route(
    attempt_id: str,
    request: Request,
    correlation_id: uuid.UUID = Depends(resolve_request_correlation),
    machine: AuthenticatedWorkflowService = Depends(authenticated_workflow_service),
):
    supplied = extract_attempt_callback_credential(request.headers)
    try:
        parsed_attempt_id = uuid.UUID(attempt_id)
    except ValueError as exc:
        raise IntakeError(404, "ATTEMPT_NOT_FOUND", "The requested attempt was not found.") from exc
    factory = request.app.state.session_factory
    with factory() as session:
        with session.begin():
            verified = AttemptCallbackCredentialVerifier(session).verify(
                attempt_id=parsed_attempt_id,
                machine=machine,
                supplied_credential=supplied,
                expected_operation_kind="AIInterpretation",
            )
            verified.assert_transaction_bound(session)
            return {
                "correlation_id": correlation_id,
                "integration_attempt_id": verified.integration_attempt_id,
                "callback_credential_id": verified.callback_credential_id,
            }


def signed_headers(attempt_id, nonce, *, callback=CURRENT_TOKEN, correlation=None):
    path = TEST_ROUTE.format(attempt_id=attempt_id)
    timestamp = str(int(NOW.timestamp()))
    signing = canonical_signing_bytes("POST", path.encode(), b"", timestamp, nonce, b"")
    headers = {
        "X-Correlation-ID": correlation or str(uuid.uuid4()),
        "X-Service-ID": "workflow.callback.test",
        "X-Service-Timestamp": timestamp,
        "X-Service-Nonce": nonce,
        "X-Service-Signature": calculate_signature(SECRET, signing),
    }
    if callback is not None:
        headers["X-Attempt-Callback-Credential"] = callback
    return headers


def test_test_only_http_ordering_nonce_and_success(callback_context) -> None:
    database, factory, _ = callback_context
    attempt_id, *_ = seed_running_graph(callback_context)
    app = create_app(
        Settings(app_environment="test", _env_file=None),
        factory,
        machine_secret_resolver=Resolver(),
        machine_clock=lambda: NOW,
    )
    app.include_router(callback_test_router)
    client = TestClient(app)
    path = TEST_ROUTE.format(attempt_id=attempt_id)
    assert client.post(path).status_code == 401
    invalid = signed_headers(attempt_id, "callback-invalid-signature-001")
    invalid["X-Service-Signature"] = "0" * 64
    assert client.post(path, headers=invalid).status_code == 401
    nonce = "callback-missing-header-0001"
    missing = client.post(path, headers=signed_headers(attempt_id, nonce, callback=None))
    assert missing.status_code == 403
    reused = client.post(path, headers=signed_headers(attempt_id, nonce))
    assert reused.status_code == 401
    wrong = client.post(
        path, headers=signed_headers(attempt_id, "callback-wrong-token-00001", callback="W" * 43)
    )
    assert wrong.status_code == 403
    correlation = str(uuid.uuid4())
    success = client.post(
        path,
        headers=signed_headers(attempt_id, "callback-success-token-0001", correlation=correlation),
    )
    assert success.status_code == 200
    assert success.json()["correlation_id"] == correlation
    malformed = "not-a-uuid"
    hidden = client.post(
        TEST_ROUTE.format(attempt_id=malformed),
        headers=signed_headers(malformed, "callback-malformed-id-00001"),
    )
    assert hidden.status_code == 404 and hidden.json()["error"]["code"] == "ATTEMPT_NOT_FOUND"
    with database.connect() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(Base.metadata.tables["machine_request_nonces"])
            )
            == 4
        )
    production_paths = create_app(Settings(_env_file=None), factory).openapi()["paths"]
    assert not any(path.startswith("/test/") for path in production_paths)
    assert len(production_paths) == 11
