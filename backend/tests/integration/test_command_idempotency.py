import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, func, insert, inspect, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from ai_operations_automation.app import create_app
from ai_operations_automation.command_idempotency import (
    CommandIdempotencyScope,
    CommandIdempotencyService,
    CompletedCommandReplay,
    NewCommandReservation,
    SecretDeliveryMetadata,
)
from ai_operations_automation.command_idempotency.keys import command_key_digest
from ai_operations_automation.config import Settings
from ai_operations_automation.db import (
    Base,
    create_database_engine,
    create_session_factory,
)
from ai_operations_automation.intake.errors import IntakeError
from alembic import command

pytestmark = pytest.mark.integration
BACKEND_ROOT = Path(__file__).resolve().parents[2]
HASH_A = "a" * 64
HASH_B = "b" * 64


def alembic_config() -> Config:
    return Config(str(BACKEND_ROOT / "alembic.ini"))


@pytest.fixture(scope="module")
def engine() -> Engine:
    command.upgrade(alembic_config(), "head")
    value = create_database_engine(Settings(_env_file=None).database_url)
    yield value
    value.dispose()


@pytest.fixture(autouse=True)
def clean_database(engine: Engine) -> None:
    tables = ", ".join(f'"{name}"' for name in Base.metadata.tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} CASCADE"))


def scope(**changes) -> CommandIdempotencyScope:
    values = {
        "actor_class": "MachineService",
        "actor_id": uuid.uuid4(),
        "command_intent": "StartAiInterpretation",
        "route_template": (
            "/api/v1/service-requests/{request_id}/commands/start-ai-interpretation"
        ),
        "target_type": "ServiceRequest",
        "target_id": uuid.uuid4(),
        **changes,
    }
    return CommandIdempotencyScope(**values)


def processing_values(selected_scope=None, **changes):
    selected_scope = selected_scope or scope()
    values = {
        "id": uuid.uuid4(),
        **selected_scope.model_dump(),
        "idempotency_key_digest": HASH_A,
        "canonical_body_hash": HASH_B,
        "status": "Processing",
        "command_id": uuid.uuid4(),
        "correlation_id": uuid.uuid4(),
        **changes,
    }
    return values


def completed_values(selected_scope=None, **changes):
    created = datetime.now(UTC) - timedelta(seconds=1)
    values = processing_values(selected_scope)
    values.update(
        status="Completed",
        logical_http_status=202,
        safe_response_snapshot={"result": "accepted"},
        created_at=created,
        completed_at=created + timedelta(milliseconds=1),
    )
    values.update(changes)
    return values


def assert_rejected(engine: Engine, values: dict) -> str:
    with pytest.raises(IntegrityError) as captured:
        with engine.begin() as connection:
            connection.execute(
                insert(Base.metadata.tables["command_idempotency_records"]).values(**values)
            )
    return captured.value.orig.diag.constraint_name


def test_migration_inventory_downgrade_and_reupgrade(engine: Engine) -> None:
    assert set(inspect(engine).get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
    assert len(Base.metadata.tables) == 16
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0007_command_idempotency_foundation"
        )
    engine.dispose()
    command.downgrade(alembic_config(), "0006_workflow_authentication_foundation")
    assert "command_idempotency_records" not in inspect(engine).get_table_names()
    command.upgrade(alembic_config(), "head")


def test_valid_processing_nonsecret_completed_and_timestamps(engine: Engine) -> None:
    table = Base.metadata.tables["command_idempotency_records"]
    processing = processing_values()
    completed = completed_values(command_id=uuid.uuid4())
    with engine.begin() as connection:
        connection.execute(insert(table).values(**processing))
        connection.execute(insert(table).values(**completed))
    with engine.connect() as connection:
        rows = connection.execute(select(table)).mappings().all()
    assert {row["status"] for row in rows} == {"Processing", "Completed"}
    assert all(row["created_at"].utcoffset() == timedelta(0) for row in rows)
    completed_row = next(row for row in rows if row["status"] == "Completed")
    assert completed_row["completed_at"].utcoffset() == timedelta(0)


