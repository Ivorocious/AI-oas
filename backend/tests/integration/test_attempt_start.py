import hashlib
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, func, insert, select, text, update

from ai_operations_automation.app import create_app
from ai_operations_automation.attempt_start.models import AttemptStartRequest
from ai_operations_automation.command_idempotency.canonicalization import canonical_command_hash
from ai_operations_automation.command_idempotency.keys import command_key_digest
from ai_operations_automation.config import Settings
from ai_operations_automation.db import (
    Base,
    create_database_engine,
    create_session_factory,
)
from ai_operations_automation.db.models.intake import ServiceRequest
from ai_operations_automation.machine_auth.authenticator import calculate_signature
from ai_operations_automation.machine_auth.canonicalization import canonical_signing_bytes
from ai_operations_automation.start_ai.hashing import ai_input_hash
from alembic import command

pytestmark = pytest.mark.integration
BACKEND_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime.now(UTC)
SECRET = b"synthetic-attempt-start-machine-secret"
PATH_TEMPLATE = "/api/v1/integration-attempts/{attempt_id}/commands/start"


class Resolver:
    def resolve(self, reference: str) -> bytes:
        if reference != "test/attempt-start-current":
            raise RuntimeError("unknown synthetic reference")
        return SECRET


@pytest.fixture(scope="module")
def engine() -> Engine:
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")
    value = create_database_engine(Settings(_env_file=None).database_url)
    yield value
    value.dispose()


@pytest.fixture
def command_context(engine: Engine):
    tables = ", ".join(f'"{name}"' for name in Base.metadata.tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} CASCADE"))
    app = create_app(
        Settings(app_environment="test", _env_file=None),
        create_session_factory(engine),
        machine_secret_resolver=Resolver(),
        machine_clock=lambda: NOW,
    )
    identity_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            insert(Base.metadata.tables["machine_identities"]).values(
                id=identity_id,
                service_type="WorkflowService",
                environment="test",
                stable_service_id="workflow.attempt-start.test",
                display_label="Synthetic attempt-start workflow",
                status="Active",
                version=1,
            )
        )
        connection.execute(
            insert(Base.metadata.tables["machine_credential_versions"]).values(
                id=uuid.uuid4(),
                machine_identity_id=identity_id,
                credential_version=1,
                external_secret_reference="test/attempt-start-current",
                status="Current",
                activated_at=NOW - timedelta(days=1),
            )
        )
    return TestClient(app), engine, identity_id


def attempt_body(version=1) -> bytes:
    return json.dumps(
        {
            "schema_version": "1.0",
            "expected_versions": {"integration_attempt": version},
            "command": {},
        },
        separators=(",", ":"),
    ).encode()


def signed_headers(attempt_id, body, *, nonce, key="attempt-start-key-0001", correlation=None):
    path = PATH_TEMPLATE.format(attempt_id=attempt_id)
    timestamp = str(int(NOW.timestamp()))
    signing = canonical_signing_bytes("POST", path.encode(), b"", timestamp, nonce, body)
    return {
        "Content-Type": "application/json",
        "Idempotency-Key": key,
        "X-Correlation-ID": correlation or str(uuid.uuid4()),
        "X-Service-ID": "workflow.attempt-start.test",
        "X-Service-Timestamp": timestamp,
        "X-Service-Nonce": nonce,
        "X-Service-Signature": calculate_signature(SECRET, signing),
    }


def post_start(
    client: TestClient,
    attempt_id,
    *,
    version=1,
    nonce,
    key="attempt-start-key-0001",
    correlation=None,
):
    body = attempt_body(version)
    return client.post(
        PATH_TEMPLATE.format(attempt_id=attempt_id),
        content=body,
        headers=signed_headers(attempt_id, body, nonce=nonce, key=key, correlation=correlation),
    )


def seed_request(database: Engine, *, status="TriagePending") -> uuid.UUID:
    tables = Base.metadata.tables
    delivery_id = uuid.uuid4()
    contact_id = uuid.uuid4()
    request_id = uuid.uuid4()
    with database.begin() as connection:
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
                status=status,
                version=2,
                location_context="Private home context",
                timing_preference=None,
            )
        )
    return request_id


