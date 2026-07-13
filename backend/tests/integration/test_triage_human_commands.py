"""PostgreSQL lifecycle evidence for duplicate resolution and human review."""

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, func, insert, inspect, select, text, update

from ai_operations_automation.app import create_app
from ai_operations_automation.config import Settings
from ai_operations_automation.db import Base, create_database_engine, create_session_factory
from ai_operations_automation.deterministic_decision import (
    DEMO_DECISION_POLICY,
    AIAdvisory,
    CandidateKind,
    DecisionEvaluationInput,
    DuplicateCandidateInput,
    NormalizedDecisionFacts,
    ServiceCategory,
    ServiceInterruption,
    ServiceMode,
    evaluate_decision,
)
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
RAW_CUSTOMER_DESCRIPTION = "Private customer narrative never emitted to evidence."
RAW_CUSTOMER_EMAIL = "private.customer@example.test"


class TokenVerifier:
    def verify(self, token: str) -> str:
        return token


@dataclass(frozen=True)
class PersistedDecisionGraph:
    request_id: uuid.UUID
    contact_id: uuid.UUID
    interpretation_id: uuid.UUID
    decision_id: uuid.UUID
    version: int
    candidate_id: uuid.UUID | None = None
    candidate_contact_id: uuid.UUID | None = None


def alembic_config() -> Config:
    return Config(str(BACKEND_ROOT / "alembic.ini"))


@pytest.fixture(scope="module")
def engine() -> Engine:
    command.upgrade(alembic_config(), "head")
    value = create_database_engine(Settings(_env_file=None).database_url)
    yield value
    value.dispose()


@pytest.fixture(autouse=True)
def clean_operational_rows(engine: Engine):
    existing = set(inspect(engine).get_table_names())
    names = [
        name
        for name in Base.metadata.tables
        if name in existing and name not in SEEDED_POLICY_TABLES
    ]
    if names:
        quoted = ", ".join(f'"{name}"' for name in names)
        with engine.begin() as connection:
            connection.execute(text(f"TRUNCATE {quoted} CASCADE"))
    _ensure_decision_policy(engine)
    yield


@pytest.fixture
def client(engine: Engine) -> TestClient:
    app = create_app(
        Settings(_env_file=None),
        create_session_factory(engine),
        jwt_verifier=TokenVerifier(),
    )
    return TestClient(app)


def _ensure_decision_policy(engine: Engine) -> None:
    table = Base.metadata.tables["decision_policy_versions"]
    policy = DEMO_DECISION_POLICY
    with engine.begin() as connection:
        if connection.scalar(select(func.count()).select_from(table)):
            return
        connection.execute(
            insert(table).values(
                id=policy.id,
                policy_key=policy.policy_key,
                semantic_version=policy.semantic_version,
                revision=policy.revision,
                content_digest=policy.content_digest,
                effective_at=policy.effective_at,
                status=policy.status.value,
                policy_snapshot=policy.content.model_dump(mode="json"),
            )
        )


def _grant(engine: Engine, subject: str, role: str) -> uuid.UUID:
    actor_id = uuid.uuid4()
    tables = Base.metadata.tables
    with engine.begin() as connection:
        connection.execute(
            insert(tables["application_actors"]).values(
                id=actor_id,
                supabase_subject=subject,
                display_label=f"{role} integration actor",
                status="Active",
                version=1,
            )
        )
        connection.execute(
            insert(tables["application_actor_role_assignments"]).values(
                id=uuid.uuid4(),
                actor_id=actor_id,
                role=role,
                assigned_by_actor_id=actor_id,
                effective_from=datetime.now(UTC) - timedelta(minutes=1),
                assignment_reason="human command integration test",
            )
        )
    return actor_id


