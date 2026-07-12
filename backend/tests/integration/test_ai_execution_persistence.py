import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from ai_operations_automation.app import create_app
from ai_operations_automation.config import Settings
from ai_operations_automation.db import Base, create_database_engine, create_session_factory
from alembic import command

pytestmark = pytest.mark.integration
BACKEND_ROOT = Path(__file__).resolve().parents[2]
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


class TokenVerifier:
    def verify(self, token: str) -> str:
        return token


@pytest.fixture(scope="module")
def engine() -> Engine:
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")
    value = create_database_engine(Settings(_env_file=None).database_url)
    yield value
    value.dispose()


@pytest.fixture
def client(engine: Engine) -> TestClient:
    tables = ", ".join(f'"{name}"' for name in Base.metadata.tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} CASCADE"))
    return TestClient(
        create_app(
            Settings(_env_file=None),
            create_session_factory(engine),
            jwt_verifier=TokenVerifier(),
        )
    )


def create_request(client: TestClient) -> tuple[uuid.UUID, str]:
    response = client.post(
        "/api/v1/intake/service-requests",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={
            "schema_version": "1.0",
            "contact": {"display_name": "AI Foundation Contact", "email": "ai@example.com"},
            "service_request": {"description": "Inspect the ventilation system condition."},
        },
    )
    assert response.status_code == 201
    return uuid.UUID(response.json()["result"]["service_request_id"]), response.headers["location"]


def grant_reader(engine: Engine, subject: str = "ai-reader") -> None:
    actors = Base.metadata.tables["application_actors"]
    roles = Base.metadata.tables["application_actor_role_assignments"]
    actor_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            insert(actors).values(
                id=actor_id, supabase_subject=subject, display_label="AI foundation reader"
            )
        )
        connection.execute(
            insert(roles).values(
                id=uuid.uuid4(),
                actor_id=actor_id,
                role="OperationsAgent",
                assigned_by_actor_id=actor_id,
                effective_from=datetime.now(UTC),
                assignment_reason="integration fixture",
            )
        )


def operation_values(request_id: uuid.UUID, **overrides):
    values = {
        "id": uuid.uuid4(),
        "service_request_id": request_id,
        "operation_kind": "AIInterpretation",
        "input_hash": HASH_A,
        "configuration_hash": HASH_B,
        "prompt_version": "prompt-v1",
        "result_schema_version": "interpretation-v1",
        "provider_name": "test-provider",
        "model_name": "test-model",
        "adapter_name": "test-adapter",
        "adapter_version": "1.0",
    }
    values.update(overrides)
    return values


def attempt_values(operation_id: uuid.UUID, request_id: uuid.UUID, **overrides):
    values = {
        "id": uuid.uuid4(),
        "logical_operation_id": operation_id,
        "service_request_id": request_id,
        "operation_kind": "AIInterpretation",
        "attempt_number": 1,
        "state": "Pending",
        "adapter_name": "test-adapter",
        "adapter_version": "1.0",
        "assigned_workflow_service": "workflow-test",
        "workflow_environment": "integration",
        "callback_authorization_deadline": datetime.now(UTC) + timedelta(hours=1),
    }
    values.update(overrides)
    return values


def credential_values(attempt_id: uuid.UUID, **overrides):
    values = {
        "id": uuid.uuid4(),
        "integration_attempt_id": attempt_id,
        "operation_kind": "AIInterpretation",
        "workflow_service_identity": "workflow-test",
        "workflow_environment": "integration",
        "credential_version": 1,
        "credential_hash": HASH_C,
        "state": "Active",
        "expires_at": datetime.now(UTC) + timedelta(minutes=30),
    }
    values.update(overrides)
    return values


