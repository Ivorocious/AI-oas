import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, insert, literal, select, text, update
from sqlalchemy.exc import OperationalError

from ai_operations_automation.app import create_app
from ai_operations_automation.auth.verifier import AuthenticationFailure
from ai_operations_automation.config import Settings
from ai_operations_automation.db import Base, create_database_engine, create_session_factory
from ai_operations_automation.machine_auth.authenticator import calculate_signature
from ai_operations_automation.machine_auth.canonicalization import canonical_signing_bytes
from ai_operations_automation.machine_auth.secrets import MachineSecretUnavailable
from ai_operations_automation.protected_queries import BackendProtectedQueryService
from alembic import command

pytestmark = pytest.mark.integration
BACKEND_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime.now(UTC)
MACHINE_SECRET = b"synthetic-protected-query-machine-key"


class Resolver:
    def resolve(self, reference: str) -> bytes:
        if not reference.startswith("test/protected-query"):
            raise MachineSecretUnavailable
        return MACHINE_SECRET


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
    tables = ", ".join(
        f'"{name}"'
        for name in Base.metadata.tables
        if name not in {"failure_recovery_policy_versions", "decision_policy_versions"}
    )
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} CASCADE"))
    app = create_app(
        Settings(
            app_environment="test",
            _env_file=None,
            protected_query_cursor_signing_key="synthetic-query-cursor-key-for-tests-only-0001",
        ),
        create_session_factory(engine),
        jwt_verifier=TokenVerifier(),
        machine_secret_resolver=Resolver(),
        machine_clock=lambda: NOW,
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


def seed_workflow(
    engine: Engine,
    *,
    service_id: str = "workflow.protected",
    service_type=None,
    secret_reference: str = "test/protected-query",
    status: str = "Active",
):
    identity_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    with engine.begin() as connection:
        identity_values = {
            "id": identity_id,
            "service_type": service_type or "WorkflowService",
            "environment": "test",
            "stable_service_id": service_id,
            "display_label": f"Synthetic {service_id}",
            "status": status,
        }
        if status == "Disabled":
            identity_values.update(
                disabled_at=NOW - timedelta(seconds=1),
                disable_reason="synthetic integration test disablement",
            )
        connection.execute(
            insert(Base.metadata.tables["machine_identities"]).values(**identity_values)
        )
        connection.execute(
            insert(Base.metadata.tables["machine_credential_versions"]).values(
                id=credential_id,
                machine_identity_id=identity_id,
                credential_version=1,
                external_secret_reference=secret_reference,
                status="Current",
                activated_at=NOW - timedelta(minutes=1),
            )
        )


def machine_headers(
    method: str,
    path: str,
    *,
    query: str = "",
    body: bytes = b"",
    nonce: str | None = None,
    service_id: str = "workflow.protected",
    timestamp: datetime = NOW,
) -> dict[str, str]:
    nonce = nonce or f"nonce-{uuid.uuid4()}"
    stamp = str(int(timestamp.timestamp()))
    canonical = canonical_signing_bytes(
        method,
        path.encode(),
        query.encode(),
        stamp,
        nonce,
        body,
    )
    return {
        "X-Service-ID": service_id,
        "X-Service-Timestamp": stamp,
        "X-Service-Nonce": nonce,
        "X-Service-Signature": calculate_signature(MACHINE_SECRET, canonical),
    }


def clone_row(connection, table, source_id: uuid.UUID, **overrides):
    columns = list(table.c)
    expressions = [
        literal(overrides[column.name], type_=column.type).label(column.name)
        if column.name in overrides
        else column
        for column in columns
    ]
    connection.execute(
        insert(table).from_select(
            [column.name for column in columns],
            select(*expressions).where(table.c.id == source_id),
        )
    )


def walk_pages(client: TestClient, path: str, headers: dict[str, str], *, limit: int = 1):
    separator = "&" if "?" in path else "?"
    response = client.get(f"{path}{separator}limit={limit}", headers=headers)
    assert response.status_code == 200
    result = response.json()["result"]
    first_cursor = result["page"]["next_cursor"]
    seen = [item["id"] for item in result["items"]]
    cursor = first_cursor
    while cursor:
        response = client.get(
            f"{path}{separator}limit={limit}&cursor={cursor}",
            headers=headers,
        )
        assert response.status_code == 200
        result = response.json()["result"]
        seen.extend(item["id"] for item in result["items"])
        cursor = result["page"]["next_cursor"]
    assert len(seen) == len(set(seen))
    return seen, first_cursor


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


def test_protected_query_catalog_returns_safe_empty_history_and_conceals_unknown_children(
    client, engine
) -> None:
    created = intake(client)
    request_id = created.json()["result"]["service_request_id"]
    delivery_id = created.json()["result"]["delivery_id"]
    grant(engine, "catalog-reader")
    headers = {"Authorization": "Bearer catalog-reader"}

    request_list = client.get("/api/v1/service-requests?limit=1", headers=headers)
    assert request_list.status_code == 200
    assert request_list.json()["result"]["items"][0]["id"] == request_id
    assert request_list.json()["result"]["page"]["next_cursor"] is None
    assert (
        client.get(f"/api/v1/inbound-deliveries/{delivery_id}", headers=headers).status_code == 200
    )
    for suffix in (
        "timeline",
        "ai-interpretations",
        "duplicate-candidates",
        "routing-decisions",
        "proposed-actions",
    ):
        response = client.get(f"/api/v1/service-requests/{request_id}/{suffix}", headers=headers)
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["page"] == {"next_cursor": None}
        assert len(result["items"]) == (2 if suffix == "timeline" else 0)

    unknown = uuid.uuid4()
    for path in (
        f"/api/v1/proposed-actions/{unknown}",
        f"/api/v1/proposed-actions/{unknown}/approvals",
        f"/api/v1/proposed-actions/{unknown}/integration-attempts",
        f"/api/v1/integration-attempts/{unknown}",
    ):
        response = client.get(path, headers=headers)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    grant(engine, "catalog-manager", "ManagerApprover")
    audit = client.get(
        f"/api/v1/audit-events?aggregate_type=ServiceRequest&aggregate_id={request_id}",
        headers={"Authorization": "Bearer catalog-manager"},
    )
    assert audit.status_code == 200
    assert "idempotency" not in audit.text.lower()
    assert "payload" not in audit.text.lower()


def test_backendservice_uses_explicit_in_process_projection_without_external_authority(
    client, engine
) -> None:
    created = intake(client).json()["result"]
    service = BackendProtectedQueryService(create_session_factory(engine))
    request_id = uuid.UUID(created["service_request_id"])

    detail = service.get_service_request(request_id, uuid.uuid4())
    assert detail.service_request.id == request_id
    assert detail.service_request.description == "The office air conditioner is leaking."
    assert service.list_service_requests(limit=1).items[0].id == request_id
    assert service.get_inbound_delivery(uuid.UUID(created["delivery_id"])).service_request_id == (
        request_id
    )
    assert len(service.get_request_timeline(request_id).items) == 2

    # No external caller can assert BackendService authority through bearer or machine headers.
    external = client.get(
        f"/api/v1/service-requests/{request_id}",
        headers={"Authorization": "BackendService trusted-in-process"},
    )
    assert external.status_code == 401


def test_security_audit_rows_are_administrator_only_in_search_and_timeline(client, engine) -> None:
    created = intake(client).json()["result"]
    request_id = uuid.UUID(created["service_request_id"])
    grant(engine, "security-operations")
    grant(engine, "security-manager", "ManagerApprover")
    grant(engine, "security-administrator", "Administrator")
    event_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            insert(Base.metadata.tables["audit_events"]).values(
                id=event_id,
                schema_version="1.0",
                event_name="integration_attempt.callback_credential_replaced",
                aggregate_type="ServiceRequest",
                aggregate_id=request_id,
                aggregate_version=1,
                actor_type="WorkflowService",
                actor_reference_id=uuid.uuid4(),
                occurred_at=NOW,
                outcome="Succeeded",
                correlation_id=uuid.uuid4(),
                reason_codes=[],
                safe_metadata={"credential_version": 2},
            )
        )

    search = f"/api/v1/audit-events?aggregate_type=ServiceRequest&aggregate_id={request_id}"
    timeline = f"/api/v1/service-requests/{request_id}/timeline"
    manager = client.get(search, headers={"Authorization": "Bearer security-manager"})
    administrator = client.get(search, headers={"Authorization": "Bearer security-administrator"})
    operations_timeline = client.get(
        timeline, headers={"Authorization": "Bearer security-operations"}
    )
    administrator_timeline = client.get(
        timeline, headers={"Authorization": "Bearer security-administrator"}
    )
    assert manager.status_code == administrator.status_code == 200
    assert operations_timeline.status_code == administrator_timeline.status_code == 200
    assert str(event_id) not in manager.text
    assert str(event_id) not in operations_timeline.text
    assert str(event_id) in administrator.text
    assert str(event_id) in administrator_timeline.text


