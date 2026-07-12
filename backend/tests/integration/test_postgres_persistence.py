import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from sqlalchemy import Connection, Engine, delete, insert, inspect, select, text, update
from sqlalchemy.exc import IntegrityError

from ai_operations_automation.config import Settings
from ai_operations_automation.db import (
    Base,
    create_database_engine,
    models,  # noqa: F401
)
from alembic import command

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_APPLICATION_TABLES = {
    "accepted_intake_keys",
    "audit_events",
    "contacts",
    "inbound_deliveries",
    "outbox_messages",
    "service_requests",
}


def alembic_config() -> Config:
    return Config(str(BACKEND_ROOT / "alembic.ini"))


@pytest.fixture(scope="session")
def migrated_engine() -> Iterator[Engine]:
    command.upgrade(alembic_config(), "head")
    engine = create_database_engine(Settings(_env_file=None).database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def connection(migrated_engine: Engine) -> Iterator[Connection]:
    with migrated_engine.connect() as database_connection:
        transaction = database_connection.begin()
        yield database_connection
        if transaction.is_active:
            transaction.rollback()


def _insert_accepted_graph(
    connection: Connection,
    *,
    scope: str | None = None,
    digest: str | None = None,
) -> dict[str, uuid.UUID]:
    ids = {
        "contact": uuid.uuid4(),
        "delivery": uuid.uuid4(),
        "request": uuid.uuid4(),
        "accepted_key": uuid.uuid4(),
        "audit": uuid.uuid4(),
        "outbox": uuid.uuid4(),
        "correlation": uuid.uuid4(),
    }
    selected_scope = scope or f"public-intake-{uuid.uuid4()}"
    selected_digest = digest or uuid.uuid4().hex
    tables = Base.metadata.tables

    connection.execute(
        insert(tables["contacts"]).values(
            id=ids["contact"],
            display_label="Local integration contact",
            normalized_email="local@example.test",
            version=1,
        )
    )
    connection.execute(
        insert(tables["inbound_deliveries"]).values(
            id=ids["delivery"],
            scope=selected_scope,
            idempotency_key_digest=selected_digest,
            processing_status="Received",
            schema_version="1.0.0",
            version=1,
            correlation_id=ids["correlation"],
            canonical_payload_hash=uuid.uuid4().hex,
        )
    )
    connection.execute(
        insert(tables["service_requests"]).values(
            id=ids["request"],
            originating_delivery_id=ids["delivery"],
            contact_id=ids["contact"],
            normalized_request_description="Inspect a local integration fixture.",
            status="TriagePending",
            version=1,
        )
    )
    connection.execute(
        insert(tables["accepted_intake_keys"]).values(
            id=ids["accepted_key"],
            scope=selected_scope,
            idempotency_key_digest=selected_digest,
            canonical_payload_hash=uuid.uuid4().hex,
            original_delivery_id=ids["delivery"],
            request_id=ids["request"],
            original_http_status=201,
            safe_response_snapshot={"request_id": str(ids["request"])},
        )
    )
    connection.execute(
        update(tables["inbound_deliveries"])
        .where(tables["inbound_deliveries"].c.id == ids["delivery"])
        .values(
            processing_status="Accepted",
            intake_outcome="New",
            created_request_id=ids["request"],
            logical_result_request_id=ids["request"],
            accepted_intake_key_id=ids["accepted_key"],
            completed_at=text("now()"),
        )
    )
    connection.execute(
        insert(tables["audit_events"]).values(
            id=ids["audit"],
            schema_version="1.0.0",
            event_name="service_request.created",
            aggregate_type="ServiceRequest",
            aggregate_id=ids["request"],
            aggregate_version=1,
            actor_type="Customer",
            actor_reference_id=ids["delivery"],
            outcome="Accepted",
            correlation_id=ids["correlation"],
            reason_codes=[],
            safe_metadata={"source": "integration-test"},
        )
    )
    connection.execute(
        insert(tables["outbox_messages"]).values(
            id=ids["outbox"],
            event_type="service_request.created",
            schema_version="1.0.0",
            aggregate_type="ServiceRequest",
            aggregate_id=ids["request"],
            aggregate_version=1,
            audit_event_id=ids["audit"],
            correlation_id=ids["correlation"],
            payload={"request_id": str(ids["request"])},
            publication_state="Pending",
        )
    )
    return ids


def test_migration_from_base_to_head_succeeds(migrated_engine: Engine) -> None:
    migrated_engine.dispose()
    command.downgrade(alembic_config(), "base")
    command.upgrade(alembic_config(), "head")

    assert set(inspect(migrated_engine).get_table_names()) == EXPECTED_APPLICATION_TABLES | {
        "alembic_version"
    }


def test_head_contains_only_expected_tables(migrated_engine: Engine) -> None:
    assert set(inspect(migrated_engine).get_table_names()) == EXPECTED_APPLICATION_TABLES | {
        "alembic_version"
    }


def test_downgrade_to_base_succeeds(migrated_engine: Engine) -> None:
    migrated_engine.dispose()
    command.downgrade(alembic_config(), "base")
    try:
        assert set(inspect(migrated_engine).get_table_names()) == {"alembic_version"}
    finally:
        command.upgrade(alembic_config(), "head")


def test_upgrade_after_downgrade_succeeds(migrated_engine: Engine) -> None:
    migrated_engine.dispose()
    command.downgrade(alembic_config(), "base")
    command.upgrade(alembic_config(), "head")

    with migrated_engine.connect() as database_connection:
        revision = database_connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert revision == "0002_atomic_intake_constraints"


def test_duplicate_accepted_reservation_is_rejected(connection: Connection) -> None:
    scope = f"duplicate-scope-{uuid.uuid4()}"
    digest = uuid.uuid4().hex
    _insert_accepted_graph(connection, scope=scope, digest=digest)

    with pytest.raises(IntegrityError):
        _insert_accepted_graph(connection, scope=scope, digest=digest)


def test_originating_delivery_cannot_create_two_requests(connection: Connection) -> None:
    tables = Base.metadata.tables
    contact_id = uuid.uuid4()
    delivery_id = uuid.uuid4()
    connection.execute(
        insert(tables["contacts"]).values(id=contact_id, display_label="Unique origin", version=1)
    )
    connection.execute(
        insert(tables["inbound_deliveries"]).values(
            id=delivery_id,
            scope="unique-origin",
            idempotency_key_digest=uuid.uuid4().hex,
            processing_status="Received",
            schema_version="1.0.0",
            version=1,
            correlation_id=uuid.uuid4(),
        )
    )
    request_values: dict[str, Any] = {
        "originating_delivery_id": delivery_id,
        "contact_id": contact_id,
        "normalized_request_description": "Unique originating delivery.",
        "status": "TriagePending",
        "version": 1,
    }
    connection.execute(insert(tables["service_requests"]).values(id=uuid.uuid4(), **request_values))

    with pytest.raises(IntegrityError):
        connection.execute(
            insert(tables["service_requests"]).values(id=uuid.uuid4(), **request_values)
        )


def test_minimal_accepted_intake_graph_inserts_atomically(connection: Connection) -> None:
    ids = _insert_accepted_graph(connection)

    for table_name, id_key in (
        ("contacts", "contact"),
        ("inbound_deliveries", "delivery"),
        ("service_requests", "request"),
        ("accepted_intake_keys", "accepted_key"),
        ("audit_events", "audit"),
        ("outbox_messages", "outbox"),
    ):
        table = Base.metadata.tables[table_name]
        assert connection.scalar(select(table.c.id).where(table.c.id == ids[id_key])) == ids[id_key]


def test_forced_failure_rolls_back_whole_transaction(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as database_connection:
        transaction = database_connection.begin()
        first = _insert_accepted_graph(database_connection, scope="rollback-scope")
        with pytest.raises(IntegrityError):
            _insert_accepted_graph(database_connection, scope="rollback-scope", digest="same")
            _insert_accepted_graph(database_connection, scope="rollback-scope", digest="same")
        transaction.rollback()

    contacts = Base.metadata.tables["contacts"]
    with migrated_engine.connect() as database_connection:
        assert (
            database_connection.scalar(
                select(contacts.c.id).where(contacts.c.id == first["contact"])
            )
            is None
        )


def test_postgres_timestamps_are_timezone_aware(connection: Connection) -> None:
    ids = _insert_accepted_graph(connection)
    deliveries = Base.metadata.tables["inbound_deliveries"]

    received_at = connection.scalar(
        select(deliveries.c.received_at).where(deliveries.c.id == ids["delivery"])
    )

    assert received_at is not None
    assert received_at.tzinfo is not None
    assert received_at.utcoffset() is not None


def test_audit_delete_does_not_cascade_to_outbox(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as database_connection:
        ids = _insert_accepted_graph(database_connection)

    audit_events = Base.metadata.tables["audit_events"]
    outbox_messages = Base.metadata.tables["outbox_messages"]
    with migrated_engine.connect() as database_connection:
        transaction = database_connection.begin()
        with pytest.raises(IntegrityError):
            database_connection.execute(
                delete(audit_events).where(audit_events.c.id == ids["audit"])
            )
        transaction.rollback()

    with migrated_engine.connect() as database_connection:
        assert (
            database_connection.scalar(
                select(audit_events.c.id).where(audit_events.c.id == ids["audit"])
            )
            == ids["audit"]
        )
        assert (
            database_connection.scalar(
                select(outbox_messages.c.id).where(outbox_messages.c.id == ids["outbox"])
            )
            == ids["outbox"]
        )