def interpretation_values(
    request_id: uuid.UUID, operation_id: uuid.UUID, attempt_id: uuid.UUID, **overrides
):
    values = {
        "id": uuid.uuid4(),
        "service_request_id": request_id,
        "logical_operation_id": operation_id,
        "producing_attempt_id": attempt_id,
        "interpretation_number": 1,
        "summary": "Ventilation inspection requested.",
        "suggested_category": "Inspection",
        "missing_information": [],
        "confidence": Decimal("0.7500"),
        "input_hash": HASH_A,
        "configuration_hash": HASH_B,
        "result_schema_version": "interpretation-v1",
        "prompt_version": "prompt-v1",
        "provider_name": "test-provider",
        "model_name": "test-model",
        "adapter_name": "test-adapter",
        "adapter_version": "1.0",
        "warnings": [],
        "latency_ms": 125,
        "usage_metadata": {"input_units": 10, "output_units": 5},
    }
    values.update(overrides)
    return values


def insert_operation(engine: Engine, request_id: uuid.UUID, **overrides) -> uuid.UUID:
    values = operation_values(request_id, **overrides)
    with engine.begin() as connection:
        connection.execute(insert(Base.metadata.tables["logical_operations"]).values(**values))
    return values["id"]


def insert_attempt(engine: Engine, operation_id: uuid.UUID, request_id: uuid.UUID, **overrides):
    values = attempt_values(operation_id, request_id, **overrides)
    with engine.begin() as connection:
        connection.execute(insert(Base.metadata.tables["integration_attempts"]).values(**values))
    return values["id"]


def insert_succeeded_attempt(
    engine: Engine, operation_id: uuid.UUID, request_id: uuid.UUID, **overrides
) -> uuid.UUID:
    now = datetime.now(UTC)
    return insert_attempt(
        engine,
        operation_id,
        request_id,
        state="Succeeded",
        started_at=now,
        completed_at=now,
        result_hash=HASH_C,
        **overrides,
    )


def assert_rejected(engine: Engine, statement) -> None:
    with pytest.raises(SQLAlchemyError):
        with engine.begin() as connection:
            connection.execute(statement)


def assert_constraint(engine: Engine, statement, expected: str) -> None:
    with pytest.raises(IntegrityError) as captured:
        with engine.begin() as connection:
            connection.execute(statement)
    assert captured.value.orig.diag.constraint_name == expected


def test_valid_logical_operation_and_immutable_identity(client, engine) -> None:
    request_id, _ = create_request(client)
    operation_id = insert_operation(engine, request_id)
    operations = Base.metadata.tables["logical_operations"]
    assert operation_id is not None
    assert_rejected(engine, insert(operations).values(**operation_values(request_id)))


@pytest.mark.parametrize(
    "overrides",
    [
        {"operation_kind": "OutboundAction"},
        {"input_hash": "invalid"},
        {"configuration_hash": "A" * 64},
        {"prompt_version": " "},
        {"result_schema_version": ""},
        {"provider_name": " "},
        {"model_name": ""},
        {"adapter_name": " "},
        {"adapter_version": ""},
        {"version": 0},
    ],
)
def test_logical_operation_constraints(client, engine, overrides) -> None:
    request_id, _ = create_request(client)
    assert_rejected(
        engine,
        insert(Base.metadata.tables["logical_operations"]).values(
            **operation_values(request_id, **overrides)
        ),
    )


