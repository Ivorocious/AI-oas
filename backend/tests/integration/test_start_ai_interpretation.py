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
from ai_operations_automation.config import Settings
from ai_operations_automation.db import Base, create_database_engine, create_session_factory
from ai_operations_automation.db.models.ai_execution import IntegrationAttempt, LogicalOperation
from ai_operations_automation.db.models.intake import ServiceRequest
from ai_operations_automation.machine_auth.authenticator import calculate_signature
from ai_operations_automation.machine_auth.canonicalization import canonical_signing_bytes
from ai_operations_automation.start_ai.hashing import ai_configuration_hash, ai_input_hash
from alembic import command

pytestmark = pytest.mark.integration
BACKEND_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime.now(UTC)
SECRET = b"synthetic-start-ai-machine-secret"
PATH_TEMPLATE = "/api/v1/service-requests/{request_id}/commands/start-ai-interpretation"


class Resolver:
    def resolve(self, reference: str) -> bytes:
        if reference != "test/start-ai-current":
            raise RuntimeError("unknown synthetic reference")
        return SECRET


class Generator:
    def __init__(self) -> None:
        self.calls = 0
        self.lock = threading.Lock()

    def __call__(self) -> str:
        with self.lock:
            self.calls += 1
            character = chr(64 + self.calls)
        return character * 43


@pytest.fixture(scope="module")
def engine() -> Engine:
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")
    value = create_database_engine(Settings(_env_file=None).database_url)
    yield value
    value.dispose()


@pytest.fixture
def command_context(engine: Engine):
    tables = ", ".join(
        f'"{name}"' for name in Base.metadata.tables if name != "failure_recovery_policy_versions"
    )
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} CASCADE"))
    generator = Generator()
    settings = Settings(app_environment="test", _env_file=None)
    app = create_app(
        settings,
        create_session_factory(engine),
        machine_secret_resolver=Resolver(),
        machine_clock=lambda: NOW,
        callback_credential_generator=generator,
    )
    identity_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            insert(Base.metadata.tables["machine_identities"]).values(
                id=identity_id,
                service_type="WorkflowService",
                environment="test",
                stable_service_id="workflow.start-ai.test",
                display_label="Synthetic Start AI workflow",
                status="Active",
                version=1,
            )
        )
        connection.execute(
            insert(Base.metadata.tables["machine_credential_versions"]).values(
                id=uuid.uuid4(),
                machine_identity_id=identity_id,
                credential_version=1,
                external_secret_reference="test/start-ai-current",
                status="Current",
                activated_at=NOW - timedelta(days=1),
            )
        )
    return TestClient(app), engine, generator, settings, identity_id


def seed_request(engine: Engine, *, status="TriagePending", version=1) -> uuid.UUID:
    delivery_id = uuid.uuid4()
    contact_id = uuid.uuid4()
    request_id = uuid.uuid4()
    tables = Base.metadata.tables
    with engine.begin() as connection:
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
                version=version,
                location_context="Private home context",
                timing_preference=None,
            )
        )
    return request_id


def command_body(version=1) -> bytes:
    return json.dumps(
        {
            "schema_version": "1.0",
            "expected_versions": {"service_request": version},
            "command": {},
        },
        separators=(",", ":"),
    ).encode()


def signed_headers(request_id, body, *, nonce, key="command-key-0001", correlation=None):
    path = PATH_TEMPLATE.format(request_id=request_id)
    timestamp = str(int(NOW.timestamp()))
    signing = canonical_signing_bytes("POST", path.encode(), b"", timestamp, nonce, body)
    return {
        "Content-Type": "application/json; charset=utf-8",
        "Idempotency-Key": key,
        "X-Correlation-ID": correlation or str(uuid.uuid4()),
        "X-Service-ID": "workflow.start-ai.test",
        "X-Service-Timestamp": timestamp,
        "X-Service-Nonce": nonce,
        "X-Service-Signature": calculate_signature(SECRET, signing),
    }


def post_command(client, request_id, *, version=1, nonce, key="command-key-0001", correlation=None):
    body = command_body(version)
    path = PATH_TEMPLATE.format(request_id=request_id)
    return client.post(
        path,
        content=body,
        headers=signed_headers(request_id, body, nonce=nonce, key=key, correlation=correlation),
    )


def counts(engine):
    names = (
        "logical_operations",
        "integration_attempts",
        "attempt_callback_credentials",
        "audit_events",
        "outbox_messages",
        "command_idempotency_records",
        "ai_interpretations",
    )
    with engine.connect() as connection:
        return {
            name: connection.scalar(select(func.count()).select_from(Base.metadata.tables[name]))
            for name in names
        }


