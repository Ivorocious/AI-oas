import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, func, insert, select, update
from sqlalchemy.orm import Session, sessionmaker
from test_ai_failure_recovery_lifecycle import (
    LifecycleContext,
    RunningAttempt,
    _create_running_attempt,
    _lifecycle_counts,
    _retryable_command,
    _row,
    _seed_request,
    _signed_post,
    engine,
    lifecycle_context,
)

from ai_operations_automation.db import Base
from ai_operations_automation.stale_attempts.service import AssessStaleAttemptService

pytestmark = pytest.mark.integration
_IMPORTED_FIXTURES = (engine, lifecycle_context)

START_AI_PATH = "/api/v1/service-requests/{request_id}/commands/start-ai-interpretation"
REPLACE_PATH = "/api/v1/integration-attempts/{attempt_id}/commands/replace-callback-credential"
RETRYABLE_CALLBACK_PATH = "/api/v1/integration-attempts/{attempt_id}/callbacks/retryable-failure"
TERMINAL_PATH = "/api/v1/service-requests/{request_id}/commands/mark-terminal-failure"


class TokenVerifier:
    def verify(self, token: str) -> str:
        return token


def _create_pending_attempt(context: LifecycleContext, *, suffix: str) -> RunningAttempt:
    request_id = _seed_request(context.engine)
    path = START_AI_PATH.format(request_id=request_id)
    response = _signed_post(
        context,
        path,
        {
            "schema_version": "1.0",
            "expected_versions": {"service_request": 1},
            "command": {},
        },
        key=f"start-pending-{suffix}",
        nonce=f"start-pending-{suffix}-nonce-0001",
    )
    assert response.status_code == 202, response.text
    result = response.json()["result"]
    return RunningAttempt(
        request_id=request_id,
        operation_id=uuid.UUID(result["logical_operation_id"]),
        attempt_id=uuid.UUID(result["integration_attempt_id"]),
        callback_credential=result["callback_credential"],
    )


def _replacement_payload() -> dict:
    return {
        "schema_version": "1.0",
        "expected_versions": {
            "integration_attempt": 2,
            "callback_credential": 1,
        },
        "command": {},
    }


def _credential_rows(engine: Engine, attempt_id: uuid.UUID) -> list[dict]:
    table = Base.metadata.tables["attempt_callback_credentials"]
    with engine.connect() as connection:
        return list(
            connection.execute(
                select(table)
                .where(table.c.integration_attempt_id == attempt_id)
                .order_by(table.c.credential_version)
            ).mappings()
        )


def _grant(engine: Engine, subject: str, role: str) -> uuid.UUID:
    actor_id = uuid.uuid4()
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            insert(Base.metadata.tables["application_actors"]).values(
                id=actor_id,
                supabase_subject=subject,
                display_label=subject,
                status="Active",
            )
        )
        connection.execute(
            insert(Base.metadata.tables["application_actor_role_assignments"]).values(
                id=uuid.uuid4(),
                actor_id=actor_id,
                role=role,
                assigned_by_actor_id=actor_id,
                effective_from=now,
                assignment_reason="failure-recovery edge integration test",
            )
        )
    return actor_id


def _terminal_payload(running: RunningAttempt) -> dict:
    return {
        "schema_version": "1.0",
        "expected_versions": {"service_request": 3},
        "command": {
            "failed_attempt_id": str(running.attempt_id),
            "rationale": "The manager confirmed that this retryable work must stop.",
        },
    }