def test_attempt_uniqueness_active_success_and_parent_restriction(client, engine) -> None:
    request_id, _ = create_request(client)
    operation_id = insert_operation(engine, request_id)
    attempts = Base.metadata.tables["integration_attempts"]
    operations = Base.metadata.tables["logical_operations"]
    insert_attempt(engine, operation_id, request_id)
    assert_rejected(engine, insert(attempts).values(**attempt_values(operation_id, request_id)))
    assert_rejected(
        engine,
        insert(attempts).values(**attempt_values(operation_id, request_id, attempt_number=2)),
    )
    assert_rejected(engine, delete(operations).where(operations.c.id == operation_id))

    second_operation = insert_operation(engine, request_id, input_hash="d" * 64)
    now = datetime.now(UTC)
    insert_attempt(
        engine,
        second_operation,
        request_id,
        state="Succeeded",
        started_at=now,
        completed_at=now,
        result_hash=HASH_C,
    )
    assert_rejected(
        engine,
        insert(attempts).values(
            **attempt_values(
                second_operation,
                request_id,
                attempt_number=2,
                state="Succeeded",
                started_at=now,
                completed_at=now,
                result_hash="d" * 64,
            )
        ),
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"attempt_number": 0},
        {"attempt_number": 4},
        {"operation_kind": "OutboundAction"},
        {"state": "Unknown"},
        {"state": "Running"},
        {"state": "Pending", "started_at": datetime.now(UTC)},
        {"callback_authorization_deadline": datetime.now(UTC) - timedelta(hours=1)},
        {"state": "Succeeded", "result_hash": "invalid"},
        {
            "state": "TerminalFailure",
            "completed_at": datetime.now(UTC),
            "sanitized_error_code": "provider message",
        },
    ],
)
def test_attempt_constraints(client, engine, overrides) -> None:
    request_id, _ = create_request(client)
    operation_id = insert_operation(engine, request_id)
    assert_rejected(
        engine,
        insert(Base.metadata.tables["integration_attempts"]).values(
            **attempt_values(operation_id, request_id, **overrides)
        ),
    )


def test_callback_credential_constraints(client, engine) -> None:
    request_id, _ = create_request(client)
    operation_id = insert_operation(engine, request_id)
    attempt_id = insert_attempt(engine, operation_id, request_id)
    credentials = Base.metadata.tables["attempt_callback_credentials"]
    valid = credential_values(attempt_id)
    with engine.begin() as connection:
        connection.execute(insert(credentials).values(**valid))
    assert_rejected(engine, insert(credentials).values(**credential_values(attempt_id)))
    assert_rejected(
        engine,
        insert(credentials).values(
            **credential_values(attempt_id, credential_version=2, credential_hash=HASH_C)
        ),
    )
    assert_rejected(
        engine,
        delete(Base.metadata.tables["integration_attempts"]).where(
            Base.metadata.tables["integration_attempts"].c.id == attempt_id
        ),
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"credential_hash": "invalid"},
        {"credential_version": 0},
        {"expires_at": datetime.now(UTC) - timedelta(minutes=1)},
        {"state": "Consumed"},
        {"state": "Revoked"},
        {"state": "Active", "revoked_at": datetime.now(UTC)},
        {"operation_kind": "OutboundAction"},
        {"replacement_credential_id": uuid.uuid4()},
    ],
)
def test_callback_credential_state_hash_and_fk_constraints(client, engine, overrides) -> None:
    request_id, _ = create_request(client)
    operation_id = insert_operation(engine, request_id)
    attempt_id = insert_attempt(engine, operation_id, request_id)
    assert_rejected(
        engine,
        insert(Base.metadata.tables["attempt_callback_credentials"]).values(
            **credential_values(attempt_id, **overrides)
        ),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workflow_service_identity", ""),
        ("workflow_service_identity", "   "),
        ("workflow_environment", ""),
        ("workflow_environment", "   "),
    ],
)
def test_callback_scope_must_be_nonblank(client, engine, field, value) -> None:
    request_id, _ = create_request(client)
    operation_id = insert_operation(engine, request_id)
    attempt_id = insert_attempt(engine, operation_id, request_id)
    assert_rejected(
        engine,
        insert(Base.metadata.tables["attempt_callback_credentials"]).values(
            **credential_values(attempt_id, **{field: value})
        ),
    )