def test_full_scope_uniqueness_and_namespace_dimensions(engine: Engine) -> None:
    table = Base.metadata.tables["command_idempotency_records"]
    selected = scope()
    original = processing_values(selected)
    with engine.begin() as connection:
        connection.execute(insert(table).values(**original))
    assert (
        assert_rejected(engine, processing_values(selected, command_id=uuid.uuid4()))
        == "uq_command_idempotency_scope_key"
    )
    variants = [
        scope(**{**selected.model_dump(), "actor_id": uuid.uuid4()}),
        scope(**{**selected.model_dump(), "command_intent": "RetryAi"}),
        scope(**{**selected.model_dump(), "route_template": "/api/v1/retry/{request_id}"}),
        scope(**{**selected.model_dump(), "target_id": uuid.uuid4()}),
    ]
    with engine.begin() as connection:
        for variant in variants:
            connection.execute(insert(table).values(**processing_values(variant)))
    assert len(variants) + 1 == 5


@pytest.mark.parametrize(
    ("changes", "constraint"),
    [
        ({"actor_class": "Customer"}, "ck_command_idem_actor_class_valid"),
        ({"command_intent": ""}, "ck_command_idem_command_intent_valid"),
        ({"target_type": "bad/type"}, "ck_command_idem_target_type_valid"),
        ({"route_template": "relative"}, "ck_command_idem_route_template_valid"),
        ({"idempotency_key_digest": "A" * 64}, "ck_command_idem_key_digest_valid"),
        ({"canonical_body_hash": "bad"}, "ck_command_idem_body_hash_valid"),
        ({"status": "Unknown"}, "ck_command_idem_status_fields_consistent"),
        ({"logical_http_status": 200}, "ck_command_idem_status_fields_consistent"),
    ],
)
def test_processing_structural_constraints(engine: Engine, changes, constraint) -> None:
    assert assert_rejected(engine, processing_values(**changes)) == constraint


@pytest.mark.parametrize(
    "changes",
    [
        {"logical_http_status": None},
        {"logical_http_status": 199},
        {"logical_http_status": 600},
        {"safe_response_snapshot": ["not-object"]},
        {"completed_at": None},
        {
            "created_at": datetime.now(UTC),
            "completed_at": datetime.now(UTC) - timedelta(seconds=1),
        },
    ],
)
def test_completed_structural_constraints(engine: Engine, changes) -> None:
    assert assert_rejected(engine, completed_values(**changes)) == (
        "ck_command_idem_status_fields_consistent"
    )


def seed_callback_credential(engine: Engine) -> tuple[uuid.UUID, datetime]:
    client = TestClient(create_app(Settings(_env_file=None), create_session_factory(engine)))
    intake = client.post(
        "/api/v1/intake/service-requests",
        headers={"Idempotency-Key": "credential-seed-key"},
        json={
            "schema_version": "1.0",
            "contact": {"display_name": "Credential Fixture", "email": "fixture@example.com"},
            "service_request": {"description": "Create synthetic callback credential graph."},
        },
    )
    request_id = uuid.UUID(intake.json()["result"]["service_request_id"])
    operation_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    tables = Base.metadata.tables
    with engine.begin() as connection:
        connection.execute(
            insert(tables["logical_operations"]).values(
                id=operation_id,
                service_request_id=request_id,
                operation_kind="AIInterpretation",
                input_hash="c" * 64,
                configuration_hash="d" * 64,
                prompt_version="v1",
                result_schema_version="v1",
                provider_name="test",
                model_name="test",
                adapter_name="test",
                adapter_version="v1",
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
                adapter_name="test",
                adapter_version="v1",
                assigned_workflow_service="workflow.test",
                workflow_environment="test",
                callback_authorization_deadline=expires_at,
            )
        )
        connection.execute(
            insert(tables["attempt_callback_credentials"]).values(
                id=credential_id,
                integration_attempt_id=attempt_id,
                operation_kind="AIInterpretation",
                workflow_service_identity="workflow.test",
                workflow_environment="test",
                credential_version=1,
                credential_hash="e" * 64,
                state="Active",
                expires_at=expires_at,
            )
        )
    return credential_id, expires_at