def test_workflowservice_query_permissions_exact_assignment_and_principal_composition(
    client, engine
) -> None:
    created = intake(client).json()["result"]
    request_id = created["service_request_id"]
    delivery_id = created["delivery_id"]
    seed_workflow(engine)

    command = {
        "schema_version": "1.0",
        "expected_versions": {"service_request": 1},
        "command": {},
    }
    body = json.dumps(command, separators=(",", ":")).encode()
    start_path = f"/api/v1/service-requests/{request_id}/commands/start-ai-interpretation"
    started = client.post(
        start_path,
        content=body,
        headers={
            **machine_headers("POST", start_path, body=body),
            "Content-Type": "application/json",
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    assert started.status_code == 202
    attempt_id = started.json()["result"]["integration_attempt_id"]

    request_path = f"/api/v1/service-requests/{request_id}"
    assigned = client.get(request_path, headers=machine_headers("GET", request_path))
    assert assigned.status_code == 200
    assert assigned.json()["result"] == {
        **assigned.json()["result"],
        "attempt_id": attempt_id,
        "id": request_id,
        "description": "The office air conditioner is leaking.",
    }
    assert "contact" not in assigned.text.lower()
    assert "current_proposed_action_id" not in assigned.text

    attempt_path = f"/api/v1/integration-attempts/{attempt_id}"
    attempt = client.get(attempt_path, headers=machine_headers("GET", attempt_path))
    assert attempt.status_code == 200
    assert attempt.json()["result"]["id"] == attempt_id
    for forbidden in (
        "credential_hash",
        "assigned_workflow_service",
        "workflow_environment",
        "result_hash",
        "sanitized_evidence_hash",
    ):
        assert forbidden not in attempt.text

    interpretations_path = f"/api/v1/service-requests/{request_id}/ai-interpretations"
    interpretations = client.get(
        interpretations_path,
        headers=machine_headers("GET", interpretations_path),
    )
    assert interpretations.status_code == 200
    assert interpretations.json()["result"] == {
        "items": [],
        "page": {"next_cursor": None},
    }

    for path in (
        "/api/v1/service-requests",
        f"/api/v1/inbound-deliveries/{delivery_id}",
        f"/api/v1/service-requests/{request_id}/timeline",
        f"/api/v1/service-requests/{request_id}/duplicate-candidates",
        f"/api/v1/service-requests/{request_id}/routing-decisions",
        f"/api/v1/service-requests/{request_id}/proposed-actions",
        f"/api/v1/proposed-actions/{uuid.uuid4()}/approvals",
        f"/api/v1/audit-events?aggregate_type=ServiceRequest&aggregate_id={request_id}",
    ):
        path_only, _, query = path.partition("?")
        response = client.get(
            path,
            headers=machine_headers("GET", path_only, query=query),
        )
        assert response.status_code == 403, path

    for path in (
        f"/api/v1/proposed-actions/{uuid.uuid4()}",
        f"/api/v1/proposed-actions/{uuid.uuid4()}/integration-attempts",
        f"/api/v1/integration-attempts/{uuid.uuid4()}",
        f"/api/v1/service-requests/{uuid.uuid4()}",
    ):
        response = client.get(path, headers=machine_headers("GET", path))
        assert response.status_code == 404, path

    seed_workflow(
        engine,
        service_id="workflow.other",
        secret_reference="test/protected-query-other",
    )
    wrong_service = client.get(
        attempt_path,
        headers=machine_headers("GET", attempt_path, service_id="workflow.other"),
    )
    wrong_request = client.get(
        request_path,
        headers=machine_headers("GET", request_path, service_id="workflow.other"),
    )
    assert wrong_service.status_code == wrong_request.status_code == 404

    unknown = client.get(
        attempt_path,
        headers=machine_headers("GET", attempt_path, service_id="workflow.unknown"),
    )
    seed_workflow(
        engine,
        service_id="workflow.disabled",
        secret_reference="test/protected-query-disabled",
        status="Disabled",
    )
    disabled = client.get(
        attempt_path,
        headers=machine_headers("GET", attempt_path, service_id="workflow.disabled"),
    )
    seed_workflow(
        engine,
        service_id="event.publisher",
        service_type="EventPublisher",
        secret_reference="test/protected-query-publisher",
    )
    publisher = client.get(
        attempt_path,
        headers=machine_headers("GET", attempt_path, service_id="event.publisher"),
    )
    seed_workflow(
        engine,
        service_id="backend.internal",
        service_type="BackendService",
        secret_reference="test/protected-query-backend",
    )
    backend_impersonation = client.get(
        attempt_path,
        headers=machine_headers("GET", attempt_path, service_id="backend.internal"),
    )
    assert unknown.status_code == disabled.status_code == 401
    assert publisher.status_code == backend_impersonation.status_code == 401

    replay_nonce = f"nonce-{uuid.uuid4()}"
    replay_headers = machine_headers("GET", attempt_path, nonce=replay_nonce)
    assert client.get(attempt_path, headers=replay_headers).status_code == 200
    assert client.get(attempt_path, headers=replay_headers).status_code == 401
    stale = client.get(
        attempt_path,
        headers=machine_headers(
            "GET",
            attempt_path,
            timestamp=NOW - timedelta(seconds=301),
        ),
    )
    assert stale.status_code == 401
    assert (
        client.get(attempt_path, headers={"X-Service-ID": "workflow.protected"}).status_code == 401
    )

    ambiguous_nonce = f"nonce-{uuid.uuid4()}"
    ambiguous = client.get(
        request_path,
        headers={
            **machine_headers("GET", request_path, nonce=ambiguous_nonce),
            "Authorization": "Bearer conflicting-human",
        },
    )
    assert ambiguous.status_code == 401
    assert ambiguous.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    with engine.connect() as connection:
        nonce_rows = connection.scalar(
            select(text("count(*)")).select_from(Base.metadata.tables["machine_request_nonces"])
        )
    assert nonce_rows == 19


@pytest.mark.parametrize("role", ["OperationsAgent", "ManagerApprover", "Administrator"])
def test_complete_human_query_authorization_matrix(client, engine, role: str) -> None:
    created = intake(client).json()["result"]
    request_id = created["service_request_id"]
    delivery_id = created["delivery_id"]
    subject = f"matrix-{role}"
    grant(engine, subject, role)
    headers = {"Authorization": f"Bearer {subject}"}
    known_paths = (
        f"/api/v1/service-requests/{request_id}",
        "/api/v1/service-requests",
        f"/api/v1/inbound-deliveries/{delivery_id}",
        f"/api/v1/service-requests/{request_id}/timeline",
        f"/api/v1/service-requests/{request_id}/ai-interpretations",
        f"/api/v1/service-requests/{request_id}/duplicate-candidates",
        f"/api/v1/service-requests/{request_id}/routing-decisions",
        f"/api/v1/service-requests/{request_id}/proposed-actions",
    )
    assert all(client.get(path, headers=headers).status_code == 200 for path in known_paths)
    hidden = uuid.uuid4()
    hidden_paths = (
        f"/api/v1/proposed-actions/{hidden}",
        f"/api/v1/proposed-actions/{hidden}/approvals",
        f"/api/v1/proposed-actions/{hidden}/integration-attempts",
        f"/api/v1/integration-attempts/{hidden}",
    )
    assert all(client.get(path, headers=headers).status_code == 404 for path in hidden_paths)
    audit_path = f"/api/v1/audit-events?aggregate_type=ServiceRequest&aggregate_id={request_id}"
    expected = 403 if role == "OperationsAgent" else 200
    assert client.get(audit_path, headers=headers).status_code == expected


def test_service_request_keyset_pagination_equal_timestamps_scope_and_concurrent_insert(
    client, engine
) -> None:
    request_ids = [
        uuid.UUID(intake(client).json()["result"]["service_request_id"]) for _ in range(5)
    ]
    fixed = NOW - timedelta(days=1)
    service_requests = Base.metadata.tables["service_requests"]
    with engine.begin() as connection:
        connection.execute(
            update(service_requests)
            .where(service_requests.c.id.in_(request_ids))
            .values(created_at=fixed, updated_at=fixed)
        )
    grant(engine, "page-reader")
    grant(engine, "page-manager", "ManagerApprover")
    headers = {"Authorization": "Bearer page-reader"}

    first = client.get("/api/v1/service-requests?limit=2", headers=headers)
    assert first.status_code == 200
    first_result = first.json()["result"]
    cursor = first_result["page"]["next_cursor"]
    assert cursor and len(first_result["items"]) == 2
    concurrent_id = intake(client).json()["result"]["service_request_id"]

    seen = [item["id"] for item in first_result["items"]]
    while cursor:
        page = client.get(
            f"/api/v1/service-requests?limit=2&cursor={cursor}",
            headers=headers,
        )
        assert page.status_code == 200
        result = page.json()["result"]
        seen.extend(item["id"] for item in result["items"])
        cursor = result["page"]["next_cursor"]
    assert len(seen) == len(set(seen)) == 5
    assert set(seen) == {str(value) for value in request_ids}
    assert concurrent_id not in seen

    scope_cursor = first_result["page"]["next_cursor"]
    assert (
        client.get(
            f"/api/v1/service-requests?limit=2&cursor={scope_cursor}",
            headers={"Authorization": "Bearer page-manager"},
        ).status_code
        == 400
    )
    assert (
        client.get(
            f"/api/v1/service-requests?limit=2&status=TriagePending&cursor={scope_cursor}",
            headers=headers,
        ).status_code
        == 400
    )
    tampered = scope_cursor[:-1] + ("A" if scope_cursor[-1] != "A" else "B")
    assert (
        client.get(
            f"/api/v1/service-requests?limit=2&cursor={tampered}",
            headers=headers,
        ).status_code
        == 400
    )
    assert client.get("/api/v1/service-requests?cursor=invalid", headers=headers).status_code == 400
    assert client.get("/api/v1/service-requests?limit=0", headers=headers).status_code == 400
    assert client.get("/api/v1/service-requests?limit=101", headers=headers).status_code == 400


def test_request_graph_lists_paginate_and_bind_parent_principal_and_assignment(
    client, engine
) -> None:
    from tests.integration.test_deterministic_triage_lifecycle import (
        _insert_request_with_interpretation,
    )
    from tests.integration.test_deterministic_triage_persistence import (
        _insert_candidate,
        _insert_decision,
        _insert_request_graph,
    )

    graph = _insert_request_with_interpretation(
        engine,
        description="Pagination fixture",
        suggested_category="Repair",
    )
    other = _insert_request_graph(engine, "pagination-other")
    candidate_id = _insert_candidate(
        engine,
        source_request_id=graph["request"],
        candidate_request_id=other["request"],
    )
    decision_id = _insert_decision(engine, request_id=graph["request"])
    fixed = NOW - timedelta(days=2)
    tables = Base.metadata.tables
    interpretation_ids = [graph["interpretation"]]
    candidate_ids = [candidate_id]
    decision_ids = [decision_id]
    with engine.begin() as connection:
        connection.execute(
            update(tables["integration_attempts"])
            .where(tables["integration_attempts"].c.id == graph["attempt"])
            .values(
                assigned_workflow_service="workflow.protected",
                workflow_environment="test",
                created_at=fixed,
                updated_at=fixed,
            )
        )
        connection.execute(
            update(tables["ai_interpretations"])
            .where(tables["ai_interpretations"].c.id == graph["interpretation"])
            .values(created_at=fixed)
        )
        for number, digit in ((2, "d"), (3, "e")):
            operation_id = uuid.uuid4()
            attempt_id = uuid.uuid4()
            interpretation_id = uuid.uuid4()
            clone_row(
                connection,
                tables["logical_operations"],
                graph["operation"],
                id=operation_id,
                input_hash=digit * 64,
                succeeded_attempt_id=None,
                created_at=fixed,
                updated_at=fixed,
            )
            clone_row(
                connection,
                tables["integration_attempts"],
                graph["attempt"],
                id=attempt_id,
                logical_operation_id=operation_id,
                assigned_workflow_service=(
                    "workflow.protected" if number == 2 else "workflow.other"
                ),
                workflow_environment="test",
                callback_authorization_deadline=NOW + timedelta(hours=1),
                created_at=fixed,
                updated_at=fixed,
            )
            clone_row(
                connection,
                tables["ai_interpretations"],
                graph["interpretation"],
                id=interpretation_id,
                logical_operation_id=operation_id,
                producing_attempt_id=attempt_id,
                interpretation_number=number,
                input_hash=digit * 64,
                created_at=fixed,
            )
            connection.execute(
                update(tables["logical_operations"])
                .where(tables["logical_operations"].c.id == operation_id)
                .values(succeeded_attempt_id=attempt_id)
            )
            interpretation_ids.append(interpretation_id)

        connection.execute(
            update(tables["duplicate_candidates"])
            .where(tables["duplicate_candidates"].c.id == candidate_id)
            .values(detected_at=fixed)
        )
        for source_hash, candidate_hash in (("d" * 64, "e" * 64), ("f" * 64, "0" * 64)):
            cloned_id = uuid.uuid4()
            clone_row(
                connection,
                tables["duplicate_candidates"],
                candidate_id,
                id=cloned_id,
                source_evidence_hash=source_hash,
                candidate_evidence_hash=candidate_hash,
                detected_at=fixed,
            )
            candidate_ids.append(cloned_id)

        connection.execute(
            update(tables["routing_decisions"])
            .where(tables["routing_decisions"].c.id == decision_id)
            .values(evaluated_at=fixed, created_at=fixed)
        )
        for number, digest in ((2, "d" * 64), (3, "e" * 64)):
            cloned_id = uuid.uuid4()
            clone_row(
                connection,
                tables["routing_decisions"],
                decision_id,
                id=cloned_id,
                decision_number=number,
                canonical_input_hash=digest,
                evaluated_at=fixed,
                created_at=fixed,
            )
            decision_ids.append(cloned_id)

        audit_ids = [uuid.uuid4() for _ in range(3)]
        connection.execute(
            insert(tables["audit_events"]),
            [
                {
                    "id": audit_id,
                    "schema_version": "1.0",
                    "event_name": f"service_request.pagination_{number}",
                    "aggregate_type": "ServiceRequest",
                    "aggregate_id": graph["request"],
                    "aggregate_version": number,
                    "actor_type": "BackendService",
                    "actor_reference_id": uuid.uuid4(),
                    "occurred_at": fixed,
                    "outcome": "Succeeded",
                    "correlation_id": uuid.uuid4(),
                    "reason_codes": [],
                    "safe_metadata": {},
                }
                for number, audit_id in enumerate(audit_ids, start=1)
            ],
        )

    grant(engine, "graph-page-reader")
    grant(engine, "graph-page-manager", "ManagerApprover")
    grant(engine, "graph-page-admin", "Administrator")
    seed_workflow(engine)
    seed_workflow(
        engine,
        service_id="workflow.other",
        secret_reference="test/protected-query-other-pagination",
    )
    human = {"Authorization": "Bearer graph-page-reader"}
    manager = {"Authorization": "Bearer graph-page-manager"}
    administrator = {"Authorization": "Bearer graph-page-admin"}
    request_id = graph["request"]
    paths = {
        f"/api/v1/service-requests/{request_id}/timeline": ({str(v) for v in audit_ids}, human),
        f"/api/v1/service-requests/{request_id}/ai-interpretations": (
            {str(v) for v in interpretation_ids},
            human,
        ),
        f"/api/v1/service-requests/{request_id}/duplicate-candidates": (
            {str(v) for v in candidate_ids},
            human,
        ),
        f"/api/v1/service-requests/{request_id}/routing-decisions": (
            {str(v) for v in decision_ids},
            human,
        ),
        (f"/api/v1/audit-events?aggregate_type=ServiceRequest&aggregate_id={request_id}"): (
            {str(v) for v in audit_ids},
            manager,
        ),
    }
    cursors = {}
    for path, (expected, headers) in paths.items():
        seen, cursor = walk_pages(client, path, headers)
        assert set(seen) == expected
        assert cursor is not None
        cursors[path] = cursor
        separator = "&" if "?" in path else "?"
        terminal = client.get(f"{path}{separator}limit=100", headers=headers)
        assert terminal.status_code == 200
        assert terminal.json()["result"]["page"]["next_cursor"] is None
        assert client.get(f"{path}{separator}limit=0", headers=headers).status_code == 400
        assert client.get(f"{path}{separator}limit=101", headers=headers).status_code == 400

    timeline_path = f"/api/v1/service-requests/{request_id}/timeline"
    timeline_cursor = cursors[timeline_path]
    assert (
        client.get(
            f"{timeline_path}?limit=1&cursor={timeline_cursor}",
            headers=manager,
        ).status_code
        == 400
    )
    assert (
        client.get(
            f"/api/v1/service-requests/{other['request']}/timeline?limit=1&cursor={timeline_cursor}",
            headers=human,
        ).status_code
        == 400
    )
    tampered = timeline_cursor[:-1] + ("A" if timeline_cursor[-1] != "A" else "B")
    assert (
        client.get(f"{timeline_path}?limit=1&cursor={tampered}", headers=human).status_code == 400
    )
    audit_path = f"/api/v1/audit-events?aggregate_type=ServiceRequest&aggregate_id={request_id}"
    assert (
        client.get(
            f"{audit_path}&limit=1&cursor={cursors[audit_path]}",
            headers=administrator,
        ).status_code
        == 400
    )

    backend = BackendProtectedQueryService(create_session_factory(engine))
    assert {item.id for item in backend.get_request_timeline(request_id).items} == set(audit_ids)
    assert {item.id for item in backend.list_ai_interpretations(request_id).items} == set(
        interpretation_ids
    )
    assert {item.id for item in backend.list_duplicate_candidates(request_id).items} == set(
        candidate_ids
    )
    assert {item.id for item in backend.list_routing_decisions(request_id).items} == set(
        decision_ids
    )
    assert {
        item.id for item in backend.list_audit_events("ServiceRequest", request_id).items
    } == set(audit_ids)

    ai_path = f"/api/v1/service-requests/{request_id}/ai-interpretations"
    protected_first = client.get(
        f"{ai_path}?limit=1",
        headers=machine_headers("GET", ai_path, query="limit=1"),
    )
    other_first = client.get(
        f"{ai_path}?limit=1",
        headers=machine_headers("GET", ai_path, query="limit=1", service_id="workflow.other"),
    )
    assert protected_first.status_code == other_first.status_code == 200
    protected_items = protected_first.json()["result"]["items"]
    other_items = other_first.json()["result"]["items"]
    assert len(protected_items) == len(other_items) == 1
    assert protected_items[0]["id"] != other_items[0]["id"]
    assignment_cursor = protected_first.json()["result"]["page"]["next_cursor"]
    assert assignment_cursor is not None
    assignment_query = f"cursor={assignment_cursor}&limit=1"
    assert (
        client.get(
            f"{ai_path}?{assignment_query}",
            headers=machine_headers(
                "GET", ai_path, query=assignment_query, service_id="workflow.other"
            ),
        ).status_code
        == 400
    )


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