def test_atomic_callback_credential_replacement(client, engine) -> None:
    request_id, _ = create_request(client)
    operation_id = insert_operation(engine, request_id)
    attempt_id = insert_attempt(engine, operation_id, request_id)
    credentials = Base.metadata.tables["attempt_callback_credentials"]
    old_id = uuid.uuid4()
    new_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(insert(credentials).values(**credential_values(attempt_id, id=old_id)))
    with engine.begin() as connection:
        connection.execute(
            update(credentials)
            .where(credentials.c.id == old_id)
            .values(
                state="Replaced",
                replaced_at=datetime.now(UTC),
                replacement_credential_id=new_id,
            )
        )
        connection.execute(
            insert(credentials).values(
                **credential_values(
                    attempt_id,
                    id=new_id,
                    credential_version=2,
                    credential_hash="d" * 64,
                )
            )
        )
    with engine.connect() as connection:
        rows = (
            connection.execute(
                select(credentials).where(credentials.c.integration_attempt_id == attempt_id)
            )
            .mappings()
            .all()
        )
    by_id = {row["id"]: row for row in rows}
    assert by_id[old_id]["state"] == "Replaced"
    assert by_id[old_id]["replacement_credential_id"] == new_id
    assert by_id[new_id]["state"] == "Active"
    assert sum(row["state"] == "Active" for row in rows) == 1
    assert all("plaintext" not in row for row in rows)
    assert_constraint(
        engine,
        delete(credentials).where(credentials.c.id == new_id),
        "fk_callback_credential_replacement",
    )


def test_callback_replacement_rolls_back_atomically(client, engine) -> None:
    request_id, _ = create_request(client)
    operation_id = insert_operation(engine, request_id)
    attempt_id = insert_attempt(engine, operation_id, request_id)
    credentials = Base.metadata.tables["attempt_callback_credentials"]
    old_id = uuid.uuid4()
    new_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(insert(credentials).values(**credential_values(attempt_id, id=old_id)))
    with pytest.raises(RuntimeError):
        with engine.begin() as connection:
            connection.execute(
                update(credentials)
                .where(credentials.c.id == old_id)
                .values(
                    state="Replaced",
                    replaced_at=datetime.now(UTC),
                    replacement_credential_id=new_id,
                )
            )
            connection.execute(
                insert(credentials).values(
                    **credential_values(
                        attempt_id,
                        id=new_id,
                        credential_version=2,
                        credential_hash="d" * 64,
                    )
                )
            )
            raise RuntimeError("forced replacement rollback")
    with engine.connect() as connection:
        rows = (
            connection.execute(
                select(credentials).where(credentials.c.integration_attempt_id == attempt_id)
            )
            .mappings()
            .all()
        )
    assert len(rows) == 1
    assert rows[0]["id"] == old_id
    assert rows[0]["state"] == "Active"
    assert rows[0]["replacement_credential_id"] is None
    assert rows[0]["replaced_at"] is None


def test_callback_replacement_self_and_missing_target_are_rejected(client, engine) -> None:
    request_id, _ = create_request(client)
    operation_id = insert_operation(engine, request_id)
    attempt_id = insert_attempt(engine, operation_id, request_id)
    credentials = Base.metadata.tables["attempt_callback_credentials"]
    self_id = uuid.uuid4()
    assert_constraint(
        engine,
        insert(credentials).values(
            **credential_values(
                attempt_id,
                id=self_id,
                state="Replaced",
                replaced_at=datetime.now(UTC),
                replacement_credential_id=self_id,
            )
        ),
        "ck_attempt_callback_credentials_replacement_not_self",
    )
    assert_constraint(
        engine,
        insert(credentials).values(
            **credential_values(
                attempt_id,
                state="Replaced",
                replaced_at=datetime.now(UTC),
                replacement_credential_id=uuid.uuid4(),
            )
        ),
        "fk_callback_credential_replacement",
    )


def test_callback_uniqueness_constraints_are_isolated(client, engine) -> None:
    request_id, _ = create_request(client)
    operation_id = insert_operation(engine, request_id)
    attempt_id = insert_attempt(engine, operation_id, request_id)
    credentials = Base.metadata.tables["attempt_callback_credentials"]
    old_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(insert(credentials).values(**credential_values(attempt_id, id=old_id)))
        connection.execute(
            update(credentials)
            .where(credentials.c.id == old_id)
            .values(state="Revoked", revoked_at=datetime.now(UTC))
        )
    assert_constraint(
        engine,
        insert(credentials).values(
            **credential_values(
                attempt_id,
                credential_hash="d" * 64,
                state="Revoked",
                revoked_at=datetime.now(UTC),
            )
        ),
        "uq_attempt_callback_credentials_attempt_credential_version",
    )

    second_operation = insert_operation(engine, request_id, input_hash="e" * 64)
    second_attempt = insert_attempt(engine, second_operation, request_id)
    assert_constraint(
        engine,
        insert(credentials).values(**credential_values(second_attempt, credential_hash=HASH_C)),
        "uq_attempt_callback_credentials_credential_hash",
    )

    with engine.begin() as connection:
        connection.execute(
            insert(credentials).values(
                **credential_values(
                    attempt_id,
                    credential_version=2,
                    credential_hash="e" * 64,
                )
            )
        )
    assert_constraint(
        engine,
        insert(credentials).values(
            **credential_values(
                attempt_id,
                credential_version=3,
                credential_hash="f" * 64,
            )
        ),
        "uq_attempt_callback_credentials_one_active",
    )