def _complete_repair_facts(
    request_id: uuid.UUID,
    contact_id: uuid.UUID,
    now: datetime,
    **changes,
) -> NormalizedDecisionFacts:
    values = {
        "source_request_id": request_id,
        "explicit_category": ServiceCategory.REPAIR,
        "contact_method_present": True,
        "timing_preference_present": True,
        "requested_deadline": now + timedelta(days=10),
        "requested_service_date": (now + timedelta(days=10)).date(),
        "service_mode": ServiceMode.ON_SITE,
        "location_or_service_context_present": True,
        "access_constraints_known": True,
        "repair_symptoms_present": True,
        "repair_asset_context_present": True,
        "contact_id": contact_id,
        "normalized_email_digest": HASH_A,
        "normalized_phone_digest": HASH_B,
        "description_fingerprint": HASH_C,
        "description_token_digests": ("d" * 64, "e" * 64),
        "location_or_service_context_digest": "f" * 64,
    }
    values.update(changes)
    return NormalizedDecisionFacts.model_validate(values)


def _seed_decision_graph(engine: Engine, scenario: str) -> PersistedDecisionGraph:
    now = datetime.now(UTC)
    request_id = uuid.uuid4()
    contact_id = uuid.uuid4()
    delivery_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    interpretation_id = uuid.uuid4()
    decision_id = uuid.uuid4()
    candidate_id = uuid.uuid4() if scenario == "duplicate" else None
    candidate_contact_id = uuid.uuid4() if scenario == "duplicate" else None
    version = 4

    facts_changes: dict[str, object] = {}
    if scenario in {"missing", "incomplete"}:
        facts_changes["location_or_service_context_present"] = False
    if scenario == "incomplete":
        facts_changes["access_constraints_known"] = False
    if scenario == "urgent":
        facts_changes.update(
            requested_deadline=now + timedelta(hours=12),
            requested_service_date=now.date(),
            service_interruption=ServiceInterruption.ACTIVE,
        )
    facts = _complete_repair_facts(request_id, contact_id, now, **facts_changes)
    advisory = AIAdvisory(
        confidence=Decimal("0.90"),
        suggested_category=ServiceCategory.REPAIR,
    )
    candidates: tuple[DuplicateCandidateInput, ...] = ()
    if candidate_id is not None and candidate_contact_id is not None:
        candidates = (
            DuplicateCandidateInput(
                observation_id=candidate_id,
                candidate_kind=CandidateKind.CONTACT,
                candidate_id=candidate_contact_id,
                candidate_activity_at=now - timedelta(days=1),
                candidate_evidence_hash=HASH_B,
                contact_id=candidate_contact_id,
                normalized_email_digest=HASH_A,
                normalized_phone_digest=HASH_B,
            ),
        )
    decision_input = DecisionEvaluationInput(
        evaluation_at=now,
        normalized_facts=facts,
        interpretation_id=interpretation_id,
        interpretation_version=1,
        interpretation_evidence_hash=HASH_C,
        ai_advisory=advisory,
        duplicate_candidates=candidates,
    )
    evaluation = evaluate_decision(decision_input)
    expected_status = {
        "duplicate": "DuplicateReview",
        "missing": "HumanReview",
        "incomplete": "HumanReview",
        "urgent": "HumanReview",
    }[scenario]
    assert evaluation.final_status.value == expected_status

    tables = Base.metadata.tables
    policy = DEMO_DECISION_POLICY
    with engine.begin() as connection:
        connection.execute(
            insert(tables["contacts"]).values(
                id=contact_id,
                display_label="Private customer",
                normalized_email=RAW_CUSTOMER_EMAIL,
                version=1,
            )
        )
        if candidate_contact_id is not None:
            connection.execute(
                insert(tables["contacts"]).values(
                    id=candidate_contact_id,
                    display_label="Existing separate contact",
                    normalized_email="existing.contact@example.test",
                    version=1,
                )
            )
        connection.execute(
            insert(tables["inbound_deliveries"]).values(
                id=delivery_id,
                scope=f"human-command-{uuid.uuid4()}",
                idempotency_key_digest=uuid.uuid4().hex,
                processing_status="Received",
                schema_version="1.0.0",
                version=1,
                correlation_id=uuid.uuid4(),
            )
        )
        connection.execute(
            insert(tables["service_requests"]).values(
                id=request_id,
                originating_delivery_id=delivery_id,
                contact_id=contact_id,
                normalized_request_description=RAW_CUSTOMER_DESCRIPTION,
                status="TriagePending",
                version=version,
            )
        )
        connection.execute(
            insert(tables["logical_operations"]).values(
                id=operation_id,
                service_request_id=request_id,
                operation_kind="AIInterpretation",
                input_hash=HASH_A,
                configuration_hash=HASH_B,
                prompt_version="prompt-v1",
                result_schema_version="interpretation-v1",
                provider_name="integration-provider",
                model_name="integration-model",
                adapter_name="integration-adapter",
                adapter_version="1.0",
            )
        )
        connection.execute(
            insert(tables["integration_attempts"]).values(
                id=attempt_id,
                logical_operation_id=operation_id,
                service_request_id=request_id,
                operation_kind="AIInterpretation",
                attempt_number=1,
                state="Succeeded",
                version=3,
                adapter_name="integration-adapter",
                adapter_version="1.0",
                assigned_workflow_service="workflow.human-command.test",
                workflow_environment="integration",
                callback_authorization_deadline=now + timedelta(hours=1),
                started_at=now,
                completed_at=now,
                result_hash=HASH_C,
            )
        )
        connection.execute(
            update(tables["logical_operations"])
            .where(tables["logical_operations"].c.id == operation_id)
            .values(succeeded_attempt_id=attempt_id)
        )
        connection.execute(
            insert(tables["ai_interpretations"]).values(
                id=interpretation_id,
                service_request_id=request_id,
                logical_operation_id=operation_id,
                producing_attempt_id=attempt_id,
                interpretation_number=1,
                summary="Bounded synthetic interpretation.",
                suggested_category="Repair",
                missing_information=[],
                confidence=Decimal("0.9000"),
                input_hash=HASH_A,
                configuration_hash=HASH_B,
                result_schema_version="interpretation-v1",
                prompt_version="prompt-v1",
                provider_name="integration-provider",
                model_name="integration-model",
                adapter_name="integration-adapter",
                adapter_version="1.0",
                warnings=[],
            )
        )
        if candidate_id is not None and candidate_contact_id is not None:
            scored = evaluation.duplicate_candidates[0]
            assert scored.score >= 60
            connection.execute(
                insert(tables["duplicate_candidates"]).values(
                    id=candidate_id,
                    service_request_id=request_id,
                    candidate_type="Contact",
                    candidate_contact_id=candidate_contact_id,
                    policy_id=policy.id,
                    policy_semantic_version=policy.semantic_version,
                    policy_revision=policy.revision,
                    policy_digest=policy.content_digest,
                    source_evidence_hash=HASH_A,
                    candidate_evidence_hash=HASH_B,
                    reason_codes=[item.value for item in scored.reason_codes],
                    deterministic_score=scored.score,
                    resolution_status="Pending",
                )
            )
        connection.execute(
            insert(tables["routing_decisions"]).values(
                id=decision_id,
                service_request_id=request_id,
                decision_number=1,
                policy_id=policy.id,
                policy_semantic_version=policy.semantic_version,
                policy_revision=policy.revision,
                policy_digest=policy.content_digest,
                evaluated_at=now,
                canonical_input_hash=evaluation.canonical_input_hash,
                canonical_input_snapshot=decision_input.model_dump(mode="json"),
                ai_interpretation_id=interpretation_id,
                ai_interpretation_number=1,
                ai_confidence=Decimal("0.9000"),
                missing_information_codes=[
                    item.value for item in evaluation.missing_information_codes
                ],
                final_category=evaluation.final_category.value,
                final_priority=evaluation.final_priority.value,
                final_status=evaluation.final_status.value,
                final_queue=evaluation.final_queue.value,
                review_required=evaluation.review_required,
                review_reason_codes=[item.value for item in evaluation.review_reason_codes],
                category_reason_codes=[item.value for item in evaluation.category_reason_codes],
                priority_reason_codes=[item.value for item in evaluation.priority_reason_codes],
                decision_source=evaluation.source.value,
            )
        )
        if candidate_id is not None:
            connection.execute(
                insert(tables["routing_decision_duplicate_candidates"]).values(
                    routing_decision_id=decision_id,
                    position=1,
                    service_request_id=request_id,
                    duplicate_candidate_id=candidate_id,
                    evidence_role="CurrentPending",
                )
            )
        connection.execute(
            update(tables["service_requests"])
            .where(tables["service_requests"].c.id == request_id)
            .values(
                status=evaluation.final_status.value,
                category=evaluation.final_category.value,
                priority=evaluation.final_priority.value,
                current_queue=evaluation.final_queue.value,
                current_interpretation_id=interpretation_id,
                current_routing_decision_id=decision_id,
                review_required=evaluation.review_required,
                review_reason_codes=[item.value for item in evaluation.review_reason_codes],
            )
        )
    return PersistedDecisionGraph(
        request_id=request_id,
        contact_id=contact_id,
        interpretation_id=interpretation_id,
        decision_id=decision_id,
        version=version,
        candidate_id=candidate_id,
        candidate_contact_id=candidate_contact_id,
    )


