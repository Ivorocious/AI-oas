"""Production-service PostgreSQL scenarios for atomic deterministic triage."""

import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import Engine, func, insert, select, text, update

import ai_operations_automation.triage.service as triage_service_module
from ai_operations_automation.config import Settings
from ai_operations_automation.db import (
    Base,
    create_database_engine,
    create_session_factory,
)
from ai_operations_automation.deterministic_decision import (
    DEMO_DECISION_POLICY,
    DecisionEvaluationInput,
    canonical_decision_input_hash,
)
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.triage.models import (
    AuthoritativeDecisionFacts,
    CompleteTriageCommand,
    ExpectedDecisionPolicy,
)
from ai_operations_automation.triage.service import CompleteTriageService
from alembic import command

pytestmark = pytest.mark.integration
BACKEND_ROOT = Path(__file__).resolve().parents[2]
SEEDED_POLICY_TABLES = {
    "decision_policy_versions",
    "failure_recovery_policy_versions",
}
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def alembic_config() -> Config:
    return Config(str(BACKEND_ROOT / "alembic.ini"))


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    command.upgrade(alembic_config(), "head")
    value = create_database_engine(Settings(_env_file=None).database_url)
    yield value
    value.dispose()


@pytest.fixture(autouse=True)
def clean_operational_rows(engine: Engine) -> Iterator[None]:
    names = [name for name in Base.metadata.tables if name not in SEEDED_POLICY_TABLES]
    quoted = ", ".join(f'"{name}"' for name in names)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {quoted} CASCADE"))
    yield


@pytest.fixture
def service(engine: Engine) -> CompleteTriageService:
    return CompleteTriageService(create_session_factory(engine))


def _insert_request_with_interpretation(
    engine: Engine,
    *,
    description: str,
    suggested_category: str,
    confidence: str = "0.8800",
    missing_information: list[str] | None = None,
    warnings: list[str] | None = None,
    email: str | None = None,
    phone: str | None = None,
    location: str = "site:main",
    timing_preference: str = "Deadline provided",
    category: str | None = None,
) -> dict[str, uuid.UUID]:
    ids = {
        "contact": uuid.uuid4(),
        "delivery": uuid.uuid4(),
        "request": uuid.uuid4(),
        "operation": uuid.uuid4(),
        "attempt": uuid.uuid4(),
        "interpretation": uuid.uuid4(),
    }
    now = datetime.now(UTC)
    tables = Base.metadata.tables
    with engine.begin() as connection:
        connection.execute(
            insert(tables["contacts"]).values(
                id=ids["contact"],
                display_label="Deterministic triage fixture",
                normalized_email=email,
                normalized_phone=phone,
                version=1,
            )
        )
        connection.execute(
            insert(tables["inbound_deliveries"]).values(
                id=ids["delivery"],
                scope=f"triage-lifecycle-{uuid.uuid4()}",
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
                normalized_request_description=description,
                status="TriagePending",
                version=1,
                category=category,
                location_context=location,
                timing_preference=timing_preference,
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
                attempt_number=1,
                state="Succeeded",
                version=1,
                adapter_name="test-adapter",
                adapter_version="1.0",
                assigned_workflow_service="workflow-triage-test",
                workflow_environment="integration",
                callback_authorization_deadline=now + timedelta(hours=1),
                created_at=now - timedelta(seconds=1),
                updated_at=now,
                started_at=now,
                completed_at=now,
                result_hash=HASH_C,
            )
        )
        connection.execute(
            insert(tables["ai_interpretations"]).values(
                id=ids["interpretation"],
                service_request_id=ids["request"],
                logical_operation_id=ids["operation"],
                producing_attempt_id=ids["attempt"],
                interpretation_number=1,
                summary="Bounded advisory interpretation.",
                suggested_category=suggested_category,
                missing_information=missing_information or [],
                confidence=Decimal(confidence),
                input_hash=HASH_A,
                configuration_hash=HASH_B,
                result_schema_version="interpretation-v1",
                prompt_version="prompt-v1",
                provider_name="test-provider",
                model_name="test-model",
                adapter_name="test-adapter",
                adapter_version="1.0",
                warnings=warnings or [],
            )
        )
        connection.execute(
            update(tables["service_requests"])
            .where(tables["service_requests"].c.id == ids["request"])
            .values(current_interpretation_id=ids["interpretation"])
        )
        connection.execute(
            update(tables["logical_operations"])
            .where(tables["logical_operations"].c.id == ids["operation"])
            .values(succeeded_attempt_id=ids["attempt"])
        )
    return ids


