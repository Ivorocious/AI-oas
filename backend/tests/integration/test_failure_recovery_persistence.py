"""PostgreSQL evidence for migration 0009 and recovery-assessment constraints."""

import hashlib
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import Engine, delete, insert, inspect, select, text, update
from sqlalchemy.exc import IntegrityError

from ai_operations_automation.config import Settings
from ai_operations_automation.db import Base, create_database_engine
from ai_operations_automation.failure_recovery import (
    DEMO_FAILURE_RECOVERY_POLICY,
    DEMO_POLICY_CONTENT,
    DEMO_POLICY_CONTENT_DIGEST,
    canonical_policy_bytes,
)
from alembic import command

pytestmark = pytest.mark.integration
BACKEND_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_HEAD = "0008_callback_command_authorization_binding"
CURRENT_HEAD = "0009_failure_recovery_foundation"
POLICY_TABLE = "failure_recovery_policy_versions"
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def alembic_config() -> Config:
    return Config(str(BACKEND_ROOT / "alembic.ini"))


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    command.downgrade(alembic_config(), PREVIOUS_HEAD)
    command.upgrade(alembic_config(), "head")
    value = create_database_engine(Settings(_env_file=None).database_url)
    yield value
    value.dispose()


@pytest.fixture(autouse=True)
def clean_operational_rows(engine: Engine) -> Iterator[None]:
    existing = set(inspect(engine).get_table_names())
    names = [name for name in Base.metadata.tables if name in existing and name != POLICY_TABLE]
    if names:
        tables = ", ".join(f'"{name}"' for name in names)
        with engine.begin() as connection:
            connection.execute(text(f"TRUNCATE {tables} CASCADE"))
    yield


def _insert_graph(engine: Engine, *, attempt_number: int = 1) -> dict[str, uuid.UUID]:
    ids = {
        "contact": uuid.uuid4(),
        "delivery": uuid.uuid4(),
        "request": uuid.uuid4(),
        "operation": uuid.uuid4(),
        "attempt": uuid.uuid4(),
    }
    tables = Base.metadata.tables
    with engine.begin() as connection:
        connection.execute(
            insert(tables["contacts"]).values(
                id=ids["contact"],
                display_label="Failure recovery fixture",
                version=1,
            )
        )
        connection.execute(
            insert(tables["inbound_deliveries"]).values(
                id=ids["delivery"],
                scope=f"failure-recovery-{uuid.uuid4()}",
                idempotency_key_digest=uuid.uuid4().hex,
                processing_status="Received",
                schema_version="1.0.0",
                version=1,
                correlation_id=uuid.uuid4(),
            )
        )
        connection.execute(
            insert(tables["service_requests"]).values(
                id=ids["request"],
                originating_delivery_id=ids["delivery"],
                contact_id=ids["contact"],
                normalized_request_description="Exercise deterministic recovery persistence.",
                status="TriagePending",
                version=1,
            )
        )
        connection.execute(
            insert(tables["logical_operations"]).values(
                id=ids["operation"],
                service_request_id=ids["request"],
                operation_kind="AIInterpretation",
                input_hash=HASH_A,
                configuration_hash=HASH_B,
                prompt_version="prompt-v1",
                result_schema_version="interpretation-v1",
                provider_name="test-provider",
                model_name="test-model",
                adapter_name="test-adapter",
                adapter_version="1.0",
                version=1,
            )
        )
        connection.execute(
            insert(tables["integration_attempts"]).values(
                id=ids["attempt"],
                logical_operation_id=ids["operation"],
                service_request_id=ids["request"],
                operation_kind="AIInterpretation",
                attempt_number=attempt_number,
                state="Pending",
                version=1,
                adapter_name="test-adapter",
                adapter_version="1.0",
                assigned_workflow_service="workflow-test",
                workflow_environment="integration",
                callback_authorization_deadline=datetime.now(UTC) + timedelta(hours=1),
            )
        )
    return ids


