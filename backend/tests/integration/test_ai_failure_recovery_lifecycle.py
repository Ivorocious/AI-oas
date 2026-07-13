import hashlib
import json
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, insert, select, text, update

from ai_operations_automation.app import create_app
from ai_operations_automation.attempt_callbacks.models import (
    AiRetryableFailureCallbackRequest,
    AiSuccessCallbackRequest,
    AiTerminalFailureCallbackRequest,
)
from ai_operations_automation.attempt_callbacks.service import AiAttemptCallbackService
from ai_operations_automation.command_idempotency.canonicalization import canonical_command_hash
from ai_operations_automation.config import Settings
from ai_operations_automation.db import Base, create_database_engine, create_session_factory
from ai_operations_automation.failure_recovery.policy import DEMO_FAILURE_RECOVERY_POLICY
from ai_operations_automation.machine_auth.authenticator import calculate_signature
from ai_operations_automation.machine_auth.canonicalization import canonical_signing_bytes
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService
from ai_operations_automation.retry_ai.models import RetryAiRequest
from ai_operations_automation.retry_ai.service import RetryAiService
from alembic import command

pytestmark = pytest.mark.integration
BACKEND_ROOT = Path(__file__).resolve().parents[2]
SECRET = b"synthetic-ai-lifecycle-machine-secret"
SERVICE_ID = "workflow.ai-lifecycle.test"
SECRET_REFERENCE = "test/ai-lifecycle-current"
START_AI_PATH = "/api/v1/service-requests/{request_id}/commands/start-ai-interpretation"
CLAIM_PATH = "/api/v1/integration-attempts/{attempt_id}/commands/start"
SUCCESS_CALLBACK_PATH = "/api/v1/integration-attempts/{attempt_id}/callbacks/succeeded"
RETRY_AI_PATH = "/api/v1/service-requests/{request_id}/commands/retry-ai"
POLICY_TABLE = "failure_recovery_policy_versions"


class Resolver:
    def resolve(self, reference: str) -> bytes:
        if reference != SECRET_REFERENCE:
            raise RuntimeError("unknown synthetic secret reference")
        return SECRET


class CredentialGenerator:
    def __init__(self) -> None:
        self.calls = 0
        self.lock = threading.Lock()

    def __call__(self) -> str:
        with self.lock:
            self.calls += 1
            character = chr(64 + self.calls)
        return character * 43


@dataclass(frozen=True, slots=True)
class LifecycleContext:
    client: TestClient
    engine: Engine
    settings: Settings
    session_factory: object
    machine: AuthenticatedWorkflowService
    clock: datetime
    generator: CredentialGenerator


@dataclass(frozen=True, slots=True)
class RunningAttempt:
    request_id: uuid.UUID
    operation_id: uuid.UUID
    attempt_id: uuid.UUID
    callback_credential: str


@pytest.fixture(scope="module")
def engine() -> Engine:
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")
    value = create_database_engine(Settings(_env_file=None).database_url)
    yield value
    value.dispose()


@pytest.fixture
def lifecycle_context(engine: Engine) -> LifecycleContext:
    retained = Base.metadata.tables[POLICY_TABLE]
    truncated = [name for name in Base.metadata.tables if name != POLICY_TABLE]
    quoted = ", ".join(f'"{name}"' for name in truncated)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {quoted} CASCADE"))
        if connection.scalar(select(func.count()).select_from(retained)) == 0:
            _insert_demo_policy(connection)

    clock = datetime.now(UTC)
    settings = Settings(app_environment="test", _env_file=None)
    session_factory = create_session_factory(engine)
    generator = CredentialGenerator()
    identity_id = uuid.uuid4()
    machine_credential_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            insert(Base.metadata.tables["machine_identities"]).values(
                id=identity_id,
                service_type="WorkflowService",
                environment="test",
                stable_service_id=SERVICE_ID,
                display_label="Synthetic AI lifecycle workflow",
                status="Active",
                version=1,
            )
        )
        connection.execute(
            insert(Base.metadata.tables["machine_credential_versions"]).values(
                id=machine_credential_id,
                machine_identity_id=identity_id,
                credential_version=1,
                external_secret_reference=SECRET_REFERENCE,
                status="Current",
                activated_at=clock - timedelta(days=1),
            )
        )

    app = create_app(
        settings,
        session_factory,
        machine_secret_resolver=Resolver(),
        machine_clock=lambda: clock,
        callback_credential_generator=generator,
    )
    machine = AuthenticatedWorkflowService(
        machine_identity_id=identity_id,
        stable_service_id=SERVICE_ID,
        environment="test",
        service_type="WorkflowService",
        credential_id=machine_credential_id,
        credential_version=1,
    )
    return LifecycleContext(
        client=TestClient(app),
        engine=engine,
        settings=settings,
        session_factory=session_factory,
        machine=machine,
        clock=clock,
        generator=generator,
    )