def _expected_policy() -> ExpectedDecisionPolicy:
    return ExpectedDecisionPolicy(
        policy_key=DEMO_DECISION_POLICY.policy_key,
        semantic_version=DEMO_DECISION_POLICY.semantic_version,
        revision=DEMO_DECISION_POLICY.revision,
    )


def _command(
    facts: AuthoritativeDecisionFacts,
    *,
    expected_version: int = 1,
    expected_policy: ExpectedDecisionPolicy | None = None,
) -> CompleteTriageCommand:
    return CompleteTriageCommand(
        expected_service_request_version=expected_version,
        facts=facts,
        expected_policy=expected_policy or _expected_policy(),
    )


def _repair_facts(**changes: object) -> AuthoritativeDecisionFacts:
    values: dict[str, object] = {
        "explicit_category": "Repair",
        "requested_deadline": datetime.now(UTC) + timedelta(days=10),
        "service_mode": "OnSite",
        "access_constraints_known": True,
        "repair_symptoms_present": True,
        "repair_asset_context_present": True,
    }
    values.update(changes)
    return AuthoritativeDecisionFacts(**values)


def _execute(
    service: CompleteTriageService,
    request_id: uuid.UUID,
    command_body: CompleteTriageCommand,
    key: str,
):
    return service.execute(
        request_id=request_id,
        command=command_body,
        durable_command_key=key,
        correlation_id=uuid.uuid4(),
    )


def _row_count(engine: Engine, table_name: str) -> int:
    table = Base.metadata.tables[table_name]
    with engine.connect() as connection:
        return int(connection.scalar(select(func.count()).select_from(table)) or 0)


def _assert_triage_outbox(engine: Engine, outcome_event: str) -> None:
    table = Base.metadata.tables["outbox_messages"]
    with engine.connect() as connection:
        event_types = list(connection.scalars(select(table.c.event_type)).all())

    assert sorted(event_types) == sorted(
        [
            "service_request.triage_completed",
            outcome_event,
            "service_request.queue_changed",
        ]
    )


def test_standard_low_ready_result_is_atomic_and_reproducible(
    engine: Engine,
    service: CompleteTriageService,
) -> None:
    ids = _insert_request_with_interpretation(
        engine,
        description="Schedule routine filter and ventilation maintenance.",
        suggested_category="RoutineMaintenance",
        email="standard@example.test",
    )
    facts = AuthoritativeDecisionFacts(
        explicit_category="RoutineMaintenance",
        timing_is_flexible=True,
        requested_deadline=datetime.now(UTC) + timedelta(days=28),
        service_mode="OnSite",
        access_constraints_known=True,
        maintenance_asset_context_present=True,
    )
    correlation_id = uuid.uuid4()
    outcome = service.execute(
        request_id=ids["request"],
        command=_command(facts),
        durable_command_key="triage-standard-low",
        correlation_id=correlation_id,
    )

    assert outcome.logical_http_status == 200
    assert not outcome.is_replay
    result = outcome.safe_snapshot["result"]
    assert result["category"] == "RoutineMaintenance"
    assert result["priority"] == "Low"
    assert result["status"] == "ReadyForAction"
    assert result["queue"] == "StandardRequests"
    assert result["review_required"] is False
    assert outcome.safe_snapshot["versions"] == {"service_request": 2}

    tables = Base.metadata.tables
    with engine.connect() as connection:
        request = (
            connection.execute(
                select(tables["service_requests"]).where(
                    tables["service_requests"].c.id == ids["request"]
                )
            )
            .mappings()
            .one()
        )
        decision = connection.execute(select(tables["routing_decisions"])).mappings().one()
        command_row = (
            connection.execute(select(tables["command_idempotency_records"])).mappings().one()
        )
        audits = (
            connection.execute(
                select(tables["audit_events"]).order_by(tables["audit_events"].c.event_name)
            )
            .mappings()
            .all()
        )
        outbox = (
            connection.execute(
                select(tables["outbox_messages"]).order_by(tables["outbox_messages"].c.event_type)
            )
            .mappings()
            .all()
        )

    assert request["version"] == 2
    assert request["current_routing_decision_id"] == decision["id"]
    assert (request["category"], request["priority"]) == ("RoutineMaintenance", "Low")
    assert (request["status"], request["current_queue"]) == (
        "ReadyForAction",
        "StandardRequests",
    )
    assert request["review_required"] is False
    assert request["review_reason_codes"] == []
    assert decision["policy_id"] == DEMO_DECISION_POLICY.id
    assert decision["policy_semantic_version"] == "1.0.0"
    assert decision["policy_revision"] == 1
    assert decision["policy_digest"] == DEMO_DECISION_POLICY.content_digest
    reconstructed = DecisionEvaluationInput.model_validate(decision["canonical_input_snapshot"])
    assert decision["canonical_input_hash"] == canonical_decision_input_hash(reconstructed)
    assert command_row["command_id"] == outcome.command_id
    assert command_row["status"] == "Completed"
    assert {item["event_name"] for item in audits} == {
        "routing_decision.created",
        "service_request.queue_changed",
        "service_request.triage_completed",
    }
    assert {item["event_type"] for item in outbox} == {
        "service_request.queue_changed",
        "service_request.ready_for_action",
        "service_request.triage_completed",
    }
    assert len(outbox) == 3
    assert all(item["correlation_id"] == correlation_id for item in (*audits, *outbox))
    assert all(item["command_id"] == outcome.command_id for item in audits)