def _headers(subject: str, key: str, correlation_id: uuid.UUID | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {subject}",
        "Idempotency-Key": key,
        "X-Correlation-ID": str(correlation_id or uuid.uuid4()),
    }


def _duplicate_body(version: int, decision: str, rationale: str | None = None) -> dict:
    return {
        "schema_version": "1.0",
        "expected_versions": {"service_request": version},
        "command": {"decision": decision, "rationale": rationale},
    }


def _review_body(
    version: int,
    *,
    reviewed_facts: dict,
    addressed_reason: str,
    rationale: str = "Bounded rationale for the integration review.",
    expected_policy: dict | None = None,
) -> dict:
    body = {
        "schema_version": "1.0",
        "expected_versions": {"service_request": version},
        "reviewed_facts": reviewed_facts,
        "addressed_review_reason_codes": [addressed_reason],
        "rationale": rationale,
        "supporting_evidence_references": ["case-note:integration-1"],
    }
    if expected_policy is not None:
        body["expected_policy"] = expected_policy
    return body


def _rows(engine: Engine, table_name: str) -> list[dict]:
    table = Base.metadata.tables[table_name]
    with engine.connect() as connection:
        return list(connection.execute(select(table)).mappings().all())


def _request_row(engine: Engine, request_id: uuid.UUID) -> dict:
    table = Base.metadata.tables["service_requests"]
    with engine.connect() as connection:
        return dict(
            connection.execute(select(table).where(table.c.id == request_id)).mappings().one()
        )


