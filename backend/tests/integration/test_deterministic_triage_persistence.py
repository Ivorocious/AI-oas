"""PostgreSQL evidence for migration 0010 and deterministic triage storage."""

import hashlib
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import Engine, delete, insert, inspect, select, text, update
from sqlalchemy.exc import IntegrityError

from ai_operations_automation.config import Settings
from ai_operations_automation.db import Base, create_database_engine
from ai_operations_automation.deterministic_decision import (
    DEMO_DECISION_POLICY,
    DEMO_POLICY_CONTENT,
    DEMO_POLICY_CONTENT_DIGEST,
    canonical_policy_bytes,
)
from alembic import command

pytestmark = pytest.mark.integration
BACKEND_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_HEAD = "0009_failure_recovery_foundation"
CURRENT_HEAD = "0012_mock_outbound_execution_foundation"
SEEDED_POLICY_TABLES = {
    "decision_policy_versions",
    "failure_recovery_policy_versions",
}
NEW_TABLES = {
    "decision_policy_versions",
    "duplicate_candidates",
    "reviewed_fact_sets",
    "routing_decisions",
    "routing_decision_duplicate_candidates",
}
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
    names = [
        name
        for name in Base.metadata.tables
        if name in existing and name not in SEEDED_POLICY_TABLES
    ]
    if names:
        quoted = ", ".join(f'"{name}"' for name in names)
        with engine.begin() as connection:
            connection.execute(text(f"TRUNCATE {quoted} CASCADE"))
    yield


def _insert_request_graph(engine: Engine, label: str) -> dict[str, uuid.UUID]:
    ids = {
        "contact": uuid.uuid4(),
        "delivery": uuid.uuid4(),
        "request": uuid.uuid4(),
    }
    tables = Base.metadata.tables
    with engine.begin() as connection:
        connection.execute(
            insert(tables["contacts"]).values(
                id=ids["contact"],
                display_label=f"{label} contact",
                version=1,
            )
        )
        connection.execute(
            insert(tables["inbound_deliveries"]).values(
                id=ids["delivery"],
                scope=f"triage-{label}-{uuid.uuid4()}",
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
                normalized_request_description=f"{label} deterministic triage fixture.",
                status="TriagePending",
                version=1,
            )
        )
    return ids


def _insert_actor(engine: Engine) -> uuid.UUID:
    actor_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            insert(Base.metadata.tables["application_actors"]).values(
                id=actor_id,
                supabase_subject=f"triage-{actor_id}",
                display_label="Triage persistence reviewer",
                status="Active",
                version=1,
            )
        )
    return actor_id


def _policy_identity() -> dict[str, object]:
    policy = DEMO_DECISION_POLICY
    return {
        "policy_id": policy.id,
        "policy_semantic_version": policy.semantic_version,
        "policy_revision": policy.revision,
        "policy_digest": policy.content_digest,
    }


def _insert_candidate(
    engine: Engine,
    *,
    source_request_id: uuid.UUID,
    candidate_request_id: uuid.UUID,
    source_hash: str = HASH_A,
    candidate_hash: str = HASH_B,
) -> uuid.UUID:
    candidate_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            insert(Base.metadata.tables["duplicate_candidates"]).values(
                id=candidate_id,
                service_request_id=source_request_id,
                candidate_type="ServiceRequest",
                candidate_service_request_id=candidate_request_id,
                **_policy_identity(),
                source_evidence_hash=source_hash,
                candidate_evidence_hash=candidate_hash,
                reason_codes=["DUPLICATE_EXACT_DESCRIPTION"],
                deterministic_score=65,
                resolution_status="Pending",
            )
        )
    return candidate_id


def _insert_decision(
    engine: Engine,
    *,
    request_id: uuid.UUID,
    duplicate_review: bool = False,
) -> uuid.UUID:
    decision_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            insert(Base.metadata.tables["routing_decisions"]).values(
                id=decision_id,
                service_request_id=request_id,
                decision_number=1,
                **_policy_identity(),
                evaluated_at=datetime.now(UTC),
                canonical_input_hash=HASH_C,
                canonical_input_snapshot={"schema_version": "1.0"},
                missing_information_codes=[],
                final_category="Repair",
                final_priority="Normal",
                final_status="DuplicateReview" if duplicate_review else "ReadyForAction",
                final_queue="DuplicateReview" if duplicate_review else "StandardRequests",
                review_required=duplicate_review,
                review_reason_codes=(["REVIEW_POSSIBLE_DUPLICATE"] if duplicate_review else []),
                category_reason_codes=["CATEGORY_EXPLICIT_SELECTION_ACCEPTED"],
                priority_reason_codes=["PRIORITY_DEFAULT_NORMAL"],
                decision_source="InitialDeterministicCalculation",
            )
        )
    return decision_id