def test_secret_metadata_is_all_or_none_and_restricts_delete(engine: Engine) -> None:
    credential_id, expires_at = seed_callback_credential(engine)
    table = Base.metadata.tables["command_idempotency_records"]
    secret = completed_values(
        callback_credential_id=credential_id,
        callback_credential_version=1,
        callback_credential_expires_at=expires_at,
        secret_delivery_receipt="PlaintextIssued",
    )
    with engine.begin() as connection:
        connection.execute(insert(table).values(**secret))
    for changes in (
        {"callback_credential_id": credential_id},
        {
            "callback_credential_id": credential_id,
            "callback_credential_version": 0,
            "callback_credential_expires_at": expires_at,
            "secret_delivery_receipt": "PlaintextIssued",
        },
        {
            "callback_credential_id": credential_id,
            "callback_credential_version": 1,
            "callback_credential_expires_at": expires_at,
            "secret_delivery_receipt": "AlreadyIssued",
        },
    ):
        assert assert_rejected(engine, completed_values(**changes)) == (
            "ck_command_idem_secret_delivery_consistent"
        )
    with pytest.raises(SQLAlchemyError):
        with engine.begin() as connection:
            connection.execute(
                delete(Base.metadata.tables["attempt_callback_credentials"]).where(
                    Base.metadata.tables["attempt_callback_credentials"].c.id == credential_id
                )
            )


def test_reserve_complete_replay_conflict_and_processing(engine: Engine) -> None:
    factory = create_session_factory(engine)
    selected = scope()
    key = "service-test-key"
    with factory() as session, session.begin():
        service = CommandIdempotencyService(session)
        reservation = service.reserve(selected, key, HASH_A, uuid.uuid4())
        assert isinstance(reservation, NewCommandReservation)
        completed = service.complete(reservation, 202, {"command_id": str(reservation.command_id)})
        assert completed.command_id == reservation.command_id
    with factory() as session, session.begin():
        replay = CommandIdempotencyService(session).reserve(selected, key, HASH_A, uuid.uuid4())
        assert isinstance(replay, CompletedCommandReplay)
        assert replay.command_id == reservation.command_id
        with pytest.raises(IntakeError) as conflict:
            CommandIdempotencyService(session).reserve(selected, key, HASH_B, uuid.uuid4())
        assert conflict.value.status_code == 409

    processing_scope = scope()
    with engine.begin() as connection:
        connection.execute(
            insert(Base.metadata.tables["command_idempotency_records"]).values(
                **processing_values(
                    processing_scope,
                    idempotency_key_digest=command_key_digest("a" * 8),
                )
            )
        )
    with factory() as session, session.begin(), pytest.raises(IntakeError) as captured:
        CommandIdempotencyService(session).reserve(processing_scope, "a" * 8, HASH_B, uuid.uuid4())
    assert captured.value.status_code == 500