def test_callback_credential_replacement_issues_plaintext_once_and_replays_read_only(
    lifecycle_context: LifecycleContext,
) -> None:
    running = _create_running_attempt(lifecycle_context, suffix="replace-replay")
    path = REPLACE_PATH.format(attempt_id=running.attempt_id)
    payload = _replacement_payload()

    first = _signed_post(
        lifecycle_context,
        path,
        payload,
        key="replace-callback-replay-key-0001",
        nonce="replace-callback-replay-nonce-0001",
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["result"]["credential_delivery"] == "PlaintextIssued"
    assert first_body["result"]["callback_credential"] == "B" * 43
    assert first_body["result"]["callback_credential_version"] == 2
    assert first_body["versions"] == {
        "integration_attempt": 2,
        "callback_credential": 2,
    }

    credentials = _credential_rows(lifecycle_context.engine, running.attempt_id)
    assert [row["credential_version"] for row in credentials] == [1, 2]
    assert [row["state"] for row in credentials] == ["Replaced", "Active"]
    assert credentials[0]["replacement_credential_id"] == credentials[1]["id"]
    assert credentials[0]["replaced_at"].tzinfo is not None
    assert credentials[0]["expires_at"] == credentials[1]["expires_at"]
    attempt = _row(lifecycle_context.engine, "integration_attempts", running.attempt_id)
    assert attempt["state"] == "Running" and attempt["version"] == 2

    before_replay = _lifecycle_counts(lifecycle_context.engine)
    replay = _signed_post(
        lifecycle_context,
        path,
        payload,
        key="replace-callback-replay-key-0001",
        nonce="replace-callback-replay-nonce-0002",
    )
    assert replay.status_code == 200, replay.text
    replay_body = replay.json()
    assert replay_body["command_id"] == first_body["command_id"]
    assert replay_body["result"]["credential_delivery"] == "AlreadyIssued"
    assert "callback_credential" not in replay_body["result"]
    assert (
        replay_body["result"]["callback_credential_id"]
        == first_body["result"]["callback_credential_id"]
    )
    assert _lifecycle_counts(lifecycle_context.engine) == before_replay
    assert _credential_rows(lifecycle_context.engine, running.attempt_id) == credentials


def test_concurrent_callback_credential_replacements_have_one_version_winner(
    lifecycle_context: LifecycleContext,
) -> None:
    running = _create_running_attempt(lifecycle_context, suffix="replace-concurrent")
    path = REPLACE_PATH.format(attempt_id=running.attempt_id)

    def invoke(index: int):
        return _signed_post(
            lifecycle_context,
            path,
            _replacement_payload(),
            key=f"replace-concurrent-key-{index}-0001",
            nonce=f"replace-concurrent-nonce-{index}-0001",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(invoke, (1, 2)))

    assert sorted(response.status_code for response in responses) == [200, 409]
    success = next(response for response in responses if response.status_code == 200)
    conflict = next(response for response in responses if response.status_code == 409)
    assert success.json()["result"]["callback_credential_version"] == 2
    assert success.json()["result"]["callback_credential"] == "B" * 43
    assert conflict.json()["error"]["code"] == "CALLBACK_CREDENTIAL_VERSION_CONFLICT"
    assert conflict.json()["error"]["current_versions"] == {
        "integration_attempt": 2,
        "callback_credential": 2,
    }
    assert lifecycle_context.generator.calls == 2
    credentials = _credential_rows(lifecycle_context.engine, running.attempt_id)
    assert [row["credential_version"] for row in credentials] == [1, 2]
    assert [row["state"] for row in credentials] == ["Replaced", "Active"]


@pytest.mark.parametrize(
    ("initial_state", "threshold", "expected_attempt_version", "failure_code"),
    [
        ("Pending", timedelta(minutes=2), 1, "ATTEMPT_PENDING_STALE"),
        ("Running", timedelta(minutes=5), 2, "AI_ATTEMPT_RUNNING_STALE"),
    ],
)
def test_internal_stale_assessment_accepts_exact_database_time_boundary(
    lifecycle_context: LifecycleContext,
    initial_state: str,
    threshold: timedelta,
    expected_attempt_version: int,
    failure_code: str,
) -> None:
    if initial_state == "Pending":
        attempt = _create_pending_attempt(lifecycle_context, suffix="stale-pending-boundary")
        timestamp_column = "created_at"
    else:
        attempt = _create_running_attempt(lifecycle_context, suffix="stale-running-boundary")
        timestamp_column = "started_at"

    attempts = Base.metadata.tables["integration_attempts"]
    credentials = Base.metadata.tables["attempt_callback_credentials"]
    with lifecycle_context.engine.connect() as connection:
        outer = connection.begin()
        try:
            database_now = connection.scalar(select(func.now()))
            assert database_now is not None
            boundary_start = database_now - threshold
            connection.execute(
                update(attempts)
                .where(attempts.c.id == attempt.attempt_id)
                .values({timestamp_column: boundary_start})
            )
            bound_factory = sessionmaker[Session](
                bind=connection,
                autoflush=False,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            outcome = AssessStaleAttemptService(bound_factory).execute(
                attempt_id=attempt.attempt_id,
                expected_attempt_version=expected_attempt_version,
                durable_command_key=f"stale-{initial_state.lower()}-boundary-command-0001",
                correlation_id=uuid.uuid4(),
            )
            assert outcome.logical_http_status == 200
            assert outcome.safe_snapshot["result"]["failure_code"] == failure_code

            observed_attempt = (
                connection.execute(select(attempts).where(attempts.c.id == attempt.attempt_id))
                .mappings()
                .one()
            )
            observed_credential = (
                connection.execute(
                    select(credentials).where(
                        credentials.c.integration_attempt_id == attempt.attempt_id
                    )
                )
                .mappings()
                .one()
            )
            assert observed_attempt[timestamp_column] + threshold == database_now
            assert observed_attempt["assessed_at"] == database_now
            assert observed_attempt["completed_at"] == database_now
            assert observed_attempt["state"] == "RetryableFailure"
            assert observed_attempt["sanitized_error_code"] == failure_code
            assert observed_attempt["version"] == expected_attempt_version + 1
            assert observed_credential["state"] == "Revoked"
            assert observed_credential["revoked_at"] == database_now
        finally:
            outer.rollback()


def test_manager_can_terminalize_retryable_work_but_operations_agent_is_denied(
    lifecycle_context: LifecycleContext,
) -> None:
    running = _create_running_attempt(lifecycle_context, suffix="manager-terminal")
    callback_path = RETRYABLE_CALLBACK_PATH.format(attempt_id=running.attempt_id)
    failed = _signed_post(
        lifecycle_context,
        callback_path,
        _retryable_command(lifecycle_context.settings).model_dump(mode="json"),
        key="terminal-setup-callback-key-0001",
        nonce="terminal-setup-callback-nonce-0001",
        extra_headers={"X-Attempt-Callback-Credential": running.callback_credential},
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["result"]["service_request_status"] == "RetryableFailure"

    lifecycle_context.client.app.state.jwt_verifier = TokenVerifier()
    _grant(lifecycle_context.engine, "edge-operations-agent", "OperationsAgent")
    _grant(lifecycle_context.engine, "edge-manager", "ManagerApprover")
    path = TERMINAL_PATH.format(request_id=running.request_id)
    payload = _terminal_payload(running)
    serialized = json.dumps(payload, separators=(",", ":")).encode()
    before_denial = _lifecycle_counts(lifecycle_context.engine)

    denied = lifecycle_context.client.post(
        path,
        content=serialized,
        headers={
            "Authorization": "Bearer edge-operations-agent",
            "Content-Type": "application/json",
            "Idempotency-Key": "operations-agent-terminal-key-0001",
            "X-Correlation-ID": str(uuid.uuid4()),
        },
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "FORBIDDEN"
    assert _lifecycle_counts(lifecycle_context.engine) == before_denial

    manager_headers = {
        "Authorization": "Bearer edge-manager",
        "Content-Type": "application/json",
        "Idempotency-Key": "manager-terminal-key-0001",
        "X-Correlation-ID": str(uuid.uuid4()),
    }
    accepted = lifecycle_context.client.post(path, content=serialized, headers=manager_headers)
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["result"]["service_request_status"] == "TerminalFailure"
    assert body["result"]["service_request_queue"] is None
    assert body["result"]["failure_code"] == "PROVIDER_TIMEOUT"
    assert body["result"]["terminal_disposition_code"] == "MANAGER_TERMINAL_DISPOSITION"
    assert body["versions"] == {"service_request": 4}
    assert payload["command"]["rationale"] not in accepted.text

    service_request = _row(lifecycle_context.engine, "service_requests", running.request_id)
    failed_attempt = _row(
        lifecycle_context.engine,
        "integration_attempts",
        running.attempt_id,
    )
    assert service_request["status"] == "TerminalFailure"
    assert service_request["current_queue"] is None
    assert service_request["recovery_target"] is None
    assert service_request["recovery_attempt_id"] == running.attempt_id
    assert service_request["failure_summary_code"] == "PROVIDER_TIMEOUT"
    assert service_request["terminal_at"].tzinfo is not None
    assert failed_attempt["state"] == "RetryableFailure"

    before_replay = _lifecycle_counts(lifecycle_context.engine)
    replay_headers = {**manager_headers, "X-Correlation-ID": str(uuid.uuid4())}
    replay = lifecycle_context.client.post(path, content=serialized, headers=replay_headers)
    assert replay.status_code == 200, replay.text
    assert replay.json()["command_id"] == body["command_id"]
    assert replay.json()["result"] == body["result"]
    assert _lifecycle_counts(lifecycle_context.engine) == before_replay