def test_high_priority_request_routes_to_priority_queue(
    engine: Engine,
    service: CompleteTriageService,
) -> None:
    ids = _insert_request_with_interpretation(
        engine,
        description="Repair the interrupted ventilation system.",
        suggested_category="Repair",
        confidence="0.9100",
        email="high@example.test",
    )
    facts = _repair_facts(
        requested_deadline=datetime.now(UTC) + timedelta(hours=48),
        service_interruption="Active",
    )

    outcome = _execute(service, ids["request"], _command(facts), "triage-high")

    result = outcome.safe_snapshot["result"]
    assert (result["priority"], result["status"], result["queue"]) == (
        "High",
        "ReadyForAction",
        "PriorityRequests",
    )
    with engine.connect() as connection:
        decision = (
            connection.execute(select(Base.metadata.tables["routing_decisions"])).mappings().one()
        )
    assert "PRIORITY_ACTIVE_INTERRUPTION" in decision["priority_reason_codes"]
    assert decision["review_reason_codes"] == []
    _assert_triage_outbox(engine, "service_request.ready_for_action")


@pytest.mark.parametrize(
    ("case", "expected_priority", "expected_review", "expected_missing"),
    [
        ("urgent", "Urgent", "REVIEW_URGENT_PRIORITY", None),
        (
            "missing",
            "Normal",
            "REVIEW_MISSING_REQUIRED_INFORMATION",
            "MISSING_INSTALLATION_TARGET",
        ),
        ("low-confidence", "Normal", "REVIEW_LOW_AI_CONFIDENCE", None),
    ],
)
def test_review_scenarios_persist_complete_current_decisions(
    case: str,
    expected_priority: str,
    expected_review: str,
    expected_missing: str | None,
    engine: Engine,
    service: CompleteTriageService,
) -> None:
    if case == "urgent":
        suggested = "Repair"
        confidence = "0.9000"
        facts = _repair_facts(
            requested_deadline=datetime.now(UTC) + timedelta(hours=23),
            service_interruption="Active",
        )
    elif case == "missing":
        suggested = "Installation"
        confidence = "0.8300"
        facts = AuthoritativeDecisionFacts(
            explicit_category="Installation",
            requested_deadline=datetime.now(UTC) + timedelta(days=10),
            service_mode="OnSite",
            access_constraints_known=True,
            installation_scope_present=True,
        )
    else:
        suggested = "Inspection"
        confidence = "0.7400"
        facts = AuthoritativeDecisionFacts(
            explicit_category="Inspection",
            requested_deadline=datetime.now(UTC) + timedelta(days=10),
            service_mode="OnSite",
            access_constraints_known=True,
            inspection_subject_present=True,
            inspection_purpose_present=True,
        )
    ids = _insert_request_with_interpretation(
        engine,
        description=f"{case} deterministic triage case.",
        suggested_category=suggested,
        confidence=confidence,
        email=f"{case}@example.test",
    )

    outcome = _execute(service, ids["request"], _command(facts), f"triage-{case}")

    result = outcome.safe_snapshot["result"]
    assert result["priority"] == expected_priority
    assert (result["status"], result["queue"], result["review_required"]) == (
        "HumanReview",
        "HumanReview",
        True,
    )
    assert expected_review in result["review_reason_codes"]
    with engine.connect() as connection:
        decision = (
            connection.execute(select(Base.metadata.tables["routing_decisions"])).mappings().one()
        )
        request = (
            connection.execute(
                select(Base.metadata.tables["service_requests"]).where(
                    Base.metadata.tables["service_requests"].c.id == ids["request"]
                )
            )
            .mappings()
            .one()
        )
    assert expected_review in decision["review_reason_codes"]
    assert request["review_reason_codes"] == decision["review_reason_codes"]
    if expected_missing is not None:
        assert expected_missing in decision["missing_information_codes"]
    _assert_triage_outbox(engine, "service_request.human_review_required")