def _constraint_name(error: pytest.ExceptionInfo[IntegrityError]) -> str:
    return error.value.orig.diag.constraint_name


def test_head_has_exactly_twenty_two_application_tables(engine: Engine) -> None:
    assert set(inspect(engine).get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
    assert len(Base.metadata.tables) == 26
    assert NEW_TABLES <= set(Base.metadata.tables)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_HEAD


def test_seed_matches_the_frozen_runtime_policy(engine: Engine) -> None:
    table = Base.metadata.tables["decision_policy_versions"]
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
    assert row["id"] == uuid.UUID("2ddcb753-84a9-5186-bfab-f8b27e870cab")
    assert row["policy_key"] == "general-service-demo"
    assert row["semantic_version"] == "1.0.0"
    assert row["revision"] == 1
    assert row["effective_at"] == datetime(2026, 7, 11, tzinfo=UTC)
    assert row["status"] == "Active"
    assert row["content_digest"] == DEMO_POLICY_CONTENT_DIGEST
    assert row["policy_snapshot"] == expected
    assert canonical == canonical_policy_bytes(DEMO_POLICY_CONTENT)
    assert len(canonical) == 4_954
    assert hashlib.sha256(canonical).hexdigest() == DEMO_POLICY_CONTENT_DIGEST


def test_0010_round_trip_preserves_existing_request_and_reseeds_policy(
    engine: Engine,
) -> None:
    ids = _insert_request_graph(engine, "migration-round-trip")
    engine.dispose()
    command.downgrade(alembic_config(), PREVIOUS_HEAD)
    try:
        inspector = inspect(engine)
        assert not NEW_TABLES & set(inspector.get_table_names())
        assert "current_routing_decision_id" not in {
            column["name"] for column in inspector.get_columns("service_requests")
        }
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT id FROM service_requests WHERE id = :id"),
                    {"id": ids["request"]},
                )
                == ids["request"]
            )
    finally:
        command.upgrade(alembic_config(), "head")

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT current_routing_decision_id, review_required, review_reason_codes "
                "FROM service_requests WHERE id = :id"
            ),
            {"id": ids["request"]},
        ).one()
        assert tuple(row) == (None, None, None)
        assert connection.scalar(text("SELECT count(*) FROM decision_policy_versions")) == 1


def test_duplicate_observation_identity_is_unique(engine: Engine) -> None:
    source = _insert_request_graph(engine, "duplicate-source")
    candidate = _insert_request_graph(engine, "duplicate-target")
    _insert_candidate(
        engine,
        source_request_id=source["request"],
        candidate_request_id=candidate["request"],
    )

    with pytest.raises(IntegrityError) as captured:
        _insert_candidate(
            engine,
            source_request_id=source["request"],
            candidate_request_id=candidate["request"],
        )
    assert _constraint_name(captured) == "uq_duplicate_candidates_request_observation"


def test_candidate_policy_identity_must_match_frozen_row(engine: Engine) -> None:
    source = _insert_request_graph(engine, "policy-source")
    candidate = _insert_request_graph(engine, "policy-target")
    values = _policy_identity() | {"policy_digest": HASH_A}
    with pytest.raises(IntegrityError) as captured:
        with engine.begin() as connection:
            connection.execute(
                insert(Base.metadata.tables["duplicate_candidates"]).values(
                    id=uuid.uuid4(),
                    service_request_id=source["request"],
                    candidate_type="ServiceRequest",
                    candidate_service_request_id=candidate["request"],
                    **values,
                    source_evidence_hash=HASH_A,
                    candidate_evidence_hash=HASH_B,
                    reason_codes=["DUPLICATE_EXACT_DESCRIPTION"],
                    deterministic_score=65,
                    resolution_status="Pending",
                )
            )
    assert _constraint_name(captured) == "fk_duplicate_candidate_policy_identity"


def test_reviewed_fact_snapshot_rejects_routing_outputs(engine: Engine) -> None:
    request = _insert_request_graph(engine, "review-allowlist")
    actor_id = _insert_actor(engine)
    with pytest.raises(IntegrityError) as captured:
        with engine.begin() as connection:
            connection.execute(
                insert(Base.metadata.tables["reviewed_fact_sets"]).values(
                    id=uuid.uuid4(),
                    service_request_id=request["request"],
                    reviewed_actor_id=actor_id,
                    schema_version="1.0",
                    addressed_review_reason_codes=["REVIEW_MISSING_REQUIRED_INFORMATION"],
                    fact_snapshot={"status": "ReadyForAction"},
                    rationale_reference="review-rationale:test",
                    supporting_evidence_references=["contact-log:test"],
                )
            )
    assert _constraint_name(captured) == "ck_reviewed_fact_sets_fact_snapshot_allowlisted"