def test_confirmed_duplicate_closes_without_merging_and_writes_safe_evidence(
    client: TestClient,
    engine: Engine,
) -> None:
    graph = _seed_decision_graph(engine, "duplicate")
    _grant(engine, "duplicate-closer", "OperationsAgent")
    rationale = "SENSITIVE-RATIONALE-DO-NOT-PERSIST reviewed against bounded evidence."
    raw_key = "duplicate-close-key"
    response = client.post(
        f"/api/v1/service-requests/{graph.request_id}/duplicate-candidates/"
        f"{graph.candidate_id}/commands/resolve",
        headers=_headers("duplicate-closer", raw_key),
        json=_duplicate_body(graph.version, "ConfirmedDuplicate", rationale),
    )
    assert response.status_code == 200
    assert response.json()["result"] == {
        "service_request_id": str(graph.request_id),
        "duplicate_candidate_id": str(graph.candidate_id),
        "candidate_resolution": "ConfirmedDuplicate",
        "service_request_status": "ClosedDuplicate",
        "service_request_queue": None,
    }

    request_row = _request_row(engine, graph.request_id)
    candidate_row = _rows(engine, "duplicate_candidates")[0]
    contacts = {row["id"] for row in _rows(engine, "contacts")}
    assert request_row["status"] == "ClosedDuplicate"
    assert request_row["current_queue"] is None
    assert request_row["contact_id"] == graph.contact_id
    assert request_row["version"] == graph.version + 1
    assert candidate_row["resolution_status"] == "ConfirmedDuplicate"
    assert candidate_row["resolution_rationale_reference"].startswith("rationale-sha256:")
    assert contacts == {graph.contact_id, graph.candidate_contact_id}

    evidence = json.dumps(
        {
            "audit": [row["safe_metadata"] for row in _rows(engine, "audit_events")],
            "outbox": [row["payload"] for row in _rows(engine, "outbox_messages")],
            "commands": [
                row["safe_response_snapshot"]
                for row in _rows(engine, "command_idempotency_records")
            ],
        }
    )
    for prohibited in (rationale, RAW_CUSTOMER_DESCRIPTION, RAW_CUSTOMER_EMAIL, raw_key):
        assert prohibited not in evidence
    assert {row["event_name"] for row in _rows(engine, "audit_events")} == {
        "duplicate_candidate.resolved",
        "service_request.closed_duplicate",
        "service_request.queue_changed",
    }


