import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, func, insert, select, text

from ai_operations_automation.api.correlation import resolve_request_correlation
from ai_operations_automation.app import create_app
from ai_operations_automation.command_idempotency import (
    CommandIdempotencyScope,
    CommandIdempotencyService,
    CompletedCommandReplay,
    canonical_command_hash,
    resolve_command_idempotency_key,
)
from ai_operations_automation.config import Settings
from ai_operations_automation.db import Base, create_database_engine, create_session_factory
from ai_operations_automation.machine_auth.authenticator import calculate_signature
from ai_operations_automation.machine_auth.canonicalization import canonical_signing_bytes
from ai_operations_automation.machine_auth.dependencies import authenticated_workflow_service
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService
from alembic import command

pytestmark = pytest.mark.integration
BACKEND_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime.now(UTC)
SECRET = b"synthetic-command-http-secret"


class SyntheticCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int
    value: str
    note: str | None = None


class Resolver:
    def resolve(self, reference: str) -> bytes:
        if reference != "test/command-current":
            raise RuntimeError("unexpected test secret reference")
        return SECRET


@pytest.fixture(scope="module")
def engine() -> Engine:
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")
    value = create_database_engine(Settings(_env_file=None).database_url)
    yield value
    value.dispose()


@pytest.fixture
def command_client(engine: Engine):
    tables = ", ".join(
        f'"{name}"'
        for name in Base.metadata.tables
        if name not in {"failure_recovery_policy_versions", "decision_policy_versions"}
    )
    identity_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} CASCADE"))
        connection.execute(
            insert(Base.metadata.tables["machine_identities"]).values(
                id=identity_id,
                service_type="WorkflowService",
                environment="test",
                stable_service_id="workflow.command-test",
                display_label="Command test workflow",
                status="Active",
            )
        )
        connection.execute(
            insert(Base.metadata.tables["machine_credential_versions"]).values(
                id=uuid.uuid4(),
                machine_identity_id=identity_id,
                credential_version=1,
                external_secret_reference="test/command-current",
                status="Current",
                activated_at=NOW - timedelta(days=1),
            )
        )

    factory = create_session_factory(engine)
    app = create_app(
        Settings(app_environment="test", _env_file=None),
        factory,
        machine_secret_resolver=Resolver(),
        machine_clock=lambda: NOW,
    )
    app.state.synthetic_executions = 0

    @app.post("/__tests__/commands/{target_id}", include_in_schema=False)
    async def synthetic_command(
        target_id: uuid.UUID,
        body: SyntheticCommand,
        request: Request,
        machine: AuthenticatedWorkflowService = Depends(authenticated_workflow_service),
        key: str = Depends(resolve_command_idempotency_key),
        correlation_id: uuid.UUID = Depends(resolve_request_correlation),
    ) -> JSONResponse:
        selected_scope = CommandIdempotencyScope(
            actor_class="MachineService",
            actor_id=machine.machine_identity_id,
            command_intent="SyntheticCommand",
            route_template="/__tests__/commands/{target_id}",
            target_type="SyntheticTarget",
            target_id=target_id,
        )
        with factory() as session, session.begin():
            service = CommandIdempotencyService(session)
            result = service.reserve(
                selected_scope,
                key,
                canonical_command_hash(body),
                correlation_id,
            )
            replayed = isinstance(result, CompletedCommandReplay)
            if not replayed:
                app.state.synthetic_executions += 1
                result = service.complete(
                    result,
                    202,
                    {"result": "synthetic", "target_id": str(target_id)},
                )
        return JSONResponse(
            status_code=result.logical_http_status,
            content={
                "correlation_id": str(correlation_id),
                "command_id": str(result.command_id),
                "result": result.safe_response_snapshot,
                "replayed": replayed,
            },
            headers={"X-Correlation-ID": str(correlation_id)},
        )

    return app, TestClient(app), engine, identity_id