def test_interpretation_uniqueness(client, engine) -> None:
    request_id, _ = create_request(client)
    operation_id = insert_operation(engine, request_id)
    now = datetime.now(UTC)
    attempt_id = insert_attempt(
        engine,
        operation_id,
        request_id,
        state="Succeeded",
        started_at=now,
        completed_at=now,
        result_hash=HASH_C,
    )
    interpretations = Base.metadata.tables["ai_interpretations"]
    with engine.begin() as connection:
        connection.execute(
            insert(interpretations).values(
                **interpretation_values(request_id, operation_id, attempt_id)
            )
        )
    assert_rejected(
        engine,
        insert(interpretations).values(
            **interpretation_values(request_id, operation_id, attempt_id)
        ),
    )


def test_interpretation_uniqueness_constraints_are_isolated(client, engine) -> None:
    request_id, _ = create_request(client)
    interpretations = Base.metadata.tables["ai_interpretations"]

    operation_1 = insert_operation(engine, request_id)
    attempt_1 = insert_succeeded_attempt(engine, operation_1, request_id)
    with engine.begin() as connection:
        connection.execute(
            insert(interpretations).values(
                **interpretation_values(request_id, operation_1, attempt_1)
            )
        )

    operation_2 = insert_operation(engine, request_id, input_hash="d" * 64)
    attempt_2 = insert_succeeded_attempt(engine, operation_2, request_id)
    assert_constraint(
        engine,
        insert(interpretations).values(**interpretation_values(request_id, operation_2, attempt_2)),
        "uq_ai_interpretations_request_number",
    )

    failed_attempt = insert_attempt(
        engine,
        operation_1,
        request_id,
        attempt_number=2,
        state="TerminalFailure",
        completed_at=datetime.now(UTC),
        sanitized_error_code="SYNTHETIC_FAILURE",
    )
    assert_constraint(
        engine,
        insert(interpretations).values(
            **interpretation_values(
                request_id,
                operation_1,
                failed_attempt,
                interpretation_number=2,
            )
        ),
        "uq_ai_interpretations_logical_operation",
    )

    operation_3 = insert_operation(engine, request_id, input_hash="e" * 64)
    assert_constraint(
        engine,
        insert(interpretations).values(
            **interpretation_values(
                request_id,
                operation_3,
                attempt_1,
                interpretation_number=3,
            )
        ),
        "uq_ai_interpretations_producing_attempt",
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"suggested_category": "Unknown"},
        {"confidence": Decimal("-0.1")},
        {"confidence": Decimal("1.1")},
        {"interpretation_number": 0},
        {"summary": " "},
        {"summary": "x" * 2001},
        {"missing_information": {}},
        {"warnings": {}},
        {"input_hash": "invalid"},
        {"configuration_hash": "A" * 64},
        {"latency_ms": -1},
    ],
)
def test_interpretation_constraints(client, engine, overrides) -> None:
    request_id, _ = create_request(client)
    operation_id = insert_operation(engine, request_id)
    now = datetime.now(UTC)
    attempt_id = insert_attempt(
        engine,
        operation_id,
        request_id,
        state="Succeeded",
        started_at=now,
        completed_at=now,
        result_hash=HASH_C,
    )
    assert_rejected(
        engine,
        insert(Base.metadata.tables["ai_interpretations"]).values(
            **interpretation_values(request_id, operation_id, attempt_id, **overrides)
        ),
    )