def test_atomic_success_failure_and_double_completion(engine: Engine) -> None:
    factory = create_session_factory(engine)
    selected = scope()
    contact_id = uuid.uuid4()
    with factory() as session, session.begin():
        service = CommandIdempotencyService(session)
        reservation = service.reserve(selected, "atomic-success", HASH_A, uuid.uuid4())
        session.execute(
            insert(Base.metadata.tables["contacts"]).values(
                id=contact_id, display_label="Synthetic mutation", version=1
            )
        )
        service.complete(reservation, 200, {"contact_id": str(contact_id)})
        with pytest.raises(ValueError):
            service.complete(reservation, 200, {"contact_id": str(contact_id)})
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(
                    Base.metadata.tables["command_idempotency_records"]
                )
            )
            == 1
        )
        assert connection.scalar(select(Base.metadata.tables["contacts"].c.id)) == contact_id

    rollback_scope = scope()
    rollback_contact = uuid.uuid4()
    with pytest.raises(RuntimeError):
        with factory() as session, session.begin():
            service = CommandIdempotencyService(session)
            service.reserve(rollback_scope, "atomic-rollback", HASH_A, uuid.uuid4())
            session.execute(
                insert(Base.metadata.tables["contacts"]).values(
                    id=rollback_contact, display_label="Rolled back", version=1
                )
            )
            raise RuntimeError("forced domain rollback")
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(Base.metadata.tables["contacts"].c.id).where(
                    Base.metadata.tables["contacts"].c.id == rollback_contact
                )
            )
            is None
        )


def test_database_lookup_and_completion_failures_are_safe_and_atomic(
    engine: Engine, monkeypatch
) -> None:
    factory = create_session_factory(engine)
    with factory() as session, session.begin():
        service = CommandIdempotencyService(session)

        def fail_lookup(*_args, **_kwargs):
            raise SQLAlchemyError("hidden database detail")

        monkeypatch.setattr(service, "_find", fail_lookup)
        with pytest.raises(IntakeError) as captured:
            service.reserve(scope(), "lookup-failure", HASH_A, uuid.uuid4())
        assert captured.value.status_code == 503
        assert "hidden" not in str(captured.value)

    selected = scope()
    contact_id = uuid.uuid4()
    with pytest.raises(IntakeError) as completion_failure:
        with factory() as session, session.begin():
            service = CommandIdempotencyService(session)
            reservation = service.reserve(selected, "completion-failure", HASH_A, uuid.uuid4())
            session.execute(
                insert(Base.metadata.tables["contacts"]).values(
                    id=contact_id, display_label="Completion rollback", version=1
                )
            )

            def fail_flush(*_args, **_kwargs):
                raise SQLAlchemyError("hidden completion detail")

            monkeypatch.setattr(session, "flush", fail_flush)
            service.complete(reservation, 200, {"contact_id": str(contact_id)})
    assert completion_failure.value.status_code == 503
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(Base.metadata.tables["contacts"].c.id).where(
                    Base.metadata.tables["contacts"].c.id == contact_id
                )
            )
            is None
        )
        assert (
            connection.scalar(
                select(func.count()).select_from(
                    Base.metadata.tables["command_idempotency_records"]
                )
            )
            == 0
        )


def test_intake_and_command_key_namespaces_are_independent(engine: Engine) -> None:
    raw_key = "shared-namespace-key"
    factory = create_session_factory(engine)
    with factory() as session, session.begin():
        service = CommandIdempotencyService(session)
        reservation = service.reserve(scope(), raw_key, HASH_A, uuid.uuid4())
        service.complete(reservation, 200, {"result": "command"})
    client = TestClient(create_app(Settings(_env_file=None), factory))
    intake = client.post(
        "/api/v1/intake/service-requests",
        headers={"Idempotency-Key": raw_key},
        json={
            "schema_version": "1.0",
            "contact": {"display_name": "Namespace Fixture", "email": "namespace@example.com"},
            "service_request": {"description": "Verify independent idempotency namespaces."},
        },
    )
    assert intake.status_code == 201
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(
                    Base.metadata.tables["command_idempotency_records"]
                )
            )
            == 1
        )
        assert (
            connection.scalar(
                select(func.count()).select_from(Base.metadata.tables["accepted_intake_keys"])
            )
            == 1
        )


