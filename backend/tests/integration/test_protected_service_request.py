import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, insert, select, text

from ai_operations_automation.app import create_app
from ai_operations_automation.auth.verifier import AuthenticationFailure
from ai_operations_automation.config import Settings
from ai_operations_automation.db import Base, create_database_engine, create_session_factory
from alembic import command

pytestmark = pytest.mark.integration
BACKEND_ROOT = Path(__file__).resolve().parents[2]


class TokenVerifier:
    def verify(self, token: str) -> str:
        if token == "invalid":
            raise AuthenticationFailure
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
    app = create_app(
        Settings(_env_file=None),
        create_session_factory(engine),
        jwt_verifier=TokenVerifier(),
    )
    return TestClient(app)


def intake(client: TestClient):
    return client.post(
        "/api/v1/intake/service-requests",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={
            "schema_version": "1.0",
            "contact": {"display_name": "Jane Doe", "email": "jane@example.com"},
            "service_request": {"description": "The office air conditioner is leaking."},
        },
    )


def grant(engine: Engine, subject: str, role: str = "OperationsAgent", status: str = "Active"):
    actor = Base.metadata.tables["application_actors"]
    assignment = Base.metadata.tables["application_actor_role_assignments"]
    actor_id = uuid.uuid4()
    values = {
        "id": actor_id,
        "supabase_subject": subject,
        "display_label": subject,
        "status": status,
    }
    if status == "Disabled":
        values.update(disabled_at=datetime.now(UTC), disable_reason="test disable")
    with engine.begin() as connection:
        connection.execute(insert(actor).values(**values))
        connection.execute(
            insert(assignment).values(
                id=uuid.uuid4(),
                actor_id=actor_id,
                role=role,
                assigned_by_actor_id=actor_id,
                effective_from=datetime.now(UTC),
                assignment_reason="integration test",
            )
        )


def test_public_intake_remains_unauthenticated_and_location_is_protected(client, engine) -> None:
    response = intake(client)
    assert response.status_code == 201
    location = response.headers["location"]
    missing = client.get(location)
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    grant(engine, "reader")
    detail = client.get(location, headers={"Authorization": "Bearer reader"})
    assert detail.status_code == 200
    assert detail.json()["result"]["service_request"]["status"] == "TriagePending"
    assert detail.json()["result"]["active_references"] == {
        "current_interpretation_id": None,
        "current_routing_decision_id": None,
        "active_proposed_action_id": None,
    }


def test_invalid_token_is_401(client) -> None:
    response = client.get(
        f"/api/v1/service-requests/{uuid.uuid4()}",
        headers={"Authorization": "Bearer invalid"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


@pytest.mark.parametrize("subject", ["unknown", "disabled"])
def test_unmapped_or_disabled_actor_is_403(client, engine, subject) -> None:
    if subject == "disabled":
        grant(engine, subject, status="Disabled")
    response = client.get(
        f"/api/v1/service-requests/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {subject}"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("role", ["OperationsAgent", "ManagerApprover", "Administrator"])
def test_each_fixed_role_can_read_without_mutating_evidence(client, engine, role) -> None:
    created = intake(client)
    request_id = uuid.UUID(created.json()["result"]["service_request_id"])
    grant(engine, role, role)
    audit = Base.metadata.tables["audit_events"]
    outbox = Base.metadata.tables["outbox_messages"]
    service = Base.metadata.tables["service_requests"]
    with engine.connect() as connection:
        before = (
            len(connection.execute(select(audit.c.id)).all()),
            len(connection.execute(select(outbox.c.id)).all()),
            connection.scalar(select(service.c.version).where(service.c.id == request_id)),
        )
    response = client.get(
        f"/api/v1/service-requests/{request_id}",
        headers={"Authorization": f"Bearer {role}"},
    )
    assert response.status_code == 200
    with engine.connect() as connection:
        after = (
            len(connection.execute(select(audit.c.id)).all()),
            len(connection.execute(select(outbox.c.id)).all()),
            connection.scalar(select(service.c.version).where(service.c.id == request_id)),
        )
    assert after == before


def test_authenticated_missing_and_malformed_ids_are_safe_404(client, engine) -> None:
    grant(engine, "reader")
    headers = {"Authorization": "Bearer reader"}
    assert (
        client.get(f"/api/v1/service-requests/{uuid.uuid4()}", headers=headers).status_code == 404
    )
    assert client.get("/api/v1/service-requests/not-a-uuid", headers=headers).status_code == 404


def test_identity_constraints_reject_duplicate_subject_and_open_role(engine) -> None:
    grant(engine, "unique-subject")
    actor = Base.metadata.tables["application_actors"]
    assignment = Base.metadata.tables["application_actor_role_assignments"]
    with pytest.raises(Exception):
        with engine.begin() as connection:
            connection.execute(
                insert(actor).values(
                    id=uuid.uuid4(),
                    supabase_subject="unique-subject",
                    display_label="duplicate",
                )
            )
    with engine.begin() as connection:
        actor_id = connection.scalar(
            select(actor.c.id).where(actor.c.supabase_subject == "unique-subject")
        )
    with pytest.raises(Exception):
        with engine.begin() as connection:
            connection.execute(
                insert(assignment).values(
                    id=uuid.uuid4(),
                    actor_id=actor_id,
                    role="Administrator",
                    assigned_by_actor_id=actor_id,
                    effective_from=datetime.now(UTC),
                    assignment_reason="duplicate open role",
                )
            )