def _assessment_values(
    assessed_at: datetime,
    *,
    attempt_number: int = 1,
    terminal: bool = False,
) -> dict[str, object]:
    policy = DEMO_FAILURE_RECOVERY_POLICY
    failure_code = "PROVIDER_AUTHENTICATION_FAILED" if terminal else "PROVIDER_TIMEOUT"
    return {
        "state": "TerminalFailure" if terminal else "RetryableFailure",
        "version": 2,
        "completed_at": assessed_at,
        "sanitized_error_code": failure_code,
        "failure_policy_id": policy.id,
        "failure_policy_semantic_version": policy.semantic_version,
        "failure_policy_revision": policy.revision,
        "failure_policy_digest": policy.content_digest,
        "failure_stage": "Dispatch" if terminal else "ProviderProcessing",
        "provider_invocation": "NotInvoked" if terminal else "Invoked",
        "customer_side_effect": "NotApplicable",
        "recovery_disposition": "Terminal" if terminal else "RetrySameOperation",
        "maximum_attempts": 3,
        "remaining_attempts": 3 - attempt_number,
        "next_eligible_at": None if terminal else assessed_at + timedelta(seconds=30),
        "provider_retry_after_at": None,
        "reconciliation_status": "NotRequired",
        "reconciliation_deadline": None,
        "sanitized_evidence_reference": "failure-evidence-v1",
        "sanitized_evidence_hash": HASH_C,
        "terminal_reason": failure_code if terminal else None,
        "assessed_at": assessed_at,
    }


def _constraint_name(error: pytest.ExceptionInfo[IntegrityError]) -> str:
    return error.value.orig.diag.constraint_name


def test_0009_round_trip_preserves_existing_rows_and_reseeds_policy(engine: Engine) -> None:
    ids = _insert_graph(engine)
    engine.dispose()
    command.downgrade(alembic_config(), PREVIOUS_HEAD)
    try:
        inspector = inspect(engine)
        assert POLICY_TABLE not in inspector.get_table_names()
        assert "failure_policy_id" not in {
            column["name"] for column in inspector.get_columns("integration_attempts")
        }
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT id FROM integration_attempts WHERE id = :id"),
                    {"id": ids["attempt"]},
                )
                == ids["attempt"]
            )
    finally:
        command.upgrade(alembic_config(), "head")

    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT failure_policy_id, assessed_at FROM integration_attempts WHERE id = :id"),
            {"id": ids["attempt"]},
        ).one()
        assert tuple(row) == (None, None)
        assert connection.scalar(text("SELECT count(*) FROM failure_recovery_policy_versions")) == 1
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_HEAD