def signed_headers(
    target_id: uuid.UUID,
    body: bytes,
    *,
    key: str | None = "http-command-key",
    nonce: str = "command-http-nonce-012345",
    correlation_id: uuid.UUID | None = None,
):
    path = f"/__tests__/commands/{target_id}".encode()
    timestamp = str(int(NOW.timestamp()))
    signature = calculate_signature(
        SECRET,
        canonical_signing_bytes("POST", path, b"", timestamp, nonce, body),
    )
    headers = {
        "Content-Type": "application/json",
        "X-Service-ID": "workflow.command-test",
        "X-Service-Timestamp": timestamp,
        "X-Service-Nonce": nonce,
        "X-Service-Signature": signature,
        "X-Correlation-ID": str(correlation_id or uuid.uuid4()),
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def encoded_body(expected_version: int = 1, value: str = "same") -> bytes:
    return json.dumps(
        {"expected_version": expected_version, "value": value, "note": None},
        separators=(",", ":"),
    ).encode()


def post_command(client: TestClient, target_id: uuid.UUID, body: bytes, **header_changes):
    headers = signed_headers(target_id, body, **header_changes)
    return client.post(f"/__tests__/commands/{target_id}", content=body, headers=headers)


def test_first_command_and_exact_replay_use_current_correlation(command_client) -> None:
    app, client, engine, _ = command_client
    target_id = uuid.uuid4()
    body = encoded_body()
    first_correlation = uuid.uuid4()
    replay_correlation = uuid.uuid4()
    first = post_command(
        client,
        target_id,
        body,
        nonce="command-first-nonce-012345",
        correlation_id=first_correlation,
    )
    replay = post_command(
        client,
        target_id,
        body,
        nonce="command-replay-nonce-01234",
        correlation_id=replay_correlation,
    )
    assert first.status_code == replay.status_code == 202
    assert first.json()["command_id"] == replay.json()["command_id"]
    assert first.json()["replayed"] is False and replay.json()["replayed"] is True
    assert (
        replay.json()["correlation_id"]
        == replay.headers["x-correlation-id"]
        == str(replay_correlation)
    )
    assert app.state.synthetic_executions == 1
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(
                    Base.metadata.tables["command_idempotency_records"]
                )
            )
            == 1
        )


@pytest.mark.parametrize("body", [encoded_body(value="changed"), encoded_body(expected_version=2)])
def test_changed_body_or_expected_version_conflicts(command_client, body) -> None:
    app, client, engine, identity_id = command_client
    target_id = uuid.uuid4()
    original = encoded_body()
    assert (
        post_command(client, target_id, original, nonce="command-original-01234567").status_code
        == 202
    )
    key = "http-command-key"
    response = post_command(client, target_id, body, nonce="command-conflict-01234567", key=key)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "COMMAND_IDEMPOTENCY_CONFLICT"
    assert app.state.synthetic_executions == 1
    redacted = response.text
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
                select(func.count()).select_from(Base.metadata.tables["machine_request_nonces"])
            )
            == 2
        )
    for forbidden in (
        key,
        str(identity_id),
        "command_idempotency_records",
        "canonical_body_hash",
        "idempotency_key_digest",
        "SELECT",
        "Traceback",
    ):
        assert forbidden not in redacted


def test_same_key_under_another_target_is_independent(command_client) -> None:
    app, client, _, _ = command_client
    body = encoded_body()
    responses = [
        post_command(client, uuid.uuid4(), body, nonce=f"command-target-{index}-012345")
        for index in range(2)
    ]
    assert [response.status_code for response in responses] == [202, 202]
    assert app.state.synthetic_executions == 2