def concurrent_run(engine: Engine, hashes: list[str]):
    factory = create_session_factory(engine)
    selected = scope()
    start = threading.Barrier(2)
    executions = 0
    lock = threading.Lock()

    def worker(body_hash: str):
        nonlocal executions
        start.wait(timeout=5)
        try:
            with factory() as session, session.begin():
                service = CommandIdempotencyService(session)
                result = service.reserve(selected, "concurrent-command", body_hash, uuid.uuid4())
                if isinstance(result, NewCommandReservation):
                    with lock:
                        executions += 1
                    return service.complete(result, 202, {"result": "synthetic"})
                return result
        except IntakeError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(worker, hashes))
    return results, executions


def test_concurrent_identical_has_one_execution_and_one_replay(engine: Engine) -> None:
    results, executions = concurrent_run(engine, [HASH_A, HASH_A])
    assert executions == 1
    assert all(isinstance(result, CompletedCommandReplay) for result in results)
    assert len({result.command_id for result in results}) == 1
    with engine.connect() as connection:
        rows = (
            connection.execute(select(Base.metadata.tables["command_idempotency_records"]))
            .mappings()
            .all()
        )
    assert len(rows) == 1 and rows[0]["status"] == "Completed"


def test_concurrent_conflict_has_one_execution_and_safe_409(engine: Engine) -> None:
    results, executions = concurrent_run(engine, [HASH_A, HASH_B])
    assert executions == 1
    assert sorted(
        result.status_code if isinstance(result, IntakeError) else result.logical_http_status
        for result in results
    ) == [202, 409]


def test_winner_rollback_allows_later_new_reservation(engine: Engine) -> None:
    factory = create_session_factory(engine)
    selected = scope()
    with pytest.raises(RuntimeError):
        with factory() as session, session.begin():
            result = CommandIdempotencyService(session).reserve(
                selected, "rollback-winner", HASH_A, uuid.uuid4()
            )
            assert isinstance(result, NewCommandReservation)
            raise RuntimeError("winner rolled back")
    with factory() as session, session.begin():
        retry = CommandIdempotencyService(session).reserve(
            selected, "rollback-winner", HASH_A, uuid.uuid4()
        )
        assert isinstance(retry, NewCommandReservation)


def test_secret_completion_replay_is_safe_and_nonmutating(engine: Engine) -> None:
    credential_id, expires_at = seed_callback_credential(engine)
    factory = create_session_factory(engine)
    selected = scope()
    with factory() as session, session.begin():
        service = CommandIdempotencyService(session)
        reservation = service.reserve(selected, "secret-bearing", HASH_A, uuid.uuid4())
        first = service.complete(
            reservation,
            201,
            {"callback_credential_id": str(credential_id), "callback_credential_version": 1},
            SecretDeliveryMetadata(
                callback_credential_id=credential_id,
                callback_credential_version=1,
                callback_credential_expires_at=expires_at,
            ),
        )
        assert first.credential_delivery is None
    with engine.connect() as connection:
        credential_before = (
            connection.execute(
                select(Base.metadata.tables["attempt_callback_credentials"]).where(
                    Base.metadata.tables["attempt_callback_credentials"].c.id == credential_id
                )
            )
            .mappings()
            .one()
        )
    with factory() as session, session.begin():
        replay = CommandIdempotencyService(session).reserve(
            selected, "secret-bearing", HASH_A, uuid.uuid4()
        )
    with engine.connect() as connection:
        credential_after = (
            connection.execute(
                select(Base.metadata.tables["attempt_callback_credentials"]).where(
                    Base.metadata.tables["attempt_callback_credentials"].c.id == credential_id
                )
            )
            .mappings()
            .one()
        )
        record = (
            connection.execute(select(Base.metadata.tables["command_idempotency_records"]))
            .mappings()
            .one()
        )
    assert replay.credential_delivery == "AlreadyIssued"
    assert "plaintext" not in str(replay).lower()
    assert credential_after == credential_before
    assert "plaintext" not in str(record["safe_response_snapshot"]).lower()