def create_pending_graph(command_context):
    client, database, _ = command_context
    request_id = seed_request(database)
    operation_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    tables = Base.metadata.tables
    input_digest = ai_input_hash(
        ServiceRequest(
            normalized_request_description="Repair the leaking kitchen pipe",
            location_context="Private home context",
            timing_preference=None,
        )
    )
    with database.begin() as connection:
        database_now = connection.scalar(select(func.now()))
        deadline = database_now + timedelta(minutes=30)
        connection.execute(
            insert(tables["logical_operations"]).values(
                id=operation_id,
                service_request_id=request_id,
                operation_kind="AIInterpretation",
                input_hash=input_digest,
                configuration_hash="a" * 64,
                prompt_version="service-request-interpretation-v1",
                result_schema_version="service-request-interpretation-v1",
                provider_name="DemoAIProvider",
                model_name="demo-ai-model-v1",
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
                state="Pending",
                version=1,
                adapter_name="WorkflowServiceAIAdapter",
                adapter_version="1.0",
                assigned_workflow_service="workflow.attempt-start.test",
                workflow_environment="test",
                callback_authorization_deadline=deadline,
            )
        )
        connection.execute(
            insert(tables["attempt_callback_credentials"]).values(
                id=uuid.uuid4(),
                integration_attempt_id=attempt_id,
                operation_kind="AIInterpretation",
                workflow_service_identity="workflow.attempt-start.test",
                workflow_environment="test",
                credential_version=1,
                credential_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                state="Active",
                expires_at=deadline,
            )
        )
    return client, database, attempt_id, request_id


def transition_counts(database):
    tables = Base.metadata.tables
    with database.connect() as connection:
        return {
            "audit": connection.scalar(
                select(func.count())
                .select_from(tables["audit_events"])
                .where(tables["audit_events"].c.event_name == "integration_attempt.started")
            ),
            "outbox": connection.scalar(
                select(func.count())
                .select_from(tables["outbox_messages"])
                .where(tables["outbox_messages"].c.event_type == "integration_attempt.started")
            ),
            "command": connection.scalar(
                select(func.count())
                .select_from(tables["command_idempotency_records"])
                .where(
                    tables["command_idempotency_records"].c.command_intent
                    == "StartIntegrationAttempt"
                )
            ),
        }


def row(database, table_name, row_id):
    table = Base.metadata.tables[table_name]
    with database.connect() as connection:
        return connection.execute(select(table).where(table.c.id == row_id)).mappings().one()