def test_new_success_is_atomic_pending_and_secret_safe(command_context) -> None:
    client, engine, generator, _, identity_id = command_context
    request_id = seed_request(engine)
    correlation = str(uuid.uuid4())
    response = post_command(
        client,
        request_id,
        nonce="start-ai-success-00000001",
        correlation=correlation,
    )
    assert response.status_code == 202
    assert response.headers["x-correlation-id"] == correlation
    body = response.json()
    assert body["correlation_id"] == correlation
    assert body["result"]["credential_delivery"] == "PlaintextIssued"
    plaintext = body["result"]["callback_credential"]
    assert generator.calls == 1
    tables = Base.metadata.tables
    with engine.connect() as connection:
        request_row = (
            connection.execute(
                select(tables["service_requests"]).where(
                    tables["service_requests"].c.id == request_id
                )
            )
            .mappings()
            .one()
        )
        operation = connection.execute(select(tables["logical_operations"])).mappings().one()
        attempt = connection.execute(select(tables["integration_attempts"])).mappings().one()
        credential = (
            connection.execute(select(tables["attempt_callback_credentials"])).mappings().one()
        )
        audits = connection.execute(select(tables["audit_events"])).mappings().all()
        outbox = connection.execute(select(tables["outbox_messages"])).mappings().one()
        command_row = (
            connection.execute(select(tables["command_idempotency_records"])).mappings().one()
        )
        all_rows = {
            name: connection.execute(select(table)).mappings().all()
            for name, table in tables.items()
        }
    assert request_row["status"] == "TriagePending"
    assert request_row["version"] == 2
    assert request_row["category"] is request_row["priority"] is None
    assert operation["operation_kind"] == "AIInterpretation" and operation["version"] == 1
    assert operation["succeeded_attempt_id"] is None
    assert attempt["state"] == "Pending" and attempt["attempt_number"] == 1
    assert attempt["assigned_workflow_service"] == "workflow.start-ai.test"
    assert attempt["workflow_environment"] == "test"
    assert credential["state"] == "Active" and credential["credential_version"] == 1
    credential_digest = hashlib.sha256(plaintext.encode()).hexdigest()
    assert credential["credential_hash"] == credential_digest
    assert credential["expires_at"] == attempt["callback_authorization_deadline"]
    assert (credential["expires_at"] - credential["issued_at"]).total_seconds() == 1800
    assert {row["event_name"] for row in audits} == {
        "service_request.ai_interpretation_started",
        "integration_attempt.created",
    }
    attempt_audit = next(
        row for row in audits if row["event_name"] == "integration_attempt.created"
    )
    assert attempt_audit["actor_reference_id"] == identity_id
    assert outbox["audit_event_id"] == attempt_audit["id"]
    assert outbox["publication_state"] == "Pending"
    assert command_row["status"] == "Completed" and command_row["logical_http_status"] == 202
    assert command_row["callback_credential_id"] == credential["id"]
    assert command_row["safe_response_snapshot"]["versions"]["service_request"] == 2
    serialized_rows = json.dumps(all_rows, default=str)
    assert plaintext not in serialized_rows
    assert "command-key-0001" not in serialized_rows
    assert [
        name
        for name, rows in all_rows.items()
        if credential_digest in json.dumps(rows, default=str)
    ] == ["attempt_callback_credentials"]
    assert "private@example.test" not in json.dumps(outbox["payload"])
    assert "Private home context" not in json.dumps(outbox["payload"])
    assert counts(engine)["ai_interpretations"] == 0
    for row in (operation, attempt, credential, *audits, outbox, command_row):
        for key, value in row.items():
            if key.endswith("_at") and value is not None:
                assert value.tzinfo is not None and value.utcoffset() == timedelta(0)


def test_exact_replay_returns_safe_receipt_without_mutation(command_context) -> None:
    client, engine, generator, _, _ = command_context
    request_id = seed_request(engine)
    first = post_command(client, request_id, nonce="start-ai-replay-00000001")
    before = counts(engine)
    replay_correlation = str(uuid.uuid4())
    replay = post_command(
        client,
        request_id,
        nonce="start-ai-replay-00000002",
        correlation=replay_correlation,
    )
    assert first.status_code == 202 and replay.status_code == 200
    assert replay.json()["command_id"] == first.json()["command_id"]
    assert (
        replay.json()["result"]["integration_attempt_id"]
        == first.json()["result"]["integration_attempt_id"]
    )
    assert replay.json()["correlation_id"] == replay_correlation
    assert replay.json()["result"]["credential_delivery"] == "AlreadyIssued"
    assert "callback_credential" not in replay.json()["result"]
    assert generator.calls == 1
    assert counts(engine) == before