def test_current_reference_projection_is_read_only(client, engine) -> None:
    request_id, location = create_request(client)
    grant_reader(engine)
    headers = {"Authorization": "Bearer ai-reader"}
    initial = client.get(location, headers=headers)
    assert initial.status_code == 200
    assert initial.json()["result"]["active_references"]["current_interpretation_id"] is None

    operation_id = insert_operation(engine, request_id)
    now = datetime.now(UTC)
    attempt_id = insert_attempt(
        engine,
        operation_id,
        request_id,
        state="Succeeded",
        started_at=now,
        completed_at=now,
        result_hash=HASH_C,
    )
    interpretation_id = uuid.uuid4()
    tables = Base.metadata.tables
    with engine.begin() as connection:
        connection.execute(
            insert(tables["ai_interpretations"]).values(
                **interpretation_values(request_id, operation_id, attempt_id, id=interpretation_id)
            )
        )
        connection.execute(
            update(tables["service_requests"])
            .where(tables["service_requests"].c.id == request_id)
            .values(current_interpretation_id=interpretation_id)
        )
    with engine.connect() as connection:
        before = {
            "audit": connection.scalar(select(func.count()).select_from(tables["audit_events"])),
            "outbox": connection.scalar(
                select(func.count()).select_from(tables["outbox_messages"])
            ),
            "request": connection.scalar(
                select(tables["service_requests"].c.version).where(
                    tables["service_requests"].c.id == request_id
                )
            ),
            "contact": connection.scalar(
                select(tables["contacts"].c.version)
                .join(
                    tables["service_requests"],
                    tables["service_requests"].c.contact_id == tables["contacts"].c.id,
                )
                .where(tables["service_requests"].c.id == request_id)
            ),
            "operation": connection.scalar(
                select(tables["logical_operations"].c.version).where(
                    tables["logical_operations"].c.id == operation_id
                )
            ),
            "attempt": connection.scalar(
                select(tables["integration_attempts"].c.version).where(
                    tables["integration_attempts"].c.id == attempt_id
                )
            ),
        }
    response = client.get(location, headers=headers)
    assert response.status_code == 200
    body = response.json()["result"]
    assert body["active_references"] == {
        "current_interpretation_id": str(interpretation_id),
        "current_routing_decision_id": None,
        "active_proposed_action_id": None,
    }
    assert body["service_request"]["category"] is None
    assert body["service_request"]["priority"] is None
    assert body["service_request"]["current_queue"] is None
    with engine.connect() as connection:
        after = {
            "audit": connection.scalar(select(func.count()).select_from(tables["audit_events"])),
            "outbox": connection.scalar(
                select(func.count()).select_from(tables["outbox_messages"])
            ),
            "request": connection.scalar(
                select(tables["service_requests"].c.version).where(
                    tables["service_requests"].c.id == request_id
                )
            ),
            "contact": connection.scalar(
                select(tables["contacts"].c.version)
                .join(
                    tables["service_requests"],
                    tables["service_requests"].c.contact_id == tables["contacts"].c.id,
                )
                .where(tables["service_requests"].c.id == request_id)
            ),
            "operation": connection.scalar(
                select(tables["logical_operations"].c.version).where(
                    tables["logical_operations"].c.id == operation_id
                )
            ),
            "attempt": connection.scalar(
                select(tables["integration_attempts"].c.version).where(
                    tables["integration_attempts"].c.id == attempt_id
                )
            ),
        }
    assert after == before
    assert_rejected(
        engine,
        delete(tables["ai_interpretations"]).where(
            tables["ai_interpretations"].c.id == interpretation_id
        ),
    )
    assert_rejected(
        engine,
        delete(tables["integration_attempts"]).where(
            tables["integration_attempts"].c.id == attempt_id
        ),
    )
    assert_rejected(
        engine,
        delete(tables["logical_operations"]).where(
            tables["logical_operations"].c.id == operation_id
        ),
    )
    assert_rejected(
        engine,
        delete(tables["service_requests"]).where(tables["service_requests"].c.id == request_id),
    )


