import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text

from ai_operations_automation.app import create_app
from ai_operations_automation.config import Settings
from ai_operations_automation.db import (
    Base,
    create_database_engine,
    create_session_factory,
    models,  # noqa: F401
)
from ai_operations_automation.intake.service import IntakeService
from alembic import command

pytestmark = pytest.mark.integration
BACKEND_ROOT = Path(__file__).resolve().parents[2]


def alembic_config() -> Config:
    return Config(str(BACKEND_ROOT / "alembic.ini"))


@pytest.fixture(scope="module")
def engine() -> Engine:
    command.upgrade(alembic_config(), "head")
    database_engine = create_database_engine(Settings(_env_file=None).database_url)
    yield database_engine
    database_engine.dispose()


@pytest.fixture
def client(engine: Engine) -> TestClient:
    tables = ", ".join(f'"{name}"' for name in Base.metadata.tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} CASCADE"))
    app = create_app(Settings(_env_file=None), create_session_factory(engine))
    return TestClient(app)


def valid_payload(description: str = "The air-conditioning unit is leaking.") -> dict:
    return {
        "schema_version": "1.0",
        "contact": {
            "display_name": "Jane Doe",
            "email": "jane@example.com",
            "phone": "+639171234567",
            "preferred_channel": "Email",
        },
        "service_request": {
            "description": description,
            "location_context": "Second-floor office",
            "timing_preference": "Weekday morning",
        },
    }


def post(client: TestClient, key: str, payload: dict | str, **headers):
    request_headers = {"Idempotency-Key": key, **headers}
    if isinstance(payload, str):
        return client.post(
            "/api/v1/intake/service-requests",
            content=payload,
            headers={"Content-Type": "application/json", **request_headers},
        )
    return client.post("/api/v1/intake/service-requests", json=payload, headers=request_headers)


def counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            name: connection.scalar(select(func.count()).select_from(table))
            for name, table in Base.metadata.tables.items()
        }


def test_new_acceptance_creates_complete_graph_audit_and_pii_free_outbox(client, engine) -> None:
    response = post(client, "new-key-0001", valid_payload())
    assert response.status_code == 201
    assert response.json()["result"]["intake_outcome"] == "New"
    assert response.json()["result"]["service_request_status"] == "TriagePending"
    assert response.headers["location"].startswith("/api/v1/service-requests/")
    assert response.headers["x-correlation-id"] == response.json()["correlation_id"]
    assert counts(engine) == {
        "inbound_deliveries": 1,
        "contacts": 1,
        "service_requests": 1,
        "accepted_intake_keys": 1,
        "audit_events": 2,
        "outbox_messages": 2,
    }
    with engine.connect() as connection:
        event_names = set(
            connection.scalars(select(Base.metadata.tables["audit_events"].c.event_name))
        )
        payloads = list(
            connection.scalars(select(Base.metadata.tables["outbox_messages"].c.payload))
        )
    assert event_names == {"inbound_delivery.accepted", "service_request.created"}
    serialized = json.dumps(payloads)
    assert all(
        value not in serialized
        for value in ["Jane Doe", "jane@example.com", "+639171234567", "leaking"]
    )


def test_exact_replay_creates_only_physical_delivery_and_one_event(client, engine) -> None:
    first = post(client, "replay-key-001", valid_payload())
    second = post(client, "replay-key-001", valid_payload())
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["result"]["intake_outcome"] == "IdempotentReplay"
    assert second.json()["result"]["original_delivery_id"] == first.json()["result"]["delivery_id"]
    assert counts(engine) == {
        "inbound_deliveries": 2,
        "contacts": 1,
        "service_requests": 1,
        "accepted_intake_keys": 1,
        "audit_events": 3,
        "outbox_messages": 3,
    }
    with engine.connect() as connection:
        created = connection.scalar(
            select(func.count())
            .select_from(Base.metadata.tables["audit_events"])
            .where(Base.metadata.tables["audit_events"].c.event_name == "service_request.created")
        )
    assert created == 1


def test_changed_payload_conflicts_and_preserves_original_graph(client, engine) -> None:
    first = post(client, "conflict-key-01", valid_payload())
    second = post(
        client, "conflict-key-01", valid_payload("A materially changed service description.")
    )
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    result = counts(engine)
    assert result["contacts"] == result["service_requests"] == result["accepted_intake_keys"] == 1
    assert result["inbound_deliveries"] == 2