def test_retention_threshold_candidate_is_inspectable_without_forcing_review(
    engine: Engine,
    service: CompleteTriageService,
) -> None:
    _insert_request_with_interpretation(
        engine,
        description="repair alpha beta gamma delta extra",
        suggested_category="Repair",
        email="threshold-candidate@example.test",
        location="site:candidate",
        category="Repair",
    )
    source = _insert_request_with_interpretation(
        engine,
        description="repair alpha beta gamma delta",
        suggested_category="Repair",
        email="threshold-source@example.test",
        location="site:source",
    )
    command_body = _command(_repair_facts())

    first = _execute(service, source["request"], command_body, "triage-threshold")
    counts_before_replay = {
        name: _row_count(engine, name)
        for name in (
            "duplicate_candidates",
            "routing_decisions",
            "audit_events",
            "outbox_messages",
        )
    }
    replay = _execute(service, source["request"], command_body, "triage-threshold")

    assert first.safe_snapshot["result"]["status"] == "ReadyForAction"
    assert first.safe_snapshot["result"]["review_required"] is False
    assert replay.is_replay
    assert replay.command_id == first.command_id
    assert replay.safe_snapshot == first.safe_snapshot
    assert {name: _row_count(engine, name) for name in counts_before_replay} == counts_before_replay
    with engine.connect() as connection:
        candidate = (
            connection.execute(select(Base.metadata.tables["duplicate_candidates"]))
            .mappings()
            .one()
        )
        link = (
            connection.execute(
                select(Base.metadata.tables["routing_decision_duplicate_candidates"])
            )
            .mappings()
            .one()
        )
    assert candidate["deterministic_score"] == 40
    assert candidate["resolution_status"] == "Pending"
    assert link["evidence_role"] == "CurrentPending"
    _assert_triage_outbox(engine, "service_request.ready_for_action")


def test_standalone_contact_candidate_can_trigger_duplicate_review(
    engine: Engine,
    service: CompleteTriageService,
) -> None:
    source = _insert_request_with_interpretation(
        engine,
        description="Current repair request with a matching standalone contact.",
        suggested_category="Repair",
        email="standalone-contact-match@example.test",
        location="site:current",
    )
    standalone_contact_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            insert(Base.metadata.tables["contacts"]).values(
                id=standalone_contact_id,
                display_label="Eligible standalone contact",
                normalized_email="standalone-contact-match@example.test",
                version=1,
            )
        )

    outcome = _execute(
        service,
        source["request"],
        _command(_repair_facts()),
        "triage-standalone-contact",
    )

    result = outcome.safe_snapshot["result"]
    assert (result["status"], result["queue"]) == (
        "DuplicateReview",
        "DuplicateReview",
    )
    assert result["review_reason_codes"] == ["REVIEW_POSSIBLE_DUPLICATE"]
    with engine.connect() as connection:
        candidates = (
            connection.execute(select(Base.metadata.tables["duplicate_candidates"]))
            .mappings()
            .all()
        )
        link = (
            connection.execute(
                select(Base.metadata.tables["routing_decision_duplicate_candidates"])
            )
            .mappings()
            .one()
        )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["candidate_type"] == "Contact"
    assert candidate["candidate_contact_id"] == standalone_contact_id
    assert candidate["candidate_contact_id"] != source["contact"]
    assert candidate["candidate_service_request_id"] is None
    assert candidate["deterministic_score"] == 70
    assert "DUPLICATE_EXACT_EMAIL" in candidate["reason_codes"]
    assert link["duplicate_candidate_id"] == candidate["id"]
    assert link["evidence_role"] == "CurrentPending"
    _assert_triage_outbox(engine, "service_request.duplicate_review_required")