def test_minimal_duplicate_review_graph_and_request_summary_insert_atomically(
    engine: Engine,
) -> None:
    source = _insert_request_graph(engine, "decision-source")
    candidate = _insert_request_graph(engine, "decision-target")
    candidate_id = _insert_candidate(
        engine,
        source_request_id=source["request"],
        candidate_request_id=candidate["request"],
    )
    decision_id = _insert_decision(
        engine,
        request_id=source["request"],
        duplicate_review=True,
    )

    with engine.begin() as connection:
        connection.execute(
            insert(Base.metadata.tables["routing_decision_duplicate_candidates"]).values(
                routing_decision_id=decision_id,
                position=1,
                service_request_id=source["request"],
                duplicate_candidate_id=candidate_id,
                evidence_role="CurrentPending",
            )
        )
        connection.execute(
            update(Base.metadata.tables["service_requests"])
            .where(Base.metadata.tables["service_requests"].c.id == source["request"])
            .values(
                status="DuplicateReview",
                version=2,
                category="Repair",
                priority="Normal",
                current_queue="DuplicateReview",
                current_routing_decision_id=decision_id,
                review_required=True,
                review_reason_codes=["REVIEW_POSSIBLE_DUPLICATE"],
            )
        )

    with engine.connect() as connection:
        request_row = (
            connection.execute(
                select(Base.metadata.tables["service_requests"]).where(
                    Base.metadata.tables["service_requests"].c.id == source["request"]
                )
            )
            .mappings()
            .one()
        )
        evidence_row = (
            connection.execute(
                select(Base.metadata.tables["routing_decision_duplicate_candidates"])
            )
            .mappings()
            .one()
        )
    assert request_row["current_routing_decision_id"] == decision_id
    assert request_row["review_reason_codes"] == ["REVIEW_POSSIBLE_DUPLICATE"]
    assert evidence_row["duplicate_candidate_id"] == candidate_id


def test_decision_candidate_link_cannot_cross_request_boundary(engine: Engine) -> None:
    decision_request = _insert_request_graph(engine, "decision-owner")
    candidate_owner = _insert_request_graph(engine, "candidate-owner")
    candidate_target = _insert_request_graph(engine, "candidate-other")
    decision_id = _insert_decision(engine, request_id=decision_request["request"])
    candidate_id = _insert_candidate(
        engine,
        source_request_id=candidate_owner["request"],
        candidate_request_id=candidate_target["request"],
    )

    with pytest.raises(IntegrityError) as captured:
        with engine.begin() as connection:
            connection.execute(
                insert(Base.metadata.tables["routing_decision_duplicate_candidates"]).values(
                    routing_decision_id=decision_id,
                    position=1,
                    service_request_id=decision_request["request"],
                    duplicate_candidate_id=candidate_id,
                    evidence_role="CurrentPending",
                )
            )
    assert _constraint_name(captured) == "fk_routing_decision_candidate_evidence_identity"


def test_request_current_decision_cannot_cross_request_boundary(engine: Engine) -> None:
    owner = _insert_request_graph(engine, "routing-owner")
    other = _insert_request_graph(engine, "routing-other")
    decision_id = _insert_decision(engine, request_id=owner["request"])

    with pytest.raises(IntegrityError) as captured:
        with engine.begin() as connection:
            connection.execute(
                update(Base.metadata.tables["service_requests"])
                .where(Base.metadata.tables["service_requests"].c.id == other["request"])
                .values(
                    current_routing_decision_id=decision_id,
                    review_required=False,
                    review_reason_codes=[],
                )
            )
    assert _constraint_name(captured) == ("fk_service_request_current_routing_decision_identity")


def test_policy_delete_is_restricted_after_decision(engine: Engine) -> None:
    request = _insert_request_graph(engine, "policy-delete")
    _insert_decision(engine, request_id=request["request"])

    with pytest.raises(IntegrityError) as captured:
        with engine.begin() as connection:
            connection.execute(
                delete(Base.metadata.tables["decision_policy_versions"]).where(
                    Base.metadata.tables["decision_policy_versions"].c.id == DEMO_DECISION_POLICY.id
                )
            )
    assert _constraint_name(captured) == "fk_routing_decision_policy_identity"