def test_atomic_failure_rolls_back_complete_ai_graph(client, engine) -> None:
    request_id, _ = create_request(client)
    tables = Base.metadata.tables
    with engine.connect() as connection:
        request_before = (
            connection.execute(
                select(tables["service_requests"]).where(
                    tables["service_requests"].c.id == request_id
                )
            )
            .mappings()
            .one()
        )
        evidence_before = (
            connection.scalar(select(func.count()).select_from(tables["audit_events"])),
            connection.scalar(select(func.count()).select_from(tables["outbox_messages"])),
        )
    with pytest.raises(RuntimeError):
        with engine.begin() as connection:
            operation = operation_values(request_id)
            connection.execute(insert(tables["logical_operations"]).values(**operation))
            now = datetime.now(UTC)
            attempt = attempt_values(
                operation["id"],
                request_id,
                state="Succeeded",
                started_at=now,
                completed_at=now,
                result_hash=HASH_C,
            )
            connection.execute(insert(tables["integration_attempts"]).values(**attempt))
            connection.execute(
                insert(tables["attempt_callback_credentials"]).values(
                    **credential_values(attempt["id"])
                )
            )
            interpretation = interpretation_values(request_id, operation["id"], attempt["id"])
            connection.execute(insert(tables["ai_interpretations"]).values(**interpretation))
            connection.execute(
                update(tables["service_requests"])
                .where(tables["service_requests"].c.id == request_id)
                .values(current_interpretation_id=interpretation["id"])
            )
            raise RuntimeError("forced rollback")
    with engine.connect() as connection:
        assert all(
            connection.scalar(select(func.count()).select_from(tables[name])) == 0
            for name in (
                "logical_operations",
                "integration_attempts",
                "attempt_callback_credentials",
                "ai_interpretations",
            )
        )
        request_after = (
            connection.execute(
                select(tables["service_requests"]).where(
                    tables["service_requests"].c.id == request_id
                )
            )
            .mappings()
            .one()
        )
        evidence_after = (
            connection.scalar(select(func.count()).select_from(tables["audit_events"])),
            connection.scalar(select(func.count()).select_from(tables["outbox_messages"])),
        )
    assert request_after == request_before
    assert request_after["current_interpretation_id"] is None
    assert evidence_after == evidence_before


def test_ai_timestamps_are_timezone_aware(client, engine) -> None:
    request_id, _ = create_request(client)
    operation_id = insert_operation(engine, request_id)
    attempt_id = insert_succeeded_attempt(engine, operation_id, request_id)
    credential_id = uuid.uuid4()
    interpretation_id = uuid.uuid4()
    tables = Base.metadata.tables
    with engine.begin() as connection:
        connection.execute(
            insert(tables["attempt_callback_credentials"]).values(
                **credential_values(attempt_id, id=credential_id)
            )
        )
        connection.execute(
            insert(tables["ai_interpretations"]).values(
                **interpretation_values(
                    request_id,
                    operation_id,
                    attempt_id,
                    id=interpretation_id,
                )
            )
        )
    with engine.connect() as connection:
        timestamps = [
            connection.scalar(
                select(tables["logical_operations"].c.created_at).where(
                    tables["logical_operations"].c.id == operation_id
                )
            ),
            connection.scalar(
                select(tables["integration_attempts"].c.created_at).where(
                    tables["integration_attempts"].c.id == attempt_id
                )
            ),
            connection.scalar(
                select(tables["attempt_callback_credentials"].c.issued_at).where(
                    tables["attempt_callback_credentials"].c.id == credential_id
                )
            ),
            connection.scalar(
                select(tables["ai_interpretations"].c.created_at).where(
                    tables["ai_interpretations"].c.id == interpretation_id
                )
            ),
        ]
    assert all(
        value.tzinfo is not None and value.utcoffset() == timedelta(0) for value in timestamps
    )