def test_success_updates_only_attempt_and_writes_safe_evidence(command_context) -> None:
    client, database, attempt_id, request_id = create_pending_graph(command_context)
    tables = Base.metadata.tables
    before_attempt = row(database, "integration_attempts", attempt_id)
    before_request = row(database, "service_requests", request_id)
    before_operation = row(database, "logical_operations", before_attempt["logical_operation_id"])
    with database.connect() as connection:
        credential = (
            connection.execute(
                select(tables["attempt_callback_credentials"]).where(
                    tables["attempt_callback_credentials"].c.integration_attempt_id == attempt_id
                )
            )
            .mappings()
            .one()
        )
    before_credential = dict(credential)
    correlation = str(uuid.uuid4())
    response = post_start(
        client,
        attempt_id,
        nonce="attempt-start-success-000001",
        correlation=correlation,
    )
    assert response.status_code == 200
    assert response.headers["x-correlation-id"] == correlation
    body = response.json()
    assert body["correlation_id"] == correlation
    assert body["result"]["attempt_state"] == "Running"
    assert body["versions"] == {"integration_attempt": 2}
    after_attempt = row(database, "integration_attempts", attempt_id)
    assert after_attempt["state"] == "Running" and after_attempt["version"] == 2
    assert after_attempt["started_at"].tzinfo is not None
    assert after_attempt["completed_at"] is None
    immutable = (
        "logical_operation_id",
        "service_request_id",
        "operation_kind",
        "attempt_number",
        "adapter_name",
        "adapter_version",
        "assigned_workflow_service",
        "workflow_environment",
        "callback_authorization_deadline",
    )
    assert all(after_attempt[name] == before_attempt[name] for name in immutable)
    assert row(database, "service_requests", request_id) == before_request
    assert row(database, "logical_operations", before_operation["id"]) == before_operation
    with database.connect() as connection:
        after_credential = dict(
            connection.execute(
                select(tables["attempt_callback_credentials"]).where(
                    tables["attempt_callback_credentials"].c.integration_attempt_id == attempt_id
                )
            )
            .mappings()
            .one()
        )
        audit = (
            connection.execute(
                select(tables["audit_events"]).where(
                    tables["audit_events"].c.event_name == "integration_attempt.started"
                )
            )
            .mappings()
            .one()
        )
        outbox = (
            connection.execute(
                select(tables["outbox_messages"]).where(
                    tables["outbox_messages"].c.event_type == "integration_attempt.started"
                )
            )
            .mappings()
            .one()
        )
        command = (
            connection.execute(
                select(tables["command_idempotency_records"]).where(
                    tables["command_idempotency_records"].c.command_intent
                    == "StartIntegrationAttempt"
                )
            )
            .mappings()
            .one()
        )
    assert after_credential == before_credential
    assert audit["aggregate_version"] == 2 and audit["outcome"] == "Running"
    assert audit["reason_codes"] == []
    assert outbox["audit_event_id"] == audit["id"]
    assert outbox["publication_state"] == "Pending"
    assert command["status"] == "Completed" and command["logical_http_status"] == 200
    serialized = json.dumps(
        {"response": body, "audit": audit, "outbox": outbox, "snapshot": command},
        default=str,
    )
    for forbidden in (
        before_credential["credential_hash"],
        "attempt-start-key-0001",
        "private@example.test",
        "Private home context",
        "callback_credential_hash",
        "nonce",
    ):
        assert forbidden not in serialized
    with database.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(tables["ai_interpretations"])) == 0
        )


def test_exact_replay_returns_current_correlation_without_mutation(command_context) -> None:
    client, database, attempt_id, _ = create_pending_graph(command_context)
    first = post_start(client, attempt_id, nonce="attempt-replay-nonce-000001")
    before = transition_counts(database)
    correlation = str(uuid.uuid4())
    replay = post_start(
        client,
        attempt_id,
        nonce="attempt-replay-nonce-000002",
        correlation=correlation,
    )
    assert first.status_code == replay.status_code == 200
    assert replay.json()["command_id"] == first.json()["command_id"]
    assert replay.json()["result"] == first.json()["result"]
    assert replay.json()["correlation_id"] == correlation
    assert transition_counts(database) == before
    assert row(database, "integration_attempts", attempt_id)["version"] == 2