def test_different_body_conflicts_before_domain_and_generator(command_context) -> None:
    client, engine, generator, _, _ = command_context
    request_id = seed_request(engine)
    assert post_command(client, request_id, nonce="start-ai-conflict-000001").status_code == 202
    before = counts(engine)
    with engine.begin() as connection:
        connection.execute(
            update(Base.metadata.tables["service_requests"])
            .where(Base.metadata.tables["service_requests"].c.id == request_id)
            .values(version=99)
        )
    response = post_command(
        client,
        request_id,
        version=2,
        nonce="start-ai-conflict-000002",
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "COMMAND_IDEMPOTENCY_CONFLICT"
    assert generator.calls == 1
    after = counts(engine)
    assert after == before


@pytest.mark.parametrize(
    ("case", "status", "version", "expected_status", "code"),
    [
        ("missing", "TriagePending", 1, 404, "RESOURCE_NOT_FOUND"),
        ("stale", "TriagePending", 2, 409, "CONCURRENCY_CONFLICT"),
        ("state", "HumanReview", 1, 409, "INVALID_STATE_TRANSITION"),
    ],
)
def test_domain_guards_are_stored_and_replayed(
    command_context, case, status, version, expected_status, code
) -> None:
    client, engine, generator, _, _ = command_context
    request_id = (
        uuid.uuid4() if case == "missing" else seed_request(engine, status=status, version=version)
    )
    first = post_command(client, request_id, nonce=f"guard-{case}-nonce-000001")
    assert first.status_code == expected_status
    assert first.json()["error"]["code"] == code
    if case == "stale":
        assert first.json()["error"]["current_versions"] == {"service_request": 2}
    with engine.begin() as connection:
        if case != "missing":
            connection.execute(
                update(Base.metadata.tables["service_requests"])
                .where(Base.metadata.tables["service_requests"].c.id == request_id)
                .values(status="TriagePending", version=1)
            )
    replay = post_command(client, request_id, nonce=f"guard-{case}-nonce-000002")
    assert replay.status_code == expected_status
    assert replay.json()["error"]["code"] == code
    assert generator.calls == 0
    observed = counts(engine)
    assert observed["command_idempotency_records"] == 1
    assert all(
        observed[name] == 0
        for name in (
            "logical_operations",
            "integration_attempts",
            "attempt_callback_credentials",
            "audit_events",
            "outbox_messages",
        )
    )


def seed_matching_operation(engine, request_id, settings, state):
    factory = create_session_factory(engine)
    with factory() as session:
        request = session.get(ServiceRequest, request_id)
        operation = LogicalOperation(
            id=uuid.uuid4(),
            service_request_id=request_id,
            operation_kind="AIInterpretation",
            input_hash=ai_input_hash(request),
            configuration_hash=ai_configuration_hash(settings),
            prompt_version=settings.ai_interpretation_prompt_version,
            result_schema_version=settings.ai_interpretation_result_schema_version,
            provider_name=settings.ai_provider_name,
            model_name=settings.ai_model_name,
            adapter_name=settings.ai_adapter_name,
            adapter_version=settings.ai_adapter_version,
            version=1,
        )
        attempt = IntegrationAttempt(
            id=uuid.uuid4(),
            logical_operation_id=operation.id,
            service_request_id=request_id,
            operation_kind="AIInterpretation",
            attempt_number=1,
            state=state,
            version=1,
            adapter_name=settings.ai_adapter_name,
            adapter_version=settings.ai_adapter_version,
            assigned_workflow_service="workflow.start-ai.test",
            workflow_environment="test",
            callback_authorization_deadline=NOW + timedelta(hours=1),
            started_at=NOW if state in ("Running", "Succeeded") else None,
            completed_at=NOW
            if state in ("Succeeded", "RetryableFailure", "TerminalFailure")
            else None,
            result_hash="a" * 64 if state == "Succeeded" else None,
            sanitized_error_code=(
                "SYNTHETIC_FAILURE" if state in ("RetryableFailure", "TerminalFailure") else None
            ),
        )
        session.add(operation)
        session.flush()
        session.add(attempt)
        session.commit()
        if state == "Succeeded":
            operation.succeeded_attempt_id = attempt.id
            session.commit()


@pytest.mark.parametrize(
    ("state", "code"),
    [
        ("Pending", "ACTIVE_ATTEMPT_EXISTS"),
        ("Running", "ACTIVE_ATTEMPT_EXISTS"),
        ("Succeeded", "LOGICAL_OPERATION_ALREADY_SUCCEEDED"),
        ("RetryableFailure", "RETRY_NOT_ALLOWED"),
        ("TerminalFailure", "RETRY_NOT_ALLOWED"),
    ],
)
def test_existing_operation_guards(command_context, state, code) -> None:
    client, engine, generator, settings, _ = command_context
    request_id = seed_request(engine)
    seed_matching_operation(engine, request_id, settings, state)
    before = counts(engine)
    response = post_command(client, request_id, nonce=f"operation-{state}-nonce-0001")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == code
    assert generator.calls == 0
    after = counts(engine)
    assert after["logical_operations"] == before["logical_operations"] == 1
    assert after["integration_attempts"] == before["integration_attempts"] == 1
    assert after["command_idempotency_records"] == 1
    assert after["audit_events"] == after["outbox_messages"] == 0


def test_authentication_transport_order_and_nonce_commit(command_context) -> None:
    client, engine, generator, _, _ = command_context
    request_id = seed_request(engine)
    path = PATH_TEMPLATE.format(request_id=request_id)
    missing = client.post(path, content=command_body())
    assert missing.status_code == 401
    assert "www-authenticate" not in missing.headers
    body = command_body()
    headers = signed_headers(request_id, body, nonce="invalid-body-nonce-000001")
    headers.pop("Idempotency-Key")
    missing_key = client.post(path, content=body, headers=headers)
    assert missing_key.status_code == 400
    malformed = b"{"
    invalid = client.post(
        path,
        content=malformed,
        headers=signed_headers(request_id, malformed, nonce="invalid-body-nonce-000002"),
    )
    assert invalid.status_code == 400 and invalid.json()["error"]["code"] == "INVALID_COMMAND"
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(Base.metadata.tables["machine_request_nonces"])
            )
            == 2
        )
    assert counts(engine)["command_idempotency_records"] == 0
    assert generator.calls == 0
    human = client.get(f"/api/v1/service-requests/{request_id}")
    assert human.status_code == 401 and human.headers["www-authenticate"] == "Bearer"