def test_missing_duplicate_invalid_key_are_safe_400(command_client) -> None:
    _, client, engine, _ = command_client
    body = encoded_body()
    target_id = uuid.uuid4()
    missing = post_command(client, target_id, body, nonce="command-missing-01234567", key=None)
    invalid = post_command(client, target_id, body, nonce="command-invalid-01234567", key="short")
    headers = list(signed_headers(target_id, body, nonce="command-duplicate-012345").items()) + [
        ("Idempotency-Key", "second-command-key")
    ]
    duplicate = client.post(f"/__tests__/commands/{target_id}", content=body, headers=headers)
    assert all(response.status_code == 400 for response in (missing, invalid, duplicate))
    assert all(
        response.json()["error"]["code"] == "MISSING_IDEMPOTENCY_KEY"
        for response in (missing, invalid, duplicate)
    )
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(
                    Base.metadata.tables["command_idempotency_records"]
                )
            )
            == 0
        )


def test_reused_machine_nonce_fails_before_idempotency(command_client) -> None:
    app, client, engine, _ = command_client
    target_id = uuid.uuid4()
    body = encoded_body()
    nonce = "command-reused-nonce-012345"
    assert post_command(client, target_id, body, nonce=nonce).status_code == 202
    replay = post_command(client, target_id, body, nonce=nonce)
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "MACHINE_AUTHENTICATION_FAILED"
    assert "www-authenticate" not in replay.headers
    assert app.state.synthetic_executions == 1
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(
                    Base.metadata.tables["command_idempotency_records"]
                )
            )
            == 1
        )


def test_private_route_is_not_in_production_or_openapi(command_client) -> None:
    app, _, _, _ = command_client
    assert "/__tests__/commands/{target_id}" not in app.openapi()["paths"]
    production = create_app(Settings(_env_file=None))
    assert set(production.openapi()["paths"]) == {
        "/health",
        "/api/v1/intake/service-requests",
        "/api/v1/service-requests/{request_id}",
        "/api/v1/service-requests",
        "/api/v1/inbound-deliveries/{delivery_id}",
        "/api/v1/service-requests/{request_id}/timeline",
        "/api/v1/service-requests/{request_id}/ai-interpretations",
        "/api/v1/service-requests/{request_id}/duplicate-candidates",
        "/api/v1/service-requests/{request_id}/routing-decisions",
        "/api/v1/proposed-actions/{action_id}",
        "/api/v1/proposed-actions/{action_id}/approvals",
        "/api/v1/proposed-actions/{action_id}/integration-attempts",
        "/api/v1/integration-attempts/{attempt_id}",
        "/api/v1/audit-events",
        "/api/v1/service-requests/{request_id}/commands/start-ai-interpretation",
        "/api/v1/integration-attempts/{attempt_id}/commands/start",
        "/api/v1/integration-attempts/{attempt_id}/callbacks/succeeded",
        "/api/v1/integration-attempts/{attempt_id}/callbacks/retryable-failure",
        "/api/v1/integration-attempts/{attempt_id}/callbacks/terminal-failure",
        "/api/v1/integration-attempts/{attempt_id}/commands/replace-callback-credential",
        "/api/v1/service-requests/{request_id}/commands/retry-ai",
        "/api/v1/service-requests/{request_id}/commands/mark-terminal-failure",
        (
            "/api/v1/service-requests/{request_id}/duplicate-candidates/"
            "{candidate_id}/commands/resolve"
        ),
        "/api/v1/service-requests/{request_id}/commands/complete-human-review",
        "/api/v1/service-requests/{request_id}/proposed-actions",
        "/api/v1/proposed-actions/{action_id}/draft",
        "/api/v1/proposed-actions/{action_id}/commands/submit-for-approval",
        "/api/v1/proposed-actions/{action_id}/commands/approve",
        "/api/v1/proposed-actions/{action_id}/commands/reject",
        "/api/v1/proposed-actions/{action_id}/commands/create-material-revision",
        "/api/v1/proposed-actions/{action_id}/commands/start-outbound",
        "/api/v1/proposed-actions/{action_id}/commands/retry-outbound",
    }