def test_not_duplicate_reopens_triage_and_exact_replay_is_side_effect_free(
    client: TestClient,
    engine: Engine,
) -> None:
    graph = _seed_decision_graph(engine, "duplicate")
    _grant(engine, "duplicate-reopener", "OperationsAgent")
    path = (
        f"/api/v1/service-requests/{graph.request_id}/duplicate-candidates/"
        f"{graph.candidate_id}/commands/resolve"
    )
    body = _duplicate_body(graph.version, "NotDuplicate")
    key = "duplicate-replay-key"
    first = client.post(path, headers=_headers("duplicate-reopener", key), json=body)
    before = {
        name: len(_rows(engine, name))
        for name in ("audit_events", "outbox_messages", "command_idempotency_records")
    }
    replay = client.post(path, headers=_headers("duplicate-reopener", key), json=body)
    after = {
        name: len(_rows(engine, name))
        for name in ("audit_events", "outbox_messages", "command_idempotency_records")
    }
    assert first.status_code == replay.status_code == 200
    assert first.json()["command_id"] == replay.json()["command_id"]
    assert first.json()["result"] == replay.json()["result"]
    assert after == before
    assert _request_row(engine, graph.request_id)["status"] == "TriagePending"
    assert _request_row(engine, graph.request_id)["current_queue"] is None
    assert _rows(engine, "duplicate_candidates")[0]["resolution_status"] == "NotDuplicate"


def test_duplicate_candidate_is_request_owned_and_concurrent_versions_conflict(
    client: TestClient,
    engine: Engine,
) -> None:
    first_graph = _seed_decision_graph(engine, "duplicate")
    second_graph = _seed_decision_graph(engine, "duplicate")
    _grant(engine, "duplicate-owner-check", "OperationsAgent")
    cross_request = client.post(
        f"/api/v1/service-requests/{second_graph.request_id}/duplicate-candidates/"
        f"{first_graph.candidate_id}/commands/resolve",
        headers=_headers("duplicate-owner-check", "cross-request-key"),
        json=_duplicate_body(second_graph.version, "NotDuplicate"),
    )
    assert cross_request.status_code == 404
    assert cross_request.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    barrier = threading.Barrier(2)
    path = (
        f"/api/v1/service-requests/{first_graph.request_id}/duplicate-candidates/"
        f"{first_graph.candidate_id}/commands/resolve"
    )
    body = _duplicate_body(first_graph.version, "NotDuplicate")

    def invoke(key: str):
        with TestClient(client.app) as concurrent_client:
            barrier.wait()
            return concurrent_client.post(
                path,
                headers=_headers("duplicate-owner-check", key),
                json=body,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(invoke, ("concurrent-key-one", "concurrent-key-two")))
    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "CONCURRENCY_CONFLICT"
    assert conflict.json()["error"]["current_versions"] == {
        "service_request": first_graph.version + 1
    }