def test_generator_failure_rolls_back_processing_reservation(command_context) -> None:
    client, engine, _, _, _ = command_context
    request_id = seed_request(engine)

    def unavailable():
        raise RuntimeError("synthetic generator outage")

    client.app.state.callback_credential_generator = unavailable
    response = post_command(client, request_id, nonce="generator-failure-nonce-001")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    observed = counts(engine)
    assert all(observed[name] == 0 for name in observed if name != "ai_interpretations")
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(Base.metadata.tables["service_requests"].c.version).where(
                    Base.metadata.tables["service_requests"].c.id == request_id
                )
            )
            == 1
        )


def test_concurrent_identical_command_executes_once(command_context) -> None:
    client, engine, generator, _, _ = command_context
    request_id = seed_request(engine)

    def invoke(index):
        return post_command(
            client,
            request_id,
            nonce=f"concurrent-same-{index}-00000001",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(invoke, (1, 2)))
    assert sorted(response.status_code for response in responses) == [200, 202]
    assert generator.calls == 1
    assert sum("callback_credential" in response.json()["result"] for response in responses) == 1
    observed = counts(engine)
    assert observed["logical_operations"] == observed["integration_attempts"] == 1
    assert observed["attempt_callback_credentials"] == 1
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(Base.metadata.tables["service_requests"].c.version).where(
                    Base.metadata.tables["service_requests"].c.id == request_id
                )
            )
            == 2
        )