def test_invalid_input_persists_only_rejection_and_allows_corrected_reuse(client, engine) -> None:
    invalid = valid_payload("tiny")
    response = post(client, "correctable-key", invalid)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INTAKE_VALIDATION_FAILED"
    assert counts(engine)["accepted_intake_keys"] == 0
    assert counts(engine)["inbound_deliveries"] == 1
    assert "tiny" not in response.text
    corrected = post(client, "correctable-key", valid_payload())
    assert corrected.status_code == 201


def test_malformed_unreserved_is_400_but_accepted_key_is_conflict(client, engine) -> None:
    malformed = post(client, "malformed-key-1", '{"schema_version":')
    assert malformed.status_code == 400
    assert malformed.json()["error"]["code"] == "MALFORMED_JSON"
    assert counts(engine)["accepted_intake_keys"] == 0
    post(client, "accepted-malformed", valid_payload())
    conflict = post(client, "accepted-malformed", '{"schema_version":')
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_invalid_body_after_accepted_reservation_is_conflict(client) -> None:
    assert post(client, "accepted-invalid-1", valid_payload()).status_code == 201
    response = post(client, "accepted-invalid-1", valid_payload("tiny"))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_supplied_correlation_id_is_echoed(client) -> None:
    correlation_id = uuid.uuid4()
    response = post(
        client,
        "correlation-key-1",
        valid_payload(),
        **{"X-Correlation-ID": str(correlation_id)},
    )
    assert response.status_code == 201
    assert response.headers["x-correlation-id"] == str(correlation_id)
    assert response.json()["correlation_id"] == str(correlation_id)


@pytest.mark.parametrize(
    ("headers", "content_type", "expected_code"),
    [
        ({}, "application/json", "MISSING_IDEMPOTENCY_KEY"),
        (
            {"Idempotency-Key": "transport-key", "X-Correlation-ID": "bad"},
            "application/json",
            "INVALID_TRANSPORT_IDENTIFIER",
        ),
        ({"Idempotency-Key": "transport-key"}, "text/plain", "UNSUPPORTED_MEDIA_TYPE"),
    ],
)
def test_transport_rejections_create_no_rows(
    client, engine, headers, content_type, expected_code
) -> None:
    response = client.post(
        "/api/v1/intake/service-requests",
        content=json.dumps(valid_payload()),
        headers={"Content-Type": content_type, **headers},
    )
    assert response.json()["error"]["code"] == expected_code
    assert sum(counts(engine).values()) == 0


def test_same_key_same_payload_concurrency_is_new_and_replay(client, engine) -> None:
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(lambda _: post(client, "concurrent-same", valid_payload()), range(2))
        )
    assert sorted(response.status_code for response in responses) == [200, 201]
    assert counts(engine)["service_requests"] == 1
    assert counts(engine)["inbound_deliveries"] == 2


def test_same_key_different_payload_concurrency_is_new_and_conflict(client, engine) -> None:
    payloads = [valid_payload(), valid_payload("A different concurrent service request.")]
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda body: post(client, "concurrent-diff", body), payloads))
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert counts(engine)["service_requests"] == 1
    assert counts(engine)["inbound_deliveries"] == 2


def test_forced_service_failure_rolls_back_partial_graph(client, engine, monkeypatch) -> None:
    def fail_event(*args, **kwargs):
        raise RuntimeError("forced safe rollback")

    monkeypatch.setattr(IntakeService, "_add_event", fail_event)
    response = post(client, "rollback-endpoint", valid_payload())
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert sum(counts(engine).values()) == 0


def test_raw_key_is_absent_from_persisted_rows(client, engine) -> None:
    raw_key = "never-persist-this-key"
    assert post(client, raw_key, valid_payload()).status_code == 201
    with engine.connect() as connection:
        for table in Base.metadata.tables.values():
            for column in table.c:
                if hasattr(column.type, "python_type") and column.type.python_type is str:
                    values = connection.scalars(select(column).where(column == raw_key)).all()
                    assert values == []


def test_second_migration_is_applied_and_deferrable(engine) -> None:
    with engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        deferred = connection.scalar(
            text(
                "SELECT count(*) FROM pg_constraint WHERE conname IN "
                "('fk_accepted_key_original_delivery', 'fk_accepted_key_request') "
                "AND condeferrable AND condeferred"
            )
        )
    assert revision == "0002_atomic_intake_constraints"
    assert deferred == 2
