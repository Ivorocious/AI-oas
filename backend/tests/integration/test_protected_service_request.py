import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, insert, select, text
from sqlalchemy.exc import OperationalError

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


def grant(
    engine: Engine,
    subject: str,
    role: str = "OperationsAgent",
    status: str = "Active",
    *,
    assign: bool = True,
):
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
        if assign:
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
    return actor_id


def add_assignment(engine: Engine, actor_id, role="OperationsAgent", **values):
    assignment = Base.metadata.tables["application_actor_role_assignments"]
    defaults = {
        "id": uuid.uuid4(),
        "actor_id": actor_id,
        "role": role,
        "assigned_by_actor_id": actor_id,
        "effective_from": datetime.now(UTC),
        "assignment_reason": "integration test",
    }
    defaults.update(values)
    with engine.begin() as connection:
        connection.execute(insert(assignment).values(**defaults))


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
    correlation = str(uuid.uuid4())
    response = client.get(
        f"/api/v1/service-requests/{uuid.uuid4()}",
        headers={"Authorization": "Bearer invalid", "X-Correlation-ID": correlation},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert response.json()["error"]["correlation_id"] == correlation
    assert response.headers["x-correlation-id"] == correlation


@pytest.mark.parametrize("subject", ["unknown", "disabled"])
def test_unmapped_or_disabled_actor_is_403(client, engine, subject) -> None:
    if subject == "disabled":
        grant(engine, subject, status="Disabled")
    correlation = str(uuid.uuid4())
    response = client.get(
        f"/api/v1/service-requests/{uuid.uuid4()}",
        headers={
            "Authorization": f"Bearer {subject}",
            "X-Correlation-ID": correlation,
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["correlation_id"] == correlation


@pytest.mark.parametrize("role", ["OperationsAgent", "ManagerApprover", "Administrator"])
def test_each_fixed_role_can_read_without_mutating_evidence(client, engine, role) -> None:
    created = intake(client)
    request_id = uuid.UUID(created.json()["result"]["service_request_id"])
    grant(engine, role, role)
    audit = Base.metadata.tables["audit_events"]
    outbox = Base.metadata.tables["outbox_messages"]
    service = Base.metadata.tables["service_requests"]
    contact = Base.metadata.tables["contacts"]
    with engine.connect() as connection:
        before = (
            len(connection.execute(select(audit.c.id)).all()),
            len(connection.execute(select(outbox.c.id)).all()),
            connection.scalar(select(service.c.version).where(service.c.id == request_id)),
            connection.scalar(
                select(contact.c.version)
                .join(service, service.c.contact_id == contact.c.id)
                .where(service.c.id == request_id)
            ),
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
            connection.scalar(
                select(contact.c.version)
                .join(service, service.c.contact_id == contact.c.id)
                .where(service.c.id == request_id)
            ),
        )
    assert after == before


def test_authenticated_missing_and_malformed_ids_are_safe_404(client, engine) -> None:
    grant(engine, "reader")
    correlation = str(uuid.uuid4())
    headers = {"Authorization": "Bearer reader", "X-Correlation-ID": correlation}
    missing = client.get(f"/api/v1/service-requests/{uuid.uuid4()}", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["error"]["correlation_id"] == correlation
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


def test_no_current_assignment_fails_closed(client, engine) -> None:
    grant(engine, "no-role", assign=False)
    response = client.get(
        f"/api/v1/service-requests/{uuid.uuid4()}",
        headers={"Authorization": "Bearer no-role"},
    )
    assert response.status_code == 403


def test_overlapping_finite_current_assignments_fail_closed(client, engine) -> None:
    actor_id = grant(engine, "overlap", assign=False)
    now = datetime.now(UTC)
    add_assignment(
        engine,
        actor_id,
        effective_from=now - timedelta(hours=2),
        effective_to=now + timedelta(hours=2),
        revoked_by_actor_id=actor_id,
    )
    add_assignment(
        engine,
        actor_id,
        role="Administrator",
        effective_from=now - timedelta(hours=1),
        effective_to=now + timedelta(hours=1),
        revoked_by_actor_id=actor_id,
    )
    response = client.get(
        f"/api/v1/service-requests/{uuid.uuid4()}",
        headers={"Authorization": "Bearer overlap"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("other", ["expired", "future"])
def test_only_current_assignment_is_selected(client, engine, other) -> None:
    actor_id = grant(engine, f"single-{other}", assign=False)
    now = datetime.now(UTC)
    if other == "expired":
        start, end = now - timedelta(days=2), now - timedelta(days=1)
    else:
        start, end = now + timedelta(days=1), now + timedelta(days=2)
    add_assignment(
        engine,
        actor_id,
        effective_from=start,
        effective_to=end,
        revoked_by_actor_id=actor_id,
    )
    add_assignment(engine, actor_id)
    response = client.get(
        f"/api/v1/service-requests/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer single-{other}"},
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "table_name,values",
    [
        ("application_actors", {"version": 0}),
        ("application_actor_role_assignments", {"role": "UnknownRole"}),
        (
            "application_actor_role_assignments",
            {"effective_to": datetime.now(UTC) - timedelta(days=1)},
        ),
        (
            "application_actor_role_assignments",
            {"effective_to": datetime.now(UTC) + timedelta(days=1)},
        ),
        (
            "application_actor_role_assignments",
            {"revoked_by_actor_id": "self"},
        ),
    ],
)
def test_human_access_constraints_reject_invalid_rows(engine, table_name, values) -> None:
    actor = Base.metadata.tables["application_actors"]
    assignment = Base.metadata.tables["application_actor_role_assignments"]
    actor_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            insert(actor).values(
                id=actor_id,
                supabase_subject=str(uuid.uuid4()),
                display_label="constraint actor",
            )
        )
    if table_name == "application_actors":
        statement = insert(actor).values(
            id=uuid.uuid4(),
            supabase_subject=str(uuid.uuid4()),
            display_label="invalid version",
            **values,
        )
    else:
        defaults = {
            "id": uuid.uuid4(),
            "actor_id": actor_id,
            "role": "OperationsAgent",
            "assigned_by_actor_id": actor_id,
            "effective_from": datetime.now(UTC),
            "assignment_reason": "constraint test",
        }
        if values.get("revoked_by_actor_id") == "self":
            values = {"revoked_by_actor_id": actor_id}
        defaults.update(values)
        statement = insert(assignment).values(**defaults)
    with pytest.raises(Exception):
        with engine.begin() as connection:
            connection.execute(statement)


def test_projection_values_timestamps_and_correlation(client, engine) -> None:
    created = intake(client)
    grant(engine, "projection-reader")
    correlation = str(uuid.uuid4())
    response = client.get(
        created.headers["location"],
        headers={
            "Authorization": "Bearer projection-reader",
            "X-Correlation-ID": correlation,
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert body["correlation_id"] == response.headers["x-correlation-id"] == correlation
    assert body["result"]["service_request"]["description"] == (
        "The office air conditioner is leaking."
    )
    assert body["result"]["contact"] == {
        **body["result"]["contact"],
        "display_name": "Jane Doe",
        "email": "jane@example.com",
        "phone": None,
        "preferred_channel": None,
        "version": 1,
    }
    for name in ("created_at", "updated_at"):
        assert body["result"]["service_request"][name].endswith(("Z", "+00:00"))


def test_database_unavailability_and_unexpected_failure_are_safe(client, monkeypatch) -> None:
    import ai_operations_automation.api.service_requests as route_module

    correlation = str(uuid.uuid4())
    headers = {"Authorization": "Bearer reader", "X-Correlation-ID": correlation}
    grant_factory = client.app.state.session_factory

    def unavailable():
        raise OperationalError("safe statement", {}, Exception("private database detail"))

    client.app.state.session_factory = unavailable
    unavailable_response = client.get(f"/api/v1/service-requests/{uuid.uuid4()}", headers=headers)
    assert unavailable_response.status_code == 503
    client.app.state.session_factory = grant_factory

    # Establish a valid actor before forcing only the query projection to fail.
    grant(grant_factory.kw["bind"], "reader")

    def unexpected(*_args):
        raise RuntimeError("private exception text")

    monkeypatch.setattr(route_module, "query_service_request", unexpected)
    failure = client.get(f"/api/v1/service-requests/{uuid.uuid4()}", headers=headers)
    assert failure.status_code == 500
    assert failure.json()["error"]["code"] == "INTERNAL_ERROR"
    assert failure.json()["error"]["correlation_id"] == correlation
    combined = unavailable_response.text + failure.text
    for forbidden in (
        "Bearer reader",
        "reader",
        "OperationsAgent",
        "SELECT",
        "application_actors",
        "Traceback",
        "private",
    ):
        assert forbidden not in combined