def _insert_demo_policy(connection) -> None:
    policy = DEMO_FAILURE_RECOVERY_POLICY
    content = policy.content.model_dump(mode="json")
    connection.execute(
        insert(Base.metadata.tables[POLICY_TABLE]).values(
            id=policy.id,
            policy_key=policy.policy_key,
            semantic_version=policy.semantic_version,
            revision=policy.revision,
            content_digest=policy.content_digest,
            effective_at=policy.effective_at,
            status=policy.status.value,
            policy_snapshot=content,
            operation_kind_rules=content["operation_kind_rules"],
            failure_code_catalog=content["failure_code_catalog"],
            attempt_budgets=content["attempt_budgets"],
            retry_delay_schedule=content["retry_delay_schedule"],
            stale_attempt_thresholds=content["stale_attempt_thresholds"],
            reconciliation_rules=content["reconciliation_rules"],
            recovery_disposition_rules=content["recovery_disposition_rules"],
            terminalization_rules=content["terminalization_rules"],
        )
    )


def _seed_request(engine: Engine) -> uuid.UUID:
    tables = Base.metadata.tables
    delivery_id = uuid.uuid4()
    contact_id = uuid.uuid4()
    request_id = uuid.uuid4()
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
                status="TriagePending",
                version=1,
                location_context="Private home context",
            )
        )
    return request_id


def _signed_post(
    context: LifecycleContext,
    path: str,
    payload: dict,
    *,
    key: str,
    nonce: str,
    extra_headers: dict[str, str] | None = None,
):
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(context.clock.timestamp()))
    signing = canonical_signing_bytes(
        "POST",
        path.encode(),
        b"",
        timestamp,
        nonce,
        body,
    )
    headers = {
        "Content-Type": "application/json",
        "Idempotency-Key": key,
        "X-Correlation-ID": str(uuid.uuid4()),
        "X-Service-ID": SERVICE_ID,
        "X-Service-Timestamp": timestamp,
        "X-Service-Nonce": nonce,
        "X-Service-Signature": calculate_signature(SECRET, signing),
    }
    headers.update(extra_headers or {})
    return context.client.post(path, content=body, headers=headers)