def test_operations_agent_completes_nonurgent_review_with_redacted_evidence_and_replay(
    client: TestClient,
    engine: Engine,
) -> None:
    graph = _seed_decision_graph(engine, "missing")
    _grant(engine, "nonurgent-reviewer", "OperationsAgent")
    rationale = "PRIVATE-REVIEW-RATIONALE resolved through a verified case note."
    body = _review_body(
        graph.version,
        reviewed_facts={"resolved_missing_information_codes": ["MISSING_SERVICE_LOCATION"]},
        addressed_reason="REVIEW_MISSING_REQUIRED_INFORMATION",
        rationale=rationale,
    )
    path = f"/api/v1/service-requests/{graph.request_id}/commands/complete-human-review"
    first = client.post(
        path,
        headers=_headers("nonurgent-reviewer", "human-review-replay-key"),
        json=body,
    )
    before = {
        name: len(_rows(engine, name))
        for name in (
            "reviewed_fact_sets",
            "routing_decisions",
            "audit_events",
            "outbox_messages",
            "command_idempotency_records",
        )
    }
    replay = client.post(
        path,
        headers=_headers("nonurgent-reviewer", "human-review-replay-key"),
        json=body,
    )
    after = {name: len(_rows(engine, name)) for name in before}
    assert first.status_code == replay.status_code == 200
    assert first.json()["command_id"] == replay.json()["command_id"]
    assert first.json()["result"] == replay.json()["result"]
    assert after == before
    result = first.json()["result"]
    assert result["service_request_status"] == "ReadyForAction"
    assert result["service_request_queue"] == "StandardRequests"
    assert result["priority"] == "Normal"
    assert result["review_required"] is False
    assert result["outstanding_review_reason_codes"] == []

    fact = _rows(engine, "reviewed_fact_sets")[0]
    assert fact["fact_snapshot"] == {
        "resolved_missing_information_codes": ["MISSING_SERVICE_LOCATION"]
    }
    assert fact["rationale_reference"].startswith("rationale-sha256:")
    evidence = json.dumps(
        {
            "fact": fact["fact_snapshot"],
            "audit": [row["safe_metadata"] for row in _rows(engine, "audit_events")],
            "outbox": [row["payload"] for row in _rows(engine, "outbox_messages")],
        }
    )
    for prohibited in (rationale, RAW_CUSTOMER_DESCRIPTION, RAW_CUSTOMER_EMAIL):
        assert prohibited not in evidence