def test_changed_body_conflicts_before_attempt_read(command_context) -> None:
    client, database, attempt_id, _ = create_pending_graph(command_context)
    assert post_start(client, attempt_id, nonce="attempt-conflict-nonce-0001").status_code == 200
    with database.begin() as connection:
        connection.execute(
            update(Base.metadata.tables["integration_attempts"])
            .where(Base.metadata.tables["integration_attempts"].c.id == attempt_id)
            .values(version=99)
        )
    response = post_start(
        client,
        attempt_id,
        version=2,
        nonce="attempt-conflict-nonce-0002",
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "COMMAND_IDEMPOTENCY_CONFLICT"
    assert transition_counts(database) == {"audit": 1, "outbox": 1, "command": 1}


@pytest.mark.parametrize(
    ("case", "expected_version", "code"),
    [
        ("missing", 1, "ATTEMPT_NOT_FOUND"),
        ("stale", 2, "CONCURRENCY_CONFLICT"),
        ("owner-state", 1, "INVALID_STATE_TRANSITION"),
        ("input", 1, "INVALID_STATE_TRANSITION"),
        ("expired", 1, "INVALID_STATE_TRANSITION"),
    ],
)
def test_expected_guards_are_stored_and_replayed(
    command_context, case, expected_version, code
) -> None:
    client, database, attempt_id, request_id = create_pending_graph(command_context)
    tables = Base.metadata.tables
    if case == "missing":
        attempt_id = uuid.uuid4()
    elif case == "owner-state":
        with database.begin() as connection:
            connection.execute(
                update(tables["service_requests"])
                .where(tables["service_requests"].c.id == request_id)
                .values(status="HumanReview")
            )
    elif case == "input":
        with database.begin() as connection:
            connection.execute(
                update(tables["service_requests"])
                .where(tables["service_requests"].c.id == request_id)
                .values(normalized_request_description="Changed approved AI input")
            )
    elif case == "expired":
        with database.begin() as connection:
            current_time = connection.scalar(select(func.now()))
            issued_at = current_time - timedelta(hours=2)
            deadline = current_time - timedelta(hours=1)
            connection.execute(
                update(tables["integration_attempts"])
                .where(tables["integration_attempts"].c.id == attempt_id)
                .values(created_at=issued_at, callback_authorization_deadline=deadline)
            )
            connection.execute(
                update(tables["attempt_callback_credentials"])
                .where(
                    tables["attempt_callback_credentials"].c.integration_attempt_id == attempt_id
                )
                .values(issued_at=issued_at, expires_at=deadline)
            )
    first = post_start(
        client,
        attempt_id,
        version=expected_version,
        nonce=f"guard-{case}-nonce-000001",
    )
    assert first.status_code in (404, 409)
    assert first.json()["error"]["code"] == code
    if case == "stale":
        assert first.json()["error"]["current_versions"] == {"integration_attempt": 1}
    if case != "missing":
        with database.begin() as connection:
            connection.execute(
                update(tables["integration_attempts"])
                .where(tables["integration_attempts"].c.id == attempt_id)
                .values(version=7)
            )
    replay = post_start(
        client,
        attempt_id,
        version=expected_version,
        nonce=f"guard-{case}-nonce-000002",
    )
    assert replay.status_code == first.status_code
    assert replay.json()["error"]["code"] == code
    assert transition_counts(database) == {"audit": 0, "outbox": 0, "command": 1}


@pytest.mark.parametrize(
    ("field", "value"),
    [("assigned_workflow_service", "hidden.other"), ("workflow_environment", "hidden")],
)
def test_assignment_mismatch_is_concealed(command_context, field, value) -> None:
    client, database, attempt_id, _ = create_pending_graph(command_context)
    with database.begin() as connection:
        connection.execute(
            update(Base.metadata.tables["integration_attempts"])
            .where(Base.metadata.tables["integration_attempts"].c.id == attempt_id)
            .values(**{field: value})
        )
    response = post_start(client, attempt_id, nonce=f"conceal-{field}-nonce-0001")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ATTEMPT_NOT_FOUND"
    assert value not in response.text
    assert transition_counts(database) == {"audit": 0, "outbox": 0, "command": 1}


def test_running_new_key_and_operation_success_guards(command_context) -> None:
    client, database, attempt_id, _ = create_pending_graph(command_context)
    assert post_start(client, attempt_id, nonce="first-running-nonce-000001").status_code == 200
    running = post_start(
        client,
        attempt_id,
        version=2,
        key="different-start-key-0002",
        nonce="running-new-key-nonce-00001",
    )
    assert running.status_code == 409
    assert running.json()["error"]["code"] == "INVALID_STATE_TRANSITION"


@pytest.mark.parametrize("state", ["Succeeded", "RetryableFailure", "TerminalFailure"])
def test_terminal_or_failed_attempt_new_key_is_invalid_state(command_context, state) -> None:
    client, database, attempt_id, _ = create_pending_graph(command_context)
    tables = Base.metadata.tables
    attempt = row(database, "integration_attempts", attempt_id)
    values = {
        "state": state,
        "completed_at": attempt["created_at"],
    }
    if state == "Succeeded":
        values.update(started_at=attempt["created_at"], result_hash="c" * 64)
    else:
        values["sanitized_error_code"] = "SYNTHETIC_FAILURE"
    with database.begin() as connection:
        connection.execute(
            update(tables["integration_attempts"])
            .where(tables["integration_attempts"].c.id == attempt_id)
            .values(**values)
        )
    response = post_start(
        client,
        attempt_id,
        nonce=f"terminal-{state}-nonce-0001",
        key=f"terminal-state-key-{state}",
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATE_TRANSITION"
    assert transition_counts(database) == {"audit": 0, "outbox": 0, "command": 1}


def test_committed_processing_record_returns_safe_internal_error(command_context) -> None:
    client, database, attempt_id, _ = create_pending_graph(command_context)
    _, _, identity_id = command_context
    key = "committed-processing-key"
    canonical_hash = canonical_command_hash(
        AttemptStartRequest.model_validate(
            {
                "schema_version": "1.0",
                "expected_versions": {"integration_attempt": 1},
                "command": {},
            }
        )
    )
    with database.begin() as connection:
        connection.execute(
            insert(Base.metadata.tables["command_idempotency_records"]).values(
                id=uuid.uuid4(),
                actor_class="MachineService",
                actor_id=identity_id,
                command_intent="StartIntegrationAttempt",
                route_template=PATH_TEMPLATE,
                target_type="IntegrationAttempt",
                target_id=attempt_id,
                idempotency_key_digest=command_key_digest(key),
                canonical_body_hash=canonical_hash,
                status="Processing",
                command_id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
            )
        )
    response = post_start(
        client,
        attempt_id,
        nonce="processing-record-nonce-0001",
        key=key,
    )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert row(database, "integration_attempts", attempt_id)["state"] == "Pending"


def test_successful_operation_guard_is_stored(command_context) -> None:
    client, database, attempt_id, _ = create_pending_graph(command_context)
    tables = Base.metadata.tables
    attempt = row(database, "integration_attempts", attempt_id)
    sibling_id = uuid.uuid4()
    now = attempt["created_at"]
    with database.begin() as connection:
        connection.execute(
            insert(tables["integration_attempts"]).values(
                id=sibling_id,
                logical_operation_id=attempt["logical_operation_id"],
                service_request_id=attempt["service_request_id"],
                operation_kind="AIInterpretation",
                attempt_number=2,
                state="Succeeded",
                version=1,
                adapter_name=attempt["adapter_name"],
                adapter_version=attempt["adapter_version"],
                assigned_workflow_service=attempt["assigned_workflow_service"],
                workflow_environment=attempt["workflow_environment"],
                callback_authorization_deadline=attempt["callback_authorization_deadline"],
                started_at=now,
                completed_at=now,
                result_hash="a" * 64,
            )
        )
        connection.execute(
            update(tables["logical_operations"])
            .where(tables["logical_operations"].c.id == attempt["logical_operation_id"])
            .values(succeeded_attempt_id=sibling_id)
        )
    response = post_start(client, attempt_id, nonce="operation-success-nonce-0001")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LOGICAL_OPERATION_ALREADY_SUCCEEDED"


@pytest.mark.parametrize(
    "failure",
    [
        "owner",
        "credential-missing",
        "credential-duplicate",
        "credential-scope",
        "success-contradiction",
    ],
)
def test_integrity_failures_roll_back_without_completed_command(command_context, failure) -> None:
    client, database, attempt_id, _ = create_pending_graph(command_context)
    tables = Base.metadata.tables
    attempt = row(database, "integration_attempts", attempt_id)
    if failure == "owner":
        other_request = seed_request(database)
        with database.begin() as connection:
            connection.execute(
                update(tables["integration_attempts"])
                .where(tables["integration_attempts"].c.id == attempt_id)
                .values(service_request_id=other_request)
            )
    elif failure == "credential-missing":
        with database.begin() as connection:
            connection.execute(
                update(tables["command_idempotency_records"])
                .where(
                    tables["command_idempotency_records"].c.command_intent
                    == "StartAiInterpretation"
                )
                .values(
                    callback_credential_id=None,
                    callback_credential_version=None,
                    callback_credential_expires_at=None,
                    secret_delivery_receipt=None,
                )
            )
            connection.execute(
                tables["attempt_callback_credentials"]
                .delete()
                .where(
                    tables["attempt_callback_credentials"].c.integration_attempt_id == attempt_id
                )
            )
    elif failure == "credential-duplicate":
        with database.begin() as connection:
            connection.execute(
                insert(tables["attempt_callback_credentials"]).values(
                    id=uuid.uuid4(),
                    integration_attempt_id=attempt_id,
                    operation_kind="AIInterpretation",
                    workflow_service_identity=attempt["assigned_workflow_service"],
                    workflow_environment=attempt["workflow_environment"],
                    credential_version=2,
                    credential_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                    state="Revoked",
                    expires_at=attempt["callback_authorization_deadline"],
                    revoked_at=attempt["created_at"],
                )
            )
    elif failure == "credential-scope":
        with database.begin() as connection:
            connection.execute(
                update(tables["attempt_callback_credentials"])
                .where(
                    tables["attempt_callback_credentials"].c.integration_attempt_id == attempt_id
                )
                .values(workflow_service_identity="hidden.other")
            )
    else:
        with database.begin() as connection:
            connection.execute(
                insert(tables["integration_attempts"]).values(
                    id=uuid.uuid4(),
                    logical_operation_id=attempt["logical_operation_id"],
                    service_request_id=attempt["service_request_id"],
                    operation_kind="AIInterpretation",
                    attempt_number=2,
                    state="Succeeded",
                    version=1,
                    adapter_name=attempt["adapter_name"],
                    adapter_version=attempt["adapter_version"],
                    assigned_workflow_service=attempt["assigned_workflow_service"],
                    workflow_environment=attempt["workflow_environment"],
                    callback_authorization_deadline=attempt["callback_authorization_deadline"],
                    started_at=attempt["created_at"],
                    completed_at=attempt["created_at"],
                    result_hash="b" * 64,
                )
            )
    response = post_start(client, attempt_id, nonce=f"integrity-{failure}-nonce-0001")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert transition_counts(database) == {"audit": 0, "outbox": 0, "command": 0}
    current = row(database, "integration_attempts", attempt_id)
    assert current["state"] == "Pending" and current["version"] == 1


@pytest.mark.parametrize(
    ("verb", "table"),
    [
        ("update", "integration_attempts"),
        ("insert", "audit_events"),
        ("insert", "outbox_messages"),
        ("update", "command_idempotency_records"),
    ],
)
def test_forced_write_failures_roll_back_transition(command_context, verb, table) -> None:
    client, database, attempt_id, _ = create_pending_graph(command_context)

    def fail_target(_connection, _cursor, statement, _parameters, _context, _many):
        prefix = f"insert into {table}" if verb == "insert" else f"update {table}"
        if statement.lstrip().lower().startswith(prefix):
            raise RuntimeError("synthetic transition failure")

    event.listen(database, "before_cursor_execute", fail_target)
    try:
        response = post_start(client, attempt_id, nonce=f"forced-{table[:12]}-nonce-0001")
    finally:
        event.remove(database, "before_cursor_execute", fail_target)
    assert response.status_code == 500
    assert "synthetic" not in response.text and "constraint" not in response.text.lower()
    assert transition_counts(database) == {"audit": 0, "outbox": 0, "command": 0}
    current = row(database, "integration_attempts", attempt_id)
    assert current["state"] == "Pending" and current["version"] == 1


def test_forced_commit_failure_rolls_back_transition(command_context) -> None:
    client, database, attempt_id, _ = create_pending_graph(command_context)

    def mark(connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().lower().startswith("insert into command_idempotency_records"):
            connection.info["fail_attempt_start_commit"] = True

    def fail_commit(connection):
        if connection.info.pop("fail_attempt_start_commit", False):
            raise RuntimeError("synthetic commit failure")

    event.listen(database, "before_cursor_execute", mark)
    event.listen(database, "commit", fail_commit)
    try:
        response = post_start(client, attempt_id, nonce="forced-start-commit-000001")
    finally:
        event.remove(database, "before_cursor_execute", mark)
        event.remove(database, "commit", fail_commit)
    assert response.status_code == 500
    assert transition_counts(database) == {"audit": 0, "outbox": 0, "command": 0}
    current = row(database, "integration_attempts", attempt_id)
    assert current["state"] == "Pending" and current["version"] == 1


def test_concurrent_identical_commands_transition_once(command_context) -> None:
    client, database, attempt_id, _ = create_pending_graph(command_context)

    def invoke(index):
        return post_start(client, attempt_id, nonce=f"start-same-{index}-nonce-00001")

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(invoke, (1, 2)))
    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json()["command_id"] == responses[1].json()["command_id"]
    assert transition_counts(database) == {"audit": 1, "outbox": 1, "command": 1}
    assert row(database, "integration_attempts", attempt_id)["version"] == 2


def test_concurrent_different_keys_yield_one_version_conflict(command_context) -> None:
    client, database, attempt_id, _ = create_pending_graph(command_context)

    def invoke(index):
        return post_start(
            client,
            attempt_id,
            nonce=f"start-keys-{index}-nonce-00001",
            key=f"attempt-start-different-{index}",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(invoke, (1, 2)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "CONCURRENCY_CONFLICT"
    assert transition_counts(database) == {"audit": 1, "outbox": 1, "command": 2}
    assert row(database, "integration_attempts", attempt_id)["version"] == 2


def test_machine_authentication_transport_order_and_nonce_behavior(command_context) -> None:
    client, database, attempt_id, request_id = create_pending_graph(command_context)
    path = PATH_TEMPLATE.format(attempt_id=attempt_id)
    body = attempt_body()
    missing = client.post(path, content=body)
    assert missing.status_code == 401
    assert "www-authenticate" not in missing.headers

    invalid_headers = signed_headers(attempt_id, body, nonce="invalid-signature-nonce-001")
    invalid_headers["X-Service-Signature"] = "0" * 64
    invalid = client.post(path, content=body, headers=invalid_headers)
    assert invalid.status_code == 401

    key_headers = signed_headers(attempt_id, body, nonce="missing-key-nonce-0000001")
    key_headers.pop("Idempotency-Key")
    missing_key = client.post(path, content=body, headers=key_headers)
    assert missing_key.status_code == 400

    malformed = b"{"
    malformed_headers = signed_headers(attempt_id, malformed, nonce="invalid-command-nonce-0001")
    invalid_command = client.post(path, content=malformed, headers=malformed_headers)
    reused_nonce = client.post(path, content=malformed, headers=malformed_headers)
    assert invalid_command.status_code == 400
    assert invalid_command.json()["error"]["code"] == "INVALID_COMMAND"
    assert reused_nonce.status_code == 401

    malformed_id = "not-a-uuid"
    malformed_path_body = attempt_body()
    malformed_id_response = client.post(
        PATH_TEMPLATE.format(attempt_id=malformed_id),
        content=malformed_path_body,
        headers=signed_headers(
            malformed_id,
            malformed_path_body,
            nonce="malformed-path-nonce-00001",
        ),
    )
    assert malformed_id_response.status_code == 404
    assert malformed_id_response.json()["error"]["code"] == "ATTEMPT_NOT_FOUND"

    with database.connect() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(Base.metadata.tables["machine_request_nonces"])
            )
            == 3
        )
    assert transition_counts(database) == {"audit": 0, "outbox": 0, "command": 0}
    assert row(database, "integration_attempts", attempt_id)["state"] == "Pending"
    human = client.get(f"/api/v1/service-requests/{request_id}")
    assert human.status_code == 401 and human.headers["www-authenticate"] == "Bearer"


def test_concurrent_same_key_different_body_is_one_success_one_conflict(
    command_context,
) -> None:
    client, database, attempt_id, _ = create_pending_graph(command_context)
    entered = threading.Event()
    release = threading.Event()

    def block_winner(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().lower().startswith("update integration_attempts"):
            entered.set()
            assert release.wait(timeout=5)

    event.listen(database, "before_cursor_execute", block_winner)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            winner = pool.submit(
                post_start,
                client,
                attempt_id,
                version=1,
                nonce="same-key-first-body-00001",
            )
            assert entered.wait(timeout=5)
            conflict = pool.submit(
                post_start,
                client,
                attempt_id,
                version=2,
                nonce="same-key-second-body-0001",
            )
            release.set()
            responses = [winner.result(timeout=10), conflict.result(timeout=10)]
    finally:
        event.remove(database, "before_cursor_execute", block_winner)
    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict_response = next(response for response in responses if response.status_code == 409)
    assert conflict_response.json()["error"]["code"] == "COMMAND_IDEMPOTENCY_CONFLICT"
    assert transition_counts(database) == {"audit": 1, "outbox": 1, "command": 1}
    assert row(database, "integration_attempts", attempt_id)["version"] == 2


def test_replaced_credential_history_permits_start_without_reading_hashes(
    command_context,
) -> None:
    client, database, attempt_id, _ = create_pending_graph(command_context)
    table = Base.metadata.tables["attempt_callback_credentials"]
    statements: list[str] = []
    with database.begin() as connection:
        original = (
            connection.execute(select(table).where(table.c.integration_attempt_id == attempt_id))
            .mappings()
            .one()
        )
        replacement_id = uuid.uuid4()
        connection.execute(
            update(table)
            .where(table.c.id == original["id"])
            .values(
                state="Replaced",
                replaced_at=original["issued_at"],
                replacement_credential_id=replacement_id,
            )
        )
        connection.execute(
            insert(table).values(
                id=replacement_id,
                integration_attempt_id=attempt_id,
                operation_kind="AIInterpretation",
                workflow_service_identity=original["workflow_service_identity"],
                workflow_environment=original["workflow_environment"],
                credential_version=2,
                credential_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                state="Active",
                expires_at=original["expires_at"],
            )
        )

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(database, "before_cursor_execute", capture)
    try:
        response = post_start(client, attempt_id, nonce="start-history-compatible-001")
    finally:
        event.remove(database, "before_cursor_execute", capture)
    assert response.status_code == 200
    assert row(database, "integration_attempts", attempt_id)["state"] == "Running"
    credential_selects = [
        statement
        for statement in statements
        if "attempt_callback_credentials" in statement.lower()
        and statement.lstrip().lower().startswith("select")
    ]
    assert credential_selects
    assert all("credential_hash" not in statement.lower() for statement in credential_selects)


def test_active_callback_credential_not_highest_is_safe_internal_error(
    command_context,
) -> None:
    client, database, attempt_id, _ = create_pending_graph(command_context)
    table = Base.metadata.tables["attempt_callback_credentials"]
    with database.begin() as connection:
        original = (
            connection.execute(select(table).where(table.c.integration_attempt_id == attempt_id))
            .mappings()
            .one()
        )
        connection.execute(
            insert(table).values(
                id=uuid.uuid4(),
                integration_attempt_id=attempt_id,
                operation_kind="AIInterpretation",
                workflow_service_identity=original["workflow_service_identity"],
                workflow_environment=original["workflow_environment"],
                credential_version=2,
                credential_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                state="Revoked",
                expires_at=original["expires_at"],
                revoked_at=original["issued_at"],
            )
        )
    response = post_start(client, attempt_id, nonce="start-active-not-current-0001")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert row(database, "integration_attempts", attempt_id)["state"] == "Pending"
    assert transition_counts(database) == {"audit": 0, "outbox": 0, "command": 0}


@pytest.mark.parametrize(
    ("field", "value"),
    [("adapter_name", "MismatchedAdapter"), ("adapter_version", "999")],
)
def test_frozen_adapter_intent_mismatch_rolls_back_safely(command_context, field, value) -> None:
    client, database, attempt_id, _ = create_pending_graph(command_context)
    attempts = Base.metadata.tables["integration_attempts"]
    with database.begin() as connection:
        connection.execute(
            update(attempts).where(attempts.c.id == attempt_id).values(**{field: value})
        )
    response = post_start(
        client,
        attempt_id,
        nonce=f"start-adapter-{field}-000001",
    )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert value not in response.text
    assert row(database, "integration_attempts", attempt_id)["state"] == "Pending"
    assert transition_counts(database) == {"audit": 0, "outbox": 0, "command": 0}