def _create_running_attempt(context: LifecycleContext, *, suffix: str) -> RunningAttempt:
    request_id = _seed_request(context.engine)
    start_path = START_AI_PATH.format(request_id=request_id)
    started = _signed_post(
        context,
        start_path,
        {
            "schema_version": "1.0",
            "expected_versions": {"service_request": 1},
            "command": {},
        },
        key=f"start-ai-{suffix}",
        nonce=f"start-ai-{suffix}-nonce-0001",
    )
    assert started.status_code == 202, started.text
    start_result = started.json()["result"]
    attempt_id = uuid.UUID(start_result["integration_attempt_id"])
    operation_id = uuid.UUID(start_result["logical_operation_id"])
    callback_credential = start_result["callback_credential"]

    claim_path = CLAIM_PATH.format(attempt_id=attempt_id)
    claimed = _signed_post(
        context,
        claim_path,
        {
            "schema_version": "1.0",
            "expected_versions": {"integration_attempt": 1},
            "command": {},
        },
        key=f"claim-ai-{suffix}",
        nonce=f"claim-ai-{suffix}-nonce-0001",
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["versions"] == {"integration_attempt": 2}
    return RunningAttempt(request_id, operation_id, attempt_id, callback_credential)


def _success_command(settings: Settings) -> AiSuccessCallbackRequest:
    return AiSuccessCallbackRequest.model_validate(
        {
            "schema_version": "1.0",
            "expected_versions": {"integration_attempt": 2},
            "evidence": {
                "result_schema_version": settings.ai_interpretation_result_schema_version,
                "prompt_version": settings.ai_interpretation_prompt_version,
                "provider_name": settings.ai_provider_name,
                "model_name": settings.ai_model_name,
                "adapter_name": settings.ai_adapter_name,
                "adapter_version": settings.ai_adapter_version,
                "safe_provider_correlation": "provider-safe-correlation-001",
                "latency_ms": 125,
                "token_usage": {"input_tokens": 80, "output_tokens": 24},
                "interpretation": {
                    "summary": "A kitchen pipe needs repair.",
                    "suggested_category": "Repair",
                    "missing_information": ["ACCESS_DETAILS"],
                    "confidence": "0.9100",
                    "warning_codes": ["CUSTOMER_TIMING_UNCONFIRMED"],
                },
            },
        }
    )


def _retryable_command(settings: Settings) -> AiRetryableFailureCallbackRequest:
    return AiRetryableFailureCallbackRequest.model_validate(
        {
            "schema_version": "1.0",
            "expected_versions": {"integration_attempt": 2},
            "evidence": {
                "failure_code": "PROVIDER_TIMEOUT",
                "adapter_version": settings.ai_adapter_version,
                "safe_provider_correlation": "provider-timeout-correlation-001",
                "safe_reason_codes": ["UPSTREAM_TIMEOUT"],
                "duration_ms": 30000,
            },
        }
    )


def _terminal_command(settings: Settings) -> AiTerminalFailureCallbackRequest:
    return AiTerminalFailureCallbackRequest.model_validate(
        {
            "schema_version": "1.0",
            "expected_versions": {"integration_attempt": 2},
            "evidence": {
                "failure_code": "PROVIDER_AUTHENTICATION_FAILED",
                "adapter_version": settings.ai_adapter_version,
                "safe_reason_codes": ["PROVIDER_CREDENTIAL_REJECTED"],
                "provider_status_code": 401,
            },
        }
    )


def _row(engine: Engine, table_name: str, row_id: uuid.UUID):
    table = Base.metadata.tables[table_name]
    with engine.connect() as connection:
        return connection.execute(select(table).where(table.c.id == row_id)).mappings().one()


def _lifecycle_counts(engine: Engine) -> dict[str, int]:
    names = (
        "service_requests",
        "logical_operations",
        "integration_attempts",
        "attempt_callback_credentials",
        "ai_interpretations",
        "audit_events",
        "outbox_messages",
        "command_idempotency_records",
    )
    with engine.connect() as connection:
        return {
            name: connection.scalar(select(func.count()).select_from(Base.metadata.tables[name]))
            for name in names
        }


def test_success_callback_commits_interpretation_and_consumed_replay_is_read_only(
    lifecycle_context: LifecycleContext,
) -> None:
    running = _create_running_attempt(lifecycle_context, suffix="success")
    callback = _success_command(lifecycle_context.settings)
    service = AiAttemptCallbackService(lifecycle_context.session_factory)
    first = service.succeed(
        attempt_id=running.attempt_id,
        command=callback,
        raw_idempotency_key="callback-success-key-0001",
        canonical_body_hash=canonical_command_hash(callback),
        correlation_id=uuid.uuid4(),
        machine=lifecycle_context.machine,
        supplied_credential=running.callback_credential,
    )
    assert first.logical_http_status == 200 and first.is_replay is False
    assert first.safe_snapshot["result"]["attempt_state"] == "Succeeded"

    attempt = _row(lifecycle_context.engine, "integration_attempts", running.attempt_id)
    operation = _row(lifecycle_context.engine, "logical_operations", running.operation_id)
    request = _row(lifecycle_context.engine, "service_requests", running.request_id)
    interpretation_id = uuid.UUID(first.safe_snapshot["result"]["interpretation_id"])
    interpretation = _row(
        lifecycle_context.engine,
        "ai_interpretations",
        interpretation_id,
    )
    with lifecycle_context.engine.connect() as connection:
        credential = (
            connection.execute(
                select(Base.metadata.tables["attempt_callback_credentials"]).where(
                    Base.metadata.tables["attempt_callback_credentials"].c.integration_attempt_id
                    == running.attempt_id
                )
            )
            .mappings()
            .one()
        )
    assert attempt["state"] == "Succeeded" and attempt["version"] == 3
    assert attempt["completed_at"].tzinfo is not None
    assert operation["succeeded_attempt_id"] == running.attempt_id
    assert operation["version"] == 2
    assert request["status"] == "TriagePending" and request["version"] == 3
    assert request["current_interpretation_id"] == interpretation_id
    assert interpretation["producing_attempt_id"] == running.attempt_id
    assert interpretation["suggested_category"] == "Repair"
    assert credential["state"] == "Consumed" and credential["consumed_at"] is not None

    before_replay = _lifecycle_counts(lifecycle_context.engine)
    replay = service.succeed(
        attempt_id=running.attempt_id,
        command=callback,
        raw_idempotency_key="callback-success-key-0001",
        canonical_body_hash=canonical_command_hash(callback),
        correlation_id=uuid.uuid4(),
        machine=lifecycle_context.machine,
        supplied_credential=running.callback_credential,
    )
    assert replay.logical_http_status == 200 and replay.is_replay is True
    assert replay.command_id == first.command_id
    assert replay.safe_snapshot == first.safe_snapshot
    assert _lifecycle_counts(lifecycle_context.engine) == before_replay


def test_success_callback_http_surface_authenticates_and_replays_consumed_credential(
    lifecycle_context: LifecycleContext,
) -> None:
    running = _create_running_attempt(lifecycle_context, suffix="success-http")
    command_body = _success_command(lifecycle_context.settings).model_dump(mode="json")
    path = SUCCESS_CALLBACK_PATH.format(attempt_id=running.attempt_id)
    callback_headers = {
        "X-Attempt-Callback-Credential": running.callback_credential,
    }
    first = _signed_post(
        lifecycle_context,
        path,
        command_body,
        key="callback-success-http-key-0001",
        nonce="callback-success-http-nonce-0001",
        extra_headers=callback_headers,
    )
    assert first.status_code == 200, first.text
    assert first.json()["result"]["attempt_state"] == "Succeeded"
    assert first.json()["result"]["attempt_number"] == 1
    before_replay = _lifecycle_counts(lifecycle_context.engine)

    replay = _signed_post(
        lifecycle_context,
        path,
        command_body,
        key="callback-success-http-key-0001",
        nonce="callback-success-http-nonce-0002",
        extra_headers=callback_headers,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["command_id"] == first.json()["command_id"]
    assert replay.json()["result"] == first.json()["result"]
    assert replay.json()["versions"] == first.json()["versions"]
    assert _lifecycle_counts(lifecycle_context.engine) == before_replay


def test_retryable_failure_persists_policy_assessment_and_recovery_target(
    lifecycle_context: LifecycleContext,
) -> None:
    running = _create_running_attempt(lifecycle_context, suffix="retryable")
    callback = _retryable_command(lifecycle_context.settings)
    outcome = AiAttemptCallbackService(lifecycle_context.session_factory).retryable_failure(
        attempt_id=running.attempt_id,
        command=callback,
        raw_idempotency_key="callback-retryable-key-0001",
        canonical_body_hash=canonical_command_hash(callback),
        correlation_id=uuid.uuid4(),
        machine=lifecycle_context.machine,
        supplied_credential=running.callback_credential,
    )
    assert outcome.logical_http_status == 200 and outcome.is_replay is False
    assert outcome.safe_snapshot["result"]["recovery_disposition"] == "RetrySameOperation"

    attempt = _row(lifecycle_context.engine, "integration_attempts", running.attempt_id)
    request = _row(lifecycle_context.engine, "service_requests", running.request_id)
    assert attempt["state"] == "RetryableFailure" and attempt["version"] == 3
    assert attempt["sanitized_error_code"] == "PROVIDER_TIMEOUT"
    assert attempt["failure_policy_id"] == DEMO_FAILURE_RECOVERY_POLICY.id
    assert attempt["failure_policy_semantic_version"] == "1.0.0"
    assert attempt["failure_policy_revision"] == 1
    assert attempt["failure_policy_digest"] == DEMO_FAILURE_RECOVERY_POLICY.content_digest
    assert attempt["failure_stage"] == "ProviderProcessing"
    assert attempt["provider_invocation"] == "Invoked"
    assert attempt["customer_side_effect"] == "NotApplicable"
    assert attempt["recovery_disposition"] == "RetrySameOperation"
    assert attempt["maximum_attempts"] == 3 and attempt["remaining_attempts"] == 2
    assert attempt["next_eligible_at"] == attempt["assessed_at"] + timedelta(seconds=30)
    assert attempt["reconciliation_status"] == "NotRequired"
    assert attempt["terminal_reason"] is None
    assert request["status"] == "RetryableFailure" and request["version"] == 3
    assert request["current_queue"] == "FailedRetryRequired"
    assert request["recovery_target"] == "TriagePending"
    assert request["recovery_attempt_id"] == running.attempt_id
    assert request["failure_summary_code"] == "PROVIDER_TIMEOUT"
    assert request["terminal_at"] is None


def test_terminal_failure_persists_terminal_disposition_without_retry_time(
    lifecycle_context: LifecycleContext,
) -> None:
    running = _create_running_attempt(lifecycle_context, suffix="terminal")
    callback = _terminal_command(lifecycle_context.settings)
    outcome = AiAttemptCallbackService(lifecycle_context.session_factory).terminal_failure(
        attempt_id=running.attempt_id,
        command=callback,
        raw_idempotency_key="callback-terminal-key-0001",
        canonical_body_hash=canonical_command_hash(callback),
        correlation_id=uuid.uuid4(),
        machine=lifecycle_context.machine,
        supplied_credential=running.callback_credential,
    )
    assert outcome.logical_http_status == 200 and outcome.is_replay is False
    assert outcome.safe_snapshot["result"]["attempt_state"] == "TerminalFailure"
    assert outcome.safe_snapshot["result"]["recovery_disposition"] == "Terminal"

    attempt = _row(lifecycle_context.engine, "integration_attempts", running.attempt_id)
    request = _row(lifecycle_context.engine, "service_requests", running.request_id)
    assert attempt["state"] == "TerminalFailure" and attempt["version"] == 3
    assert attempt["sanitized_error_code"] == "PROVIDER_AUTHENTICATION_FAILED"
    assert attempt["failure_stage"] == "Dispatch"
    assert attempt["provider_invocation"] == "NotInvoked"
    assert attempt["recovery_disposition"] == "Terminal"
    assert attempt["maximum_attempts"] == 3 and attempt["remaining_attempts"] == 2
    assert attempt["next_eligible_at"] is None
    assert attempt["terminal_reason"] == "PROVIDER_AUTHENTICATION_FAILED"
    assert request["status"] == "TerminalFailure" and request["version"] == 3
    assert request["current_queue"] is None and request["recovery_target"] is None
    assert request["recovery_attempt_id"] == running.attempt_id
    assert request["terminal_at"] == attempt["completed_at"]


def test_retry_creation_is_eligible_at_persisted_database_boundary(
    lifecycle_context: LifecycleContext,
) -> None:
    running = _create_running_attempt(lifecycle_context, suffix="retry-boundary")
    callback = _retryable_command(lifecycle_context.settings)
    failure = AiAttemptCallbackService(lifecycle_context.session_factory).retryable_failure(
        attempt_id=running.attempt_id,
        command=callback,
        raw_idempotency_key="callback-retry-boundary-key-0001",
        canonical_body_hash=canonical_command_hash(callback),
        correlation_id=uuid.uuid4(),
        machine=lifecycle_context.machine,
        supplied_credential=running.callback_credential,
    )
    assert failure.logical_http_status == 200

    attempts = Base.metadata.tables["integration_attempts"]
    with lifecycle_context.engine.begin() as connection:
        boundary = connection.scalar(select(func.now()))
        connection.execute(
            update(attempts)
            .where(attempts.c.id == running.attempt_id)
            .values(next_eligible_at=boundary)
        )

    policy = DEMO_FAILURE_RECOVERY_POLICY.identity
    retry = RetryAiRequest.model_validate(
        {
            "schema_version": "1.0",
            "expected_versions": {"service_request": 3},
            "command": {
                "failed_attempt_id": str(running.attempt_id),
                "expected_failure_policy": {
                    "policy_id": str(policy.policy_id),
                    "semantic_version": policy.semantic_version,
                    "revision": policy.revision,
                    "content_digest": policy.content_digest,
                },
            },
        }
    )
    outcome = RetryAiService(
        lifecycle_context.session_factory,
        lifecycle_context.settings,
        lifecycle_context.generator,
    ).execute(
        request_id=running.request_id,
        command=retry,
        raw_idempotency_key="retry-ai-boundary-key-0001",
        canonical_body_hash=canonical_command_hash(retry),
        correlation_id=uuid.uuid4(),
        authority=lifecycle_context.machine,
    )
    assert outcome.logical_http_status == 202 and outcome.is_replay is False
    assert outcome.callback_plaintext == "B" * 43
    result = outcome.safe_snapshot["result"]
    new_attempt_id = uuid.UUID(result["integration_attempt_id"])
    assert result["failed_attempt_id"] == str(running.attempt_id)
    assert result["attempt_number"] == 2 and result["attempt_state"] == "Pending"

    failed = _row(lifecycle_context.engine, "integration_attempts", running.attempt_id)
    created = _row(lifecycle_context.engine, "integration_attempts", new_attempt_id)
    request = _row(lifecycle_context.engine, "service_requests", running.request_id)
    operation = _row(lifecycle_context.engine, "logical_operations", running.operation_id)
    assert failed["state"] == "RetryableFailure"
    assert created["state"] == "Pending" and created["version"] == 1
    assert created["attempt_number"] == 2
    assert created["logical_operation_id"] == running.operation_id
    assert request["status"] == "TriagePending" and request["version"] == 4
    assert request["current_queue"] is None
    assert request["recovery_target"] is None and request["recovery_attempt_id"] is None
    assert operation["version"] == 3
    with lifecycle_context.engine.connect() as connection:
        active_credential = (
            connection.execute(
                select(Base.metadata.tables["attempt_callback_credentials"]).where(
                    Base.metadata.tables["attempt_callback_credentials"].c.integration_attempt_id
                    == new_attempt_id
                )
            )
            .mappings()
            .one()
        )
    assert active_credential["state"] == "Active"
    assert active_credential["credential_hash"] == hashlib.sha256(b"B" * 43).hexdigest()


def test_workflow_service_retry_http_returns_plaintext_once(
    lifecycle_context: LifecycleContext,
) -> None:
    running = _create_running_attempt(lifecycle_context, suffix="retry-http")
    callback = _retryable_command(lifecycle_context.settings)
    failure = AiAttemptCallbackService(lifecycle_context.session_factory).retryable_failure(
        attempt_id=running.attempt_id,
        command=callback,
        raw_idempotency_key="callback-retry-http-key-0001",
        canonical_body_hash=canonical_command_hash(callback),
        correlation_id=uuid.uuid4(),
        machine=lifecycle_context.machine,
        supplied_credential=running.callback_credential,
    )
    assert failure.logical_http_status == 200
    attempts = Base.metadata.tables["integration_attempts"]
    with lifecycle_context.engine.begin() as connection:
        connection.execute(
            update(attempts)
            .where(attempts.c.id == running.attempt_id)
            .values(next_eligible_at=func.now())
        )
    policy = DEMO_FAILURE_RECOVERY_POLICY.identity
    payload = {
        "schema_version": "1.0",
        "expected_versions": {"service_request": 3},
        "command": {
            "failed_attempt_id": str(running.attempt_id),
            "expected_failure_policy": {
                "policy_id": str(policy.policy_id),
                "semantic_version": policy.semantic_version,
                "revision": policy.revision,
                "content_digest": policy.content_digest,
            },
        },
    }
    path = RETRY_AI_PATH.format(request_id=running.request_id)
    first = _signed_post(
        lifecycle_context,
        path,
        payload,
        key="retry-ai-http-key-0001",
        nonce="retry-ai-http-nonce-0001",
    )
    assert first.status_code == 202, first.text
    assert first.json()["result"]["credential_delivery"] == "PlaintextIssued"
    assert first.json()["result"]["callback_credential"] == "B" * 43
    replay = _signed_post(
        lifecycle_context,
        path,
        payload,
        key="retry-ai-http-key-0001",
        nonce="retry-ai-http-nonce-0002",
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["command_id"] == first.json()["command_id"]
    assert replay.json()["result"]["credential_delivery"] == "AlreadyIssued"
    assert "callback_credential" not in replay.json()["result"]