def test_nonurgent_review_can_remain_incomplete(
    client: TestClient,
    engine: Engine,
) -> None:
    graph = _seed_decision_graph(engine, "incomplete")
    _grant(engine, "incomplete-reviewer", "OperationsAgent")
    response = client.post(
        f"/api/v1/service-requests/{graph.request_id}/commands/complete-human-review",
        headers=_headers("incomplete-reviewer", "incomplete-review-key"),
        json=_review_body(
            graph.version,
            reviewed_facts={"resolved_missing_information_codes": ["MISSING_SERVICE_LOCATION"]},
            addressed_reason="REVIEW_MISSING_REQUIRED_INFORMATION",
        ),
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["service_request_status"] == "HumanReview"
    assert result["service_request_queue"] == "HumanReview"
    assert result["review_required"] is True
    assert result["outstanding_review_reason_codes"] == ["REVIEW_MISSING_REQUIRED_INFORMATION"]
    assert "service_request.human_review_incomplete" in {
        row["event_name"] for row in _rows(engine, "audit_events")
    }


def test_urgent_review_requires_manager_and_manager_can_confirm(
    client: TestClient,
    engine: Engine,
) -> None:
    graph = _seed_decision_graph(engine, "urgent")
    _grant(engine, "urgent-operations", "OperationsAgent")
    _grant(engine, "urgent-manager", "ManagerApprover")
    path = f"/api/v1/service-requests/{graph.request_id}/commands/complete-human-review"
    body = _review_body(
        graph.version,
        reviewed_facts={"urgent_review_disposition": "ConfirmedAndActionable"},
        addressed_reason="REVIEW_URGENT_PRIORITY",
    )
    denied = client.post(
        path,
        headers=_headers("urgent-operations", "urgent-operations-key"),
        json=body,
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "FORBIDDEN"
    assert len(_rows(engine, "reviewed_fact_sets")) == 0
    assert len(_rows(engine, "routing_decisions")) == 1

    accepted = client.post(
        path,
        headers=_headers("urgent-manager", "urgent-manager-key"),
        json=body,
    )
    assert accepted.status_code == 200
    result = accepted.json()["result"]
    assert result["priority"] == "Urgent"
    assert result["service_request_status"] == "ReadyForAction"
    assert result["service_request_queue"] == "HumanReview"
    assert result["review_required"] is False


def test_urgent_disposition_on_nonurgent_review_is_a_stable_side_effect_free_conflict(
    client: TestClient,
    engine: Engine,
) -> None:
    graph = _seed_decision_graph(engine, "missing")
    _grant(engine, "invalid-urgent-reviewer", "OperationsAgent")
    before_request = _request_row(engine, graph.request_id)

    response = client.post(
        f"/api/v1/service-requests/{graph.request_id}/commands/complete-human-review",
        headers=_headers("invalid-urgent-reviewer", "invalid-urgent-disposition-key"),
        json=_review_body(
            graph.version,
            reviewed_facts={"urgent_review_disposition": "ConfirmedAndActionable"},
            addressed_reason="REVIEW_MISSING_REQUIRED_INFORMATION",
        ),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REVIEW_REQUIREMENTS_UNRESOLVED"
    after_request = _request_row(engine, graph.request_id)
    assert after_request["version"] == before_request["version"]
    assert after_request["status"] == before_request["status"]
    assert (
        after_request["current_routing_decision_id"]
        == before_request["current_routing_decision_id"]
    )
    assert len(_rows(engine, "reviewed_fact_sets")) == 0
    assert len(_rows(engine, "routing_decisions")) == 1
    assert len(_rows(engine, "audit_events")) == 0
    assert len(_rows(engine, "outbox_messages")) == 0
    command_rows = _rows(engine, "command_idempotency_records")
    assert len(command_rows) == 1
    assert command_rows[0]["status"] == "Completed"
    assert command_rows[0]["logical_http_status"] == 409
    assert command_rows[0]["safe_response_snapshot"]["error"]["code"] == (
        "REVIEW_REQUIREMENTS_UNRESOLVED"
    )


def test_expected_policy_conflict_is_guarded_without_domain_writes(
    client: TestClient,
    engine: Engine,
) -> None:
    graph = _seed_decision_graph(engine, "missing")
    _grant(engine, "policy-reviewer", "OperationsAgent")
    response = client.post(
        f"/api/v1/service-requests/{graph.request_id}/commands/complete-human-review",
        headers=_headers("policy-reviewer", "policy-conflict-key"),
        json=_review_body(
            graph.version,
            reviewed_facts={"resolved_missing_information_codes": ["MISSING_SERVICE_LOCATION"]},
            addressed_reason="REVIEW_MISSING_REQUIRED_INFORMATION",
            expected_policy={
                "policy_key": DEMO_DECISION_POLICY.policy_key,
                "semantic_version": DEMO_DECISION_POLICY.semantic_version,
                "revision": DEMO_DECISION_POLICY.revision + 1,
            },
        ),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "POLICY_VERSION_CONFLICT"
    assert len(_rows(engine, "reviewed_fact_sets")) == 0
    assert len(_rows(engine, "routing_decisions")) == 1
    assert len(_rows(engine, "audit_events")) == 0
    command_row = _rows(engine, "command_idempotency_records")[0]
    assert command_row["status"] == "Completed"
    assert command_row["logical_http_status"] == 409


def test_forced_evidence_failure_rolls_back_the_complete_human_review_transaction(
    client: TestClient,
    engine: Engine,
) -> None:
    graph = _seed_decision_graph(engine, "missing")
    _grant(engine, "rollback-reviewer", "OperationsAgent")
    before_request = _request_row(engine, graph.request_id)

    def fail_audit_insert(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        if statement.lstrip().lower().startswith("insert into audit_events"):
            raise RuntimeError("forced audit failure")

    event.listen(engine, "before_cursor_execute", fail_audit_insert)
    try:
        response = client.post(
            f"/api/v1/service-requests/{graph.request_id}/commands/complete-human-review",
            headers=_headers("rollback-reviewer", "rollback-review-key"),
            json=_review_body(
                graph.version,
                reviewed_facts={"resolved_missing_information_codes": ["MISSING_SERVICE_LOCATION"]},
                addressed_reason="REVIEW_MISSING_REQUIRED_INFORMATION",
            ),
        )
    finally:
        event.remove(engine, "before_cursor_execute", fail_audit_insert)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    after_request = _request_row(engine, graph.request_id)
    assert after_request["version"] == before_request["version"]
    assert after_request["status"] == before_request["status"]
    assert (
        after_request["current_routing_decision_id"]
        == before_request["current_routing_decision_id"]
    )
    assert len(_rows(engine, "reviewed_fact_sets")) == 0
    assert len(_rows(engine, "routing_decisions")) == 1
    assert len(_rows(engine, "audit_events")) == 0
    assert len(_rows(engine, "outbox_messages")) == 0
    assert len(_rows(engine, "command_idempotency_records")) == 0