def test_material_duplicate_precedes_urgent_human_review(
    engine: Engine,
    service: CompleteTriageService,
) -> None:
    _insert_request_with_interpretation(
        engine,
        description="Earlier request with the same verified contact.",
        suggested_category="Repair",
        email="duplicate@example.test",
        location="site:earlier",
        category="Repair",
    )
    source = _insert_request_with_interpretation(
        engine,
        description="Current urgent request with distinct description.",
        suggested_category="Repair",
        confidence="0.9000",
        email="duplicate@example.test",
        location="site:current",
    )
    facts = _repair_facts(
        requested_deadline=datetime.now(UTC) + timedelta(hours=23),
        service_interruption="Active",
    )

    outcome = _execute(service, source["request"], _command(facts), "triage-duplicate")

    result = outcome.safe_snapshot["result"]
    assert result["priority"] == "Urgent"
    assert (result["status"], result["queue"]) == (
        "DuplicateReview",
        "DuplicateReview",
    )
    assert result["review_reason_codes"] == ["REVIEW_POSSIBLE_DUPLICATE"]
    with engine.connect() as connection:
        candidate = (
            connection.execute(select(Base.metadata.tables["duplicate_candidates"]))
            .mappings()
            .one()
        )
    assert candidate["deterministic_score"] >= 60
    assert "DUPLICATE_EXACT_EMAIL" in candidate["reason_codes"]
    _assert_triage_outbox(engine, "service_request.duplicate_review_required")


def test_changed_evidence_stales_and_supersedes_prior_candidate(
    engine: Engine,
    service: CompleteTriageService,
) -> None:
    _insert_request_with_interpretation(
        engine,
        description="repair alpha beta gamma delta extra",
        suggested_category="Repair",
        email="history-candidate@example.test",
        location="site:candidate",
        category="Repair",
    )
    source = _insert_request_with_interpretation(
        engine,
        description="repair alpha beta gamma delta",
        suggested_category="Repair",
        email="history-source@example.test",
        location="site:source",
    )
    facts = _repair_facts()
    first = _execute(service, source["request"], _command(facts), "triage-history-first")
    first_decision_id = uuid.UUID(first.safe_snapshot["result"]["routing_decision_id"])
    with engine.connect() as connection:
        old_candidate_id = connection.scalar(
            select(Base.metadata.tables["duplicate_candidates"].c.id)
        )
    assert old_candidate_id is not None

    with engine.begin() as connection:
        connection.execute(
            update(Base.metadata.tables["contacts"])
            .where(Base.metadata.tables["contacts"].c.id == source["contact"])
            .values(normalized_email="history-source-updated@example.test", version=2)
        )
        connection.execute(
            update(Base.metadata.tables["service_requests"])
            .where(Base.metadata.tables["service_requests"].c.id == source["request"])
            .values(status="TriagePending", current_queue=None, version=3)
        )

    second = _execute(
        service,
        source["request"],
        _command(facts, expected_version=3),
        "triage-history-second",
    )

    assert second.logical_http_status == 200
    tables = Base.metadata.tables
    with engine.connect() as connection:
        candidates = (
            connection.execute(
                select(tables["duplicate_candidates"]).order_by(
                    tables["duplicate_candidates"].c.stale_at.desc().nulls_last()
                )
            )
            .mappings()
            .all()
        )
        second_decision = (
            connection.execute(
                select(tables["routing_decisions"]).where(
                    tables["routing_decisions"].c.decision_number == 2
                )
            )
            .mappings()
            .one()
        )
        links = (
            connection.execute(
                select(tables["routing_decision_duplicate_candidates"]).where(
                    tables["routing_decision_duplicate_candidates"].c.routing_decision_id
                    == second_decision["id"]
                )
            )
            .mappings()
            .all()
        )
    assert len(candidates) == 2
    old = next(item for item in candidates if item["id"] == old_candidate_id)
    new = next(item for item in candidates if item["id"] != old_candidate_id)
    assert old["stale_at"] is not None
    assert old["superseded_by_candidate_id"] == new["id"]
    assert new["stale_at"] is None
    assert second_decision["prior_decision_id"] == first_decision_id
    assert {item["evidence_role"] for item in links} == {
        "CurrentPending",
        "StaleHistorical",
    }