def test_seed_matches_the_frozen_runtime_policy(engine: Engine) -> None:
    table = Base.metadata.tables[POLICY_TABLE]
    with engine.connect() as connection:
        row = connection.execute(select(table)).mappings().one()

    expected = DEMO_POLICY_CONTENT.model_dump(mode="json")
    canonical = json.dumps(
        row["policy_snapshot"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert row["id"] == DEMO_FAILURE_RECOVERY_POLICY.id
    assert row["policy_key"] == "phase2-demonstration-failure-recovery"
    assert row["semantic_version"] == "1.0.0"
    assert row["revision"] == 1
    assert row["effective_at"] == datetime(2026, 7, 13, tzinfo=UTC)
    assert row["status"] == "Active"
    assert row["content_digest"] == DEMO_POLICY_CONTENT_DIGEST
    assert row["policy_snapshot"] == expected
    assert canonical == canonical_policy_bytes(DEMO_POLICY_CONTENT)
    assert hashlib.sha256(canonical).hexdigest() == DEMO_POLICY_CONTENT_DIGEST
    assert len(row["failure_code_catalog"]) == 26
    for field in (
        "operation_kind_rules",
        "failure_code_catalog",
        "attempt_budgets",
        "retry_delay_schedule",
        "stale_attempt_thresholds",
        "reconciliation_rules",
        "recovery_disposition_rules",
        "terminalization_rules",
    ):
        assert row[field] == expected[field]


def test_complete_retryable_ai_assessment_and_request_summary_insert(
    engine: Engine,
) -> None:
    ids = _insert_graph(engine)
    assessed_at = datetime.now(UTC)
    attempts = Base.metadata.tables["integration_attempts"]
    requests = Base.metadata.tables["service_requests"]
    with engine.begin() as connection:
        connection.execute(
            update(attempts)
            .where(attempts.c.id == ids["attempt"])
            .values(**_assessment_values(assessed_at))
        )
        connection.execute(
            update(requests)
            .where(requests.c.id == ids["request"])
            .values(
                status="RetryableFailure",
                version=2,
                current_queue="FailedRetryRequired",
                recovery_target="TriagePending",
                recovery_attempt_id=ids["attempt"],
                failure_summary_code="PROVIDER_TIMEOUT",
            )
        )

    with engine.connect() as connection:
        attempt = (
            connection.execute(select(attempts).where(attempts.c.id == ids["attempt"]))
            .mappings()
            .one()
        )
        request = (
            connection.execute(select(requests).where(requests.c.id == ids["request"]))
            .mappings()
            .one()
        )
    assert attempt["remaining_attempts"] == 2
    assert attempt["reconciliation_status"] == "NotRequired"
    assert request["recovery_target"] == "TriagePending"
    assert request["recovery_attempt_id"] == ids["attempt"]


@pytest.mark.parametrize(
    ("changes", "constraint"),
    [
        (
            {"failure_policy_digest": None},
            "ck_integration_attempts_recovery_assessment_complete",
        ),
        (
            {"customer_side_effect": "KnownNotApplied"},
            "ck_integration_attempts_ai_recovery_assessment_valid",
        ),
        (
            {"remaining_attempts": 0},
            "ck_integration_attempts_ai_recovery_assessment_valid",
        ),
        (
            {"next_eligible_at": None},
            "ck_integration_attempts_ai_recovery_assessment_valid",
        ),
        (
            {"reconciliation_status": None},
            "ck_integration_attempts_recovery_assessment_complete",
        ),
    ],
)
def test_incomplete_or_invalid_ai_assessments_are_rejected(
    engine: Engine,
    changes: dict[str, object],
    constraint: str,
) -> None:
    ids = _insert_graph(engine)
    attempts = Base.metadata.tables["integration_attempts"]
    values = _assessment_values(datetime.now(UTC)) | changes
    with pytest.raises(IntegrityError) as captured:
        with engine.begin() as connection:
            connection.execute(
                update(attempts).where(attempts.c.id == ids["attempt"]).values(**values)
            )
    assert _constraint_name(captured) == constraint


def test_assessment_identity_must_match_the_referenced_policy(engine: Engine) -> None:
    ids = _insert_graph(engine)
    attempts = Base.metadata.tables["integration_attempts"]
    values = _assessment_values(datetime.now(UTC)) | {"failure_policy_digest": HASH_A}
    with pytest.raises(IntegrityError) as captured:
        with engine.begin() as connection:
            connection.execute(
                update(attempts).where(attempts.c.id == ids["attempt"]).values(**values)
            )
    assert _constraint_name(captured) == "fk_attempt_failure_recovery_policy_identity"


def test_terminal_assessment_requires_terminal_request_summary(engine: Engine) -> None:
    ids = _insert_graph(engine)
    assessed_at = datetime.now(UTC)
    attempts = Base.metadata.tables["integration_attempts"]
    requests = Base.metadata.tables["service_requests"]
    with engine.begin() as connection:
        connection.execute(
            update(attempts)
            .where(attempts.c.id == ids["attempt"])
            .values(**_assessment_values(assessed_at, terminal=True))
        )
        connection.execute(
            update(requests)
            .where(requests.c.id == ids["request"])
            .values(
                status="TerminalFailure",
                version=2,
                recovery_attempt_id=ids["attempt"],
                failure_summary_code="PROVIDER_AUTHENTICATION_FAILED",
                terminal_at=assessed_at,
            )
        )

    with pytest.raises(IntegrityError) as captured:
        with engine.begin() as connection:
            connection.execute(
                update(requests)
                .where(requests.c.id == ids["request"])
                .values(status="RetryableFailure", terminal_at=None)
            )
    assert _constraint_name(captured) == "ck_service_requests_recovery_fields_consistent"


def test_policy_delete_is_restricted_after_assessment(engine: Engine) -> None:
    ids = _insert_graph(engine)
    attempts = Base.metadata.tables["integration_attempts"]
    policies = Base.metadata.tables[POLICY_TABLE]
    with engine.begin() as connection:
        connection.execute(
            update(attempts)
            .where(attempts.c.id == ids["attempt"])
            .values(**_assessment_values(datetime.now(UTC)))
        )

    with pytest.raises(IntegrityError) as captured:
        with engine.begin() as connection:
            connection.execute(
                delete(policies).where(policies.c.id == DEMO_FAILURE_RECOVERY_POLICY.id)
            )
    assert _constraint_name(captured) == "fk_attempt_failure_recovery_policy_identity"