def test_concurrent_different_keys_use_request_version_lock(command_context) -> None:
    client, engine, generator, _, _ = command_context
    request_id = seed_request(engine)

    def invoke(index):
        return post_command(
            client,
            request_id,
            nonce=f"concurrent-keys-{index}-0000001",
            key=f"different-command-key-{index}",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(invoke, (1, 2)))
    assert sorted(response.status_code for response in responses) == [202, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "CONCURRENCY_CONFLICT"
    assert conflict.json()["error"]["current_versions"] == {"service_request": 2}
    assert generator.calls == 1
    observed = counts(engine)
    assert observed["logical_operations"] == observed["integration_attempts"] == 1
    assert observed["command_idempotency_records"] == 2


@pytest.mark.parametrize(
    ("verb", "table"),
    [
        ("insert", "logical_operations"),
        ("insert", "integration_attempts"),
        ("insert", "attempt_callback_credentials"),
        ("insert", "audit_events"),
        ("insert", "outbox_messages"),
        ("update", "command_idempotency_records"),
    ],
)
def test_forced_write_failure_rolls_back_everything(command_context, verb, table) -> None:
    client, engine, _, _, _ = command_context
    request_id = seed_request(engine)

    def fail_target(_connection, _cursor, statement, _parameters, _context, _many):
        prefix = f"insert into {table}" if verb == "insert" else f"update {table}"
        if statement.lstrip().lower().startswith(prefix):
            raise RuntimeError("synthetic write failure")

    event.listen(engine, "before_cursor_execute", fail_target)
    try:
        response = post_command(
            client,
            request_id,
            nonce=f"forced-{table[:12]}-nonce-0001",
        )
    finally:
        event.remove(engine, "before_cursor_execute", fail_target)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "callback_credential" not in response.text
    assert "synthetic" not in response.text
    assert all(value == 0 for value in counts(engine).values())
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(Base.metadata.tables["service_requests"].c.version).where(
                    Base.metadata.tables["service_requests"].c.id == request_id
                )
            )
            == 1
        )


def test_forced_commit_failure_returns_no_plaintext_and_rolls_back(command_context) -> None:
    client, engine, _, _, _ = command_context
    request_id = seed_request(engine)

    def mark_command(connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().lower().startswith("insert into command_idempotency_records"):
            connection.info["fail_start_ai_commit"] = True

    def fail_commit(connection):
        if connection.info.pop("fail_start_ai_commit", False):
            raise RuntimeError("synthetic commit failure")

    event.listen(engine, "before_cursor_execute", mark_command)
    event.listen(engine, "commit", fail_commit)
    try:
        response = post_command(client, request_id, nonce="forced-commit-nonce-000001")
    finally:
        event.remove(engine, "before_cursor_execute", mark_command)
        event.remove(engine, "commit", fail_commit)
    assert response.status_code == 500
    assert "callback_credential" not in response.text
    assert all(value == 0 for value in counts(engine).values())


def test_callback_hash_collision_rolls_back_without_secret(command_context) -> None:
    client, engine, generator, settings, _ = command_context
    other_request = seed_request(engine)
    seed_matching_operation(engine, other_request, settings, "Pending")
    tables = Base.metadata.tables
    with engine.connect() as connection:
        other_attempt = connection.scalar(select(tables["integration_attempts"].c.id))
    with engine.begin() as connection:
        connection.execute(
            insert(tables["attempt_callback_credentials"]).values(
                id=uuid.uuid4(),
                integration_attempt_id=other_attempt,
                operation_kind="AIInterpretation",
                workflow_service_identity="workflow.start-ai.test",
                workflow_environment="test",
                credential_version=1,
                credential_hash=hashlib.sha256(("A" * 43).encode()).hexdigest(),
                state="Active",
                expires_at=NOW + timedelta(hours=1),
            )
        )
    target_request = seed_request(engine)
    before = counts(engine)
    response = post_command(client, target_request, nonce="hash-collision-nonce-00001")
    assert response.status_code == 500
    assert "A" * 43 not in response.text
    assert "constraint" not in response.text.lower()
    assert generator.calls == 1
    assert counts(engine) == before
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(tables["service_requests"].c.version).where(
                    tables["service_requests"].c.id == target_request
                )
            )
            == 1
        )


def test_concurrent_same_key_different_body_is_one_success_one_conflict(
    command_context,
) -> None:
    client, engine, _, _, _ = command_context
    request_id = seed_request(engine)
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def blocking_generator():
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(timeout=5)
        return "Z" * 43

    client.app.state.callback_credential_generator = blocking_generator
    with ThreadPoolExecutor(max_workers=2) as pool:
        success_future = pool.submit(
            post_command,
            client,
            request_id,
            version=1,
            nonce="same-key-body-one-000001",
        )
        assert entered.wait(timeout=5)
        conflict_future = pool.submit(
            post_command,
            client,
            request_id,
            version=2,
            nonce="same-key-body-two-000001",
        )
        release.set()
        responses = [success_future.result(timeout=10), conflict_future.result(timeout=10)]
    assert sorted(response.status_code for response in responses) == [202, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "COMMAND_IDEMPOTENCY_CONFLICT"
    assert calls == 1
    observed = counts(engine)
    assert observed["logical_operations"] == observed["integration_attempts"] == 1
    assert observed["command_idempotency_records"] == 1