def test_stale_version_evidence_and_policy_guards_write_no_domain_evidence(
    engine: Engine,
    service: CompleteTriageService,
) -> None:
    stale_version = _insert_request_with_interpretation(
        engine,
        description="Stale request version fixture.",
        suggested_category="Repair",
        email="version@example.test",
    )
    stale_evidence = _insert_request_with_interpretation(
        engine,
        description="Stale interpretation evidence fixture.",
        suggested_category="Repair",
        email="evidence@example.test",
    )
    stale_policy = _insert_request_with_interpretation(
        engine,
        description="Stale policy fixture.",
        suggested_category="Repair",
        email="policy@example.test",
    )
    with engine.begin() as connection:
        connection.execute(
            update(Base.metadata.tables["service_requests"])
            .where(Base.metadata.tables["service_requests"].c.id == stale_evidence["request"])
            .values(current_interpretation_id=None)
        )

    version_outcome = _execute(
        service,
        stale_version["request"],
        _command(_repair_facts(), expected_version=2),
        "triage-stale-version",
    )
    evidence_outcome = _execute(
        service,
        stale_evidence["request"],
        _command(_repair_facts()),
        "triage-stale-evidence",
    )
    policy_outcome = _execute(
        service,
        stale_policy["request"],
        _command(
            _repair_facts(),
            expected_policy=ExpectedDecisionPolicy(
                policy_key="general-service-demo",
                semantic_version="1.0.0",
                revision=2,
            ),
        ),
        "triage-stale-policy",
    )

    assert version_outcome.safe_snapshot["error"]["code"] == "CONCURRENCY_CONFLICT"
    assert evidence_outcome.safe_snapshot["error"]["code"] == "TRIAGE_EVIDENCE_STALE"
    assert policy_outcome.safe_snapshot["error"]["code"] == "POLICY_VERSION_CONFLICT"
    assert _row_count(engine, "command_idempotency_records") == 3
    for table_name in (
        "routing_decisions",
        "duplicate_candidates",
        "audit_events",
        "outbox_messages",
    ):
        assert _row_count(engine, table_name) == 0


def test_forced_event_failure_rolls_back_request_decision_command_and_events(
    engine: Engine,
    service: CompleteTriageService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = _insert_request_with_interpretation(
        engine,
        description="Forced deterministic triage rollback.",
        suggested_category="Repair",
        email="rollback@example.test",
    )

    def fail_event(*args, **kwargs):
        raise RuntimeError("forced triage event failure")

    monkeypatch.setattr(
        triage_service_module,
        "write_audit_and_optional_outbox",
        fail_event,
    )
    with pytest.raises(IntakeError) as captured:
        _execute(service, ids["request"], _command(_repair_facts()), "triage-rollback")

    assert captured.value.status_code == 500
    assert captured.value.code == "INTERNAL_ERROR"
    for table_name in (
        "routing_decisions",
        "duplicate_candidates",
        "routing_decision_duplicate_candidates",
        "command_idempotency_records",
        "audit_events",
        "outbox_messages",
    ):
        assert _row_count(engine, table_name) == 0
    with engine.connect() as connection:
        request = (
            connection.execute(
                select(Base.metadata.tables["service_requests"]).where(
                    Base.metadata.tables["service_requests"].c.id == ids["request"]
                )
            )
            .mappings()
            .one()
        )
    assert request["version"] == 1
    assert request["status"] == "TriagePending"
    assert request["current_routing_decision_id"] is None


def test_concurrent_commands_create_one_current_decision(
    engine: Engine,
    service: CompleteTriageService,
) -> None:
    ids = _insert_request_with_interpretation(
        engine,
        description="Concurrent deterministic triage fixture.",
        suggested_category="Repair",
        email="concurrent@example.test",
    )
    command_body = _command(_repair_facts())

    def execute(key: str):
        return _execute(service, ids["request"], command_body, key)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(execute, ("triage-concurrent-a", "triage-concurrent-b")))

    assert sorted(item.logical_http_status for item in outcomes) == [200, 409]
    conflict = next(item for item in outcomes if item.logical_http_status == 409)
    assert conflict.safe_snapshot["error"]["code"] == "CONCURRENCY_CONFLICT"
    assert _row_count(engine, "routing_decisions") == 1
    assert _row_count(engine, "command_idempotency_records") == 2
    with engine.connect() as connection:
        request = (
            connection.execute(
                select(Base.metadata.tables["service_requests"]).where(
                    Base.metadata.tables["service_requests"].c.id == ids["request"]
                )
            )
            .mappings()
            .one()
        )
    assert request["version"] == 2
    assert request["current_routing_decision_id"] is not None
