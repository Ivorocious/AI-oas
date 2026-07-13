"""Immutable deterministic triage, duplicate, and human-review evidence."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_operations_automation.db.base import Base
from ai_operations_automation.db.models.intake import (
    PRIORITY_VALUES,
    SERVICE_CATEGORY_VALUES,
    _sql_values,
)

POLICY_STATUS_VALUES = ("Draft", "Active", "Retired")
CANDIDATE_TYPE_VALUES = ("ServiceRequest", "Contact")
CANDIDATE_RESOLUTION_VALUES = ("Pending", "ConfirmedDuplicate", "NotDuplicate")
DECISION_STATUS_VALUES = ("DuplicateReview", "HumanReview", "ReadyForAction")
DECISION_QUEUE_VALUES = (
    "StandardRequests",
    "PriorityRequests",
    "HumanReview",
    "DuplicateReview",
)
DECISION_SOURCE_VALUES = (
    "InitialDeterministicCalculation",
    "ReviewedFactRecalculation",
)
DECISION_CANDIDATE_ROLE_VALUES = (
    "CurrentPending",
    "ResolvedHistorical",
    "StaleHistorical",
)


class DecisionPolicyVersion(Base):
    """One immutable, deployment-controlled deterministic decision policy."""

    __tablename__ = "decision_policy_versions"
    __table_args__ = (
        UniqueConstraint(
            "policy_key",
            "semantic_version",
            "revision",
            name="uq_decision_policy_versions_identity",
        ),
        UniqueConstraint(
            "content_digest",
            name="uq_decision_policy_versions_content_digest",
        ),
        UniqueConstraint(
            "id",
            "semantic_version",
            "revision",
            "content_digest",
            name="uq_decision_policy_versions_evaluation_identity",
        ),
        CheckConstraint(
            "policy_key ~ '^[a-z][a-z0-9._-]{0,99}$'",
            name="policy_key_valid",
        ),
        CheckConstraint(
            "semantic_version ~ '^[0-9]+[.][0-9]+[.][0-9]+(-[0-9A-Za-z.-]+)?$'",
            name="semantic_version_valid",
        ),
        CheckConstraint("revision > 0", name="revision_positive"),
        CheckConstraint(
            "content_digest ~ '^[0-9a-f]{64}$'",
            name="content_digest_valid",
        ),
        CheckConstraint(
            f"status IN ({_sql_values(POLICY_STATUS_VALUES)})",
            name="status_valid",
        ),
        CheckConstraint(
            "jsonb_typeof(policy_snapshot) = 'object' AND policy_snapshot <> '{}'::jsonb",
            name="policy_snapshot_object",
        ),
        CheckConstraint(
            "(status IN ('Draft', 'Active') AND retired_at IS NULL "
            "AND retirement_reason IS NULL AND retirement_reference IS NULL) OR "
            "(status = 'Retired' AND retired_at IS NOT NULL "
            "AND retired_at >= effective_at AND retirement_reason IS NOT NULL "
            "AND retirement_reason ~ '^[A-Z][A-Z0-9_]{0,99}$')",
            name="retirement_fields_consistent",
        ),
        Index(
            "uq_decision_policy_versions_one_active",
            "policy_key",
            unique=True,
            postgresql_where=text("status = 'Active'"),
        ),
        Index(
            "ix_decision_policy_versions_status_effective",
            "status",
            "effective_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    policy_key: Mapped[str] = mapped_column(String(100), nullable=False)
    semantic_version: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retirement_reason: Mapped[str | None] = mapped_column(String(100))
    retirement_reference: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DuplicateCandidate(Base):
    """One immutable match observation with a write-once human resolution."""

    __tablename__ = "duplicate_candidates"
    __table_args__ = (
        ForeignKeyConstraint(
            ("policy_id", "policy_semantic_version", "policy_revision", "policy_digest"),
            (
                "decision_policy_versions.id",
                "decision_policy_versions.semantic_version",
                "decision_policy_versions.revision",
                "decision_policy_versions.content_digest",
            ),
            name="fk_duplicate_candidate_policy_identity",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id",
            "service_request_id",
            name="uq_duplicate_candidates_request_identity",
        ),
        CheckConstraint(
            f"candidate_type IN ({_sql_values(CANDIDATE_TYPE_VALUES)})",
            name="candidate_type_valid",
        ),
        CheckConstraint(
            "(candidate_type = 'ServiceRequest' AND candidate_service_request_id IS NOT NULL "
            "AND candidate_contact_id IS NULL "
            "AND candidate_service_request_id <> service_request_id) OR "
            "(candidate_type = 'Contact' AND candidate_service_request_id IS NULL "
            "AND candidate_contact_id IS NOT NULL)",
            name="candidate_reference_valid",
        ),
        CheckConstraint(
            "source_evidence_hash ~ '^[0-9a-f]{64}$'",
            name="source_evidence_hash_valid",
        ),
        CheckConstraint(
            "candidate_evidence_hash ~ '^[0-9a-f]{64}$'",
            name="candidate_evidence_hash_valid",
        ),
        CheckConstraint("deterministic_score BETWEEN 40 AND 100", name="score_valid"),
        CheckConstraint(
            "jsonb_typeof(reason_codes) = 'array' AND jsonb_array_length(reason_codes) > 0",
            name="reason_codes_nonempty_array",
        ),
        CheckConstraint(
            "sanitized_display_evidence IS NULL "
            "OR jsonb_typeof(sanitized_display_evidence) = 'object'",
            name="sanitized_display_evidence_object",
        ),
        CheckConstraint(
            f"resolution_status IN ({_sql_values(CANDIDATE_RESOLUTION_VALUES)})",
            name="resolution_status_valid",
        ),
        CheckConstraint(
            "(resolution_status = 'Pending' AND resolved_by_actor_id IS NULL "
            "AND resolution_rationale_reference IS NULL AND resolved_at IS NULL) OR "
            "(resolution_status IN ('ConfirmedDuplicate', 'NotDuplicate') "
            "AND resolved_by_actor_id IS NOT NULL "
            "AND resolution_rationale_reference IS NOT NULL "
            "AND char_length(trim(resolution_rationale_reference)) > 0 "
            "AND resolved_at IS NOT NULL AND resolved_at >= detected_at)",
            name="resolution_fields_consistent",
        ),
        CheckConstraint(
            "stale_at IS NULL OR stale_at >= detected_at",
            name="stale_at_valid",
        ),
        CheckConstraint(
            "superseded_by_candidate_id IS NULL OR stale_at IS NOT NULL",
            name="stale_supersession_consistent",
        ),
        CheckConstraint(
            "superseded_by_candidate_id IS NULL OR superseded_by_candidate_id <> id",
            name="supersession_not_self",
        ),
        Index(
            "uq_duplicate_candidates_request_observation",
            "service_request_id",
            "candidate_service_request_id",
            "policy_id",
            "source_evidence_hash",
            "candidate_evidence_hash",
            unique=True,
            postgresql_where=text("candidate_type = 'ServiceRequest'"),
        ),
        Index(
            "uq_duplicate_candidates_contact_observation",
            "service_request_id",
            "candidate_contact_id",
            "policy_id",
            "source_evidence_hash",
            "candidate_evidence_hash",
            unique=True,
            postgresql_where=text("candidate_type = 'Contact'"),
        ),
        Index(
            "ix_duplicate_candidates_current_pending",
            "service_request_id",
            "deterministic_score",
            postgresql_where=text("resolution_status = 'Pending' AND stale_at IS NULL"),
        ),
        Index(
            "ix_duplicate_candidates_request_resolution",
            "service_request_id",
            "resolution_status",
            "stale_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "service_requests.id",
            name="fk_duplicate_candidate_request",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    candidate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    candidate_service_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "service_requests.id",
            name="fk_duplicate_candidate_candidate_request",
            ondelete="RESTRICT",
        ),
    )
    candidate_contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "contacts.id",
            name="fk_duplicate_candidate_candidate_contact",
            ondelete="RESTRICT",
        ),
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    policy_semantic_version: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    source_evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    deterministic_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    sanitized_display_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    resolution_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="Pending", server_default="Pending"
    )
    resolved_by_actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "application_actors.id",
            name="fk_duplicate_candidate_resolver",
            ondelete="RESTRICT",
        ),
    )
    resolution_rationale_reference: Mapped[str | None] = mapped_column(String(200))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "duplicate_candidates.id",
            name="fk_duplicate_candidate_superseding_observation",
            ondelete="RESTRICT",
            use_alter=True,
        ),
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReviewedFactSet(Base):
    """Immutable bounded facts accepted through a future review command."""

    __tablename__ = "reviewed_fact_sets"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "service_request_id",
            name="uq_reviewed_fact_sets_request_identity",
        ),
        CheckConstraint(
            "char_length(trim(schema_version)) > 0",
            name="schema_version_not_blank",
        ),
        CheckConstraint(
            "jsonb_typeof(addressed_review_reason_codes) = 'array' "
            "AND jsonb_array_length(addressed_review_reason_codes) > 0",
            name="addressed_review_reason_codes_nonempty_array",
        ),
        CheckConstraint(
            "jsonb_typeof(fact_snapshot) = 'object' AND fact_snapshot <> '{}'::jsonb",
            name="fact_snapshot_nonempty_object",
        ),
        CheckConstraint(
            "fact_snapshot - ARRAY["
            "'resolved_missing_information_codes', 'corrected_category', "
            "'custom_scope_confirmed', 'corrected_requested_deadline', "
            "'corrected_timing_preference_present', 'corrected_timing_is_flexible', "
            "'corrected_material_impact', "
            "'corrected_service_interruption', 'corrected_damage_or_deterioration', "
            "'corrected_safety_or_continuity_concern', 'urgent_review_disposition'"
            "]::text[] = '{}'::jsonb",
            name="fact_snapshot_allowlisted",
        ),
        CheckConstraint(
            "char_length(trim(rationale_reference)) > 0",
            name="rationale_reference_not_blank",
        ),
        CheckConstraint(
            "jsonb_typeof(supporting_evidence_references) = 'array' "
            "AND jsonb_array_length(supporting_evidence_references) > 0",
            name="supporting_evidence_references_nonempty_array",
        ),
        Index("ix_reviewed_fact_sets_request_created", "service_request_id", "created_at"),
        Index("ix_reviewed_fact_sets_actor_created", "reviewed_actor_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "service_requests.id",
            name="fk_reviewed_fact_set_request",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    reviewed_actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "application_actors.id",
            name="fk_reviewed_fact_set_actor",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    addressed_review_reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    fact_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    rationale_reference: Mapped[str] = mapped_column(String(200), nullable=False)
    supporting_evidence_references: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RoutingDecision(Base):
    """Complete immutable output from one deterministic policy evaluation."""

    __tablename__ = "routing_decisions"
    __table_args__ = (
        UniqueConstraint(
            "service_request_id",
            "decision_number",
            name="uq_routing_decisions_request_number",
        ),
        UniqueConstraint(
            "id",
            "service_request_id",
            name="uq_routing_decisions_request_identity",
        ),
        ForeignKeyConstraint(
            ("policy_id", "policy_semantic_version", "policy_revision", "policy_digest"),
            (
                "decision_policy_versions.id",
                "decision_policy_versions.semantic_version",
                "decision_policy_versions.revision",
                "decision_policy_versions.content_digest",
            ),
            name="fk_routing_decision_policy_identity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("ai_interpretation_id", "service_request_id", "ai_interpretation_number"),
            (
                "ai_interpretations.id",
                "ai_interpretations.service_request_id",
                "ai_interpretations.interpretation_number",
            ),
            name="fk_routing_decision_interpretation_identity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("prior_decision_id", "service_request_id"),
            ("routing_decisions.id", "routing_decisions.service_request_id"),
            name="fk_routing_decision_prior_identity",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ("reviewed_fact_set_id", "service_request_id"),
            ("reviewed_fact_sets.id", "reviewed_fact_sets.service_request_id"),
            name="fk_routing_decision_reviewed_fact_identity",
            ondelete="RESTRICT",
        ),
        CheckConstraint("decision_number > 0", name="decision_number_positive"),
        CheckConstraint(
            "canonical_input_hash ~ '^[0-9a-f]{64}$'",
            name="canonical_input_hash_valid",
        ),
        CheckConstraint(
            "jsonb_typeof(canonical_input_snapshot) = 'object' "
            "AND canonical_input_snapshot <> '{}'::jsonb",
            name="canonical_input_snapshot_nonempty_object",
        ),
        CheckConstraint(
            "(ai_interpretation_id IS NULL AND ai_interpretation_number IS NULL "
            "AND ai_confidence IS NULL) OR "
            "(ai_interpretation_id IS NOT NULL AND ai_interpretation_number IS NOT NULL "
            "AND ai_interpretation_number > 0 AND ai_confidence BETWEEN 0 AND 1)",
            name="interpretation_evidence_consistent",
        ),
        CheckConstraint(
            "jsonb_typeof(missing_information_codes) = 'array'",
            name="missing_information_codes_array",
        ),
        CheckConstraint(
            f"final_category IN ({_sql_values(SERVICE_CATEGORY_VALUES)})",
            name="final_category_valid",
        ),
        CheckConstraint(
            f"final_priority IN ({_sql_values(PRIORITY_VALUES)})",
            name="final_priority_valid",
        ),
        CheckConstraint(
            f"final_status IN ({_sql_values(DECISION_STATUS_VALUES)})",
            name="final_status_valid",
        ),
        CheckConstraint(
            f"final_queue IN ({_sql_values(DECISION_QUEUE_VALUES)})",
            name="final_queue_valid",
        ),
        CheckConstraint(
            "jsonb_typeof(review_reason_codes) = 'array' "
            "AND ((review_required AND jsonb_array_length(review_reason_codes) > 0) "
            "OR (NOT review_required AND jsonb_array_length(review_reason_codes) = 0))",
            name="review_reason_codes_consistent",
        ),
        CheckConstraint(
            "jsonb_typeof(category_reason_codes) = 'array' "
            "AND jsonb_array_length(category_reason_codes) > 0",
            name="category_reason_codes_nonempty_array",
        ),
        CheckConstraint(
            "jsonb_typeof(priority_reason_codes) = 'array' "
            "AND jsonb_array_length(priority_reason_codes) > 0",
            name="priority_reason_codes_nonempty_array",
        ),
        CheckConstraint(
            "(final_status = 'DuplicateReview' AND final_queue = 'DuplicateReview' "
            "AND review_required) OR "
            "(final_status = 'HumanReview' AND final_queue = 'HumanReview' "
            "AND review_required) OR "
            "(final_status = 'ReadyForAction' AND NOT review_required "
            "AND ((final_priority IN ('Low', 'Normal') "
            "AND final_queue = 'StandardRequests') "
            "OR (final_priority = 'High' AND final_queue = 'PriorityRequests') "
            "OR (final_priority = 'Urgent' AND final_queue = 'HumanReview')))",
            name="result_summary_consistent",
        ),
        CheckConstraint(
            f"decision_source IN ({_sql_values(DECISION_SOURCE_VALUES)})",
            name="decision_source_valid",
        ),
        CheckConstraint(
            "(decision_source = 'InitialDeterministicCalculation' "
            "AND reviewed_fact_set_id IS NULL AND reviewed_actor_id IS NULL "
            "AND reviewed_rationale_reference IS NULL) OR "
            "(decision_source = 'ReviewedFactRecalculation' "
            "AND prior_decision_id IS NOT NULL AND reviewed_fact_set_id IS NOT NULL "
            "AND reviewed_actor_id IS NOT NULL "
            "AND reviewed_rationale_reference IS NOT NULL "
            "AND char_length(trim(reviewed_rationale_reference)) > 0)",
            name="review_provenance_consistent",
        ),
        Index("ix_routing_decisions_request_created", "service_request_id", "created_at"),
        Index(
            "ix_routing_decisions_policy_identity",
            "policy_id",
            "policy_revision",
        ),
        Index("ix_routing_decisions_input_hash", "canonical_input_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "service_requests.id",
            name="fk_routing_decision_request",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    decision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    policy_semantic_version: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    canonical_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ai_interpretation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ai_interpretation_number: Mapped[int | None] = mapped_column(Integer)
    ai_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    missing_information_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    prior_decision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reviewed_fact_set_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    final_category: Mapped[str] = mapped_column(String(32), nullable=False)
    final_priority: Mapped[str] = mapped_column(String(16), nullable=False)
    final_status: Mapped[str] = mapped_column(String(32), nullable=False)
    final_queue: Mapped[str] = mapped_column(String(32), nullable=False)
    review_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    review_reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    category_reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    priority_reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    decision_source: Mapped[str] = mapped_column(String(40), nullable=False)
    reviewed_actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "application_actors.id",
            name="fk_routing_decision_reviewed_actor",
            ondelete="RESTRICT",
        ),
    )
    reviewed_rationale_reference: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RoutingDecisionDuplicateCandidate(Base):
    """Ordered immutable duplicate evidence considered by one routing decision."""

    __tablename__ = "routing_decision_duplicate_candidates"
    __table_args__ = (
        ForeignKeyConstraint(
            ("routing_decision_id", "service_request_id"),
            ("routing_decisions.id", "routing_decisions.service_request_id"),
            name="fk_routing_decision_candidate_decision_identity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("duplicate_candidate_id", "service_request_id"),
            ("duplicate_candidates.id", "duplicate_candidates.service_request_id"),
            name="fk_routing_decision_candidate_evidence_identity",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "routing_decision_id",
            "duplicate_candidate_id",
            name="uq_routing_decision_candidates_decision_candidate",
        ),
        CheckConstraint("position > 0", name="position_positive"),
        CheckConstraint(
            f"evidence_role IN ({_sql_values(DECISION_CANDIDATE_ROLE_VALUES)})",
            name="evidence_role_valid",
        ),
        Index(
            "ix_routing_decision_candidates_candidate_history",
            "duplicate_candidate_id",
            "routing_decision_id",
        ),
    )

    routing_decision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    position: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    service_request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    duplicate_candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    evidence_role: Mapped[str] = mapped_column(String(32), nullable=False)
