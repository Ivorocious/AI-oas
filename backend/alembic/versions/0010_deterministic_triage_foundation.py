"""Add immutable deterministic triage and human-review persistence.

Revision ID: 0010_deterministic_triage_foundation
Revises: 0009_failure_recovery_foundation
"""

import hashlib
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

revision: str = "0010_deterministic_triage_foundation"
down_revision: str | None = "0009_failure_recovery_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEMO_POLICY_ID = uuid.UUID("2ddcb753-84a9-5186-bfab-f8b27e870cab")
DEMO_POLICY_KEY = "general-service-demo"
DEMO_POLICY_SEMANTIC_VERSION = "1.0.0"
DEMO_POLICY_REVISION = 1
DEMO_POLICY_EFFECTIVE_AT = datetime(2026, 7, 11, tzinfo=UTC)
DEMO_POLICY_CANONICAL_JSON = (
    '{"categories":["Consultation","Installation","Repair","RoutineMaintenance","Inspection","OtherCustom'
    'Request"],"category_reason_catalog":["CATEGORY_REVIEWED_CORRECTION","CATEGORY_EXPLICIT_SELECTION_ACC'
    'EPTED","CATEGORY_NORMALIZED_EVIDENCE","CATEGORY_AI_AGREES","CATEGORY_AI_CONFLICT","CATEGORY_CONFLICT'
    '","CATEGORY_MULTIPLE_PLAUSIBLE","CATEGORY_EVIDENCE_UNUSABLE","CATEGORY_OTHER_CUSTOM_SCOPE"],"categor'
    'y_resolution_order":["ReviewedCorrection","ExplicitSelection","SingleNormalizedEvidenceSet","Conflic'
    'tOrMultiplePlausible","NoUsableEvidence","UnconfirmedOtherCustomScope"],"duplicate_reason_catalog":['
    '"DUPLICATE_EXACT_EMAIL","DUPLICATE_EXACT_PHONE","DUPLICATE_EXISTING_CONTACT","DUPLICATE_EXACT_DESCRI'
    'PTION","DUPLICATE_DESCRIPTION_SIMILARITY","DUPLICATE_CATEGORY_MATCH","DUPLICATE_LOCATION_MATCH","DUP'
    'LICATE_TIMING_PROXIMITY"],"duplicate_weights":[{"reason_code":"DUPLICATE_EXACT_EMAIL","weight":70},{'
    '"reason_code":"DUPLICATE_EXACT_PHONE","weight":70},{"reason_code":"DUPLICATE_EXISTING_CONTACT","weig'
    'ht":65},{"reason_code":"DUPLICATE_EXACT_DESCRIPTION","weight":45},{"reason_code":"DUPLICATE_DESCRIPT'
    'ION_SIMILARITY","weight":30},{"reason_code":"DUPLICATE_CATEGORY_MATCH","weight":10},{"reason_code":"'
    'DUPLICATE_LOCATION_MATCH","weight":10},{"reason_code":"DUPLICATE_TIMING_PROXIMITY","weight":5}],"mis'
    'sing_information_catalog":["MISSING_CONTACT_METHOD","MISSING_TIMING_PREFERENCE","MISSING_SERVICE_LOC'
    'ATION","MISSING_ACCESS_CONSTRAINTS","MISSING_CONSULTATION_TOPIC","MISSING_DESIRED_OUTCOME","MISSING_'
    'INSTALLATION_TARGET","MISSING_INSTALLATION_SCOPE","MISSING_REPAIR_SYMPTOMS","MISSING_REPAIR_ASSET_CO'
    'NTEXT","MISSING_MAINTENANCE_ASSET_CONTEXT","MISSING_INSPECTION_SUBJECT","MISSING_INSPECTION_PURPOSE"'
    ',"MISSING_CUSTOM_SCOPE","MISSING_CUSTOM_SCOPE_CONFIRMATION"],"priority_precedence":["Urgent","High",'
    '"Low","Normal"],"priority_reason_catalog":["PRIORITY_CRITICAL_SAFETY_OR_CONTINUITY","PRIORITY_ACTIVE'
    '_INTERRUPTION_IMMEDIATE","PRIORITY_RAPID_DAMAGE_IMMEDIATE","PRIORITY_SEVERE_IMPACT_IMMEDIATE","PRIOR'
    'ITY_ACTIVE_INTERRUPTION","PRIORITY_ACTIVE_DAMAGE_OR_DETERIORATION","PRIORITY_MAJOR_OR_SEVERE_IMPACT"'
    ',"PRIORITY_NEAR_TERM_DEADLINE","PRIORITY_FLEXIBLE_ROUTINE_WORK","PRIORITY_DEFAULT_NORMAL"],"queue_ma'
    'pping":[{"priority":null,"queue":"DuplicateReview","status":"DuplicateReview"},{"priority":null,"que'
    'ue":"HumanReview","status":"HumanReview"},{"priority":"Urgent","queue":"HumanReview","status":"Ready'
    'ForAction"},{"priority":"High","queue":"PriorityRequests","status":"ReadyForAction"},{"priority":"Lo'
    'w","queue":"StandardRequests","status":"ReadyForAction"},{"priority":"Normal","queue":"StandardReque'
    'sts","status":"ReadyForAction"}],"required_information_rules":[{"category":"Consultation","required_'
    'codes":["MISSING_CONTACT_METHOD","MISSING_TIMING_PREFERENCE","MISSING_'
    'SERVICE_LOCATION","MISSING_CONSULTATION_TOPIC","MISSING_DESIRED_OUTCOME"]},{"category":"Installation'
    '","required_codes":["MISSING_CONTACT_METHOD","MISSING_TIMING_PREFERENCE","MISSING_SERVICE_LOCATION",'
    '"MISSING_ACCESS_CONSTRAINTS","MISSING_INSTALLATION_TARGET","MISSING_INSTALLATION_SCOPE"]},{"category'
    '":"Repair","required_codes":["MISSING_CONTACT_METHOD","MISSING_TIMING_PREFERENCE","MISSING_SERVICE_L'
    'OCATION","MISSING_ACCESS_CONSTRAINTS","MISSING_REPAIR_SYMPTOMS","MISSING_REPAIR_ASSET_CONTEXT"]},{"c'
    'ategory":"RoutineMaintenance","required_codes":["MISSING_CONTACT_METHOD","MISSING_TIMING_PREFERENCE"'
    ',"MISSING_SERVICE_LOCATION","MISSING_ACCESS_CONSTRAINTS","MISSING_MAINTENANCE_ASSET_CONTEXT"]},{"cat'
    'egory":"Inspection","required_codes":["MISSING_CONTACT_METHOD","MISSING_TIMING_PREFERENCE","MISSING_'
    'SERVICE_LOCATION","MISSING_ACCESS_CONSTRAINTS","MISSING_INSPECTION_SUBJECT","MISSING_INSPECTION_PURP'
    'OSE"]},{"category":"OtherCustomRequest","required_codes":["MISSING_CONTACT_METHOD","MISSING_TIMING_P'
    'REFERENCE","MISSING_SERVICE_LOCATION","MISSING_CUSTOM_SCOPE","MISSING_DESIRED_OUTCOME","MISSING_CUST'
    'OM_SCOPE_CONFIRMATION"]}],"review_precedence_groups":[["REVIEW_ROUTING_EVIDENCE_UNAVAILABLE"],["REVI'
    'EW_POSSIBLE_DUPLICATE"],["REVIEW_URGENT_PRIORITY"],["REVIEW_REPORTED_SAFETY_OR_CONTINUITY"],["REVIEW'
    '_MISSING_REQUIRED_INFORMATION"],["REVIEW_LOW_AI_CONFIDENCE","REVIEW_AI_POSSIBLE_SAFETY_OR_CONTINUITY'
    '","REVIEW_AI_MISSING_INFORMATION_CONFLICT"],["REVIEW_CATEGORY_AMBIGUITY","REVIEW_CATEGORY_CONFLICT",'
    '"REVIEW_OTHER_CUSTOM_SCOPE"]],"review_reason_catalog":["REVIEW_ROUTING_EVIDENCE_UNAVAILABLE","REVIEW'
    '_POSSIBLE_DUPLICATE","REVIEW_URGENT_PRIORITY","REVIEW_REPORTED_SAFETY_OR_CONTINUITY","REVIEW_MISSING'
    '_REQUIRED_INFORMATION","REVIEW_LOW_AI_CONFIDENCE","REVIEW_AI_POSSIBLE_SAFETY_OR_CONTINUITY","REVIEW_'
    'AI_MISSING_INFORMATION_CONFLICT","REVIEW_CATEGORY_AMBIGUITY","REVIEW_CATEGORY_CONFLICT","REVIEW_OTHE'
    'R_CUSTOM_SCOPE"],"thresholds":{"ai_confidence_review":"0.75","description_similarity":"0.80","duplic'
    'ate_lookback_days":90,"duplicate_retention_score":40,"duplicate_review_score":60,"duplicate_timing_d'
    'ays":14,"high_deadline_hours":72,"low_flexible_days":21,"urgent_deadline_hours":24}}'
)
DEMO_POLICY_CONTENT = json.loads(DEMO_POLICY_CANONICAL_JSON)
DEMO_POLICY_CONTENT_DIGEST = hashlib.sha256(DEMO_POLICY_CANONICAL_JSON.encode("utf-8")).hexdigest()
assert DEMO_POLICY_CONTENT_DIGEST == (
    "45dd2f101bcf2a36842d942fe35a97c6103dfbeac2d4a689e4f1456fce78f41a"
)


def _create_policy_table() -> None:
    op.create_table(
        "decision_policy_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_key", sa.String(100), nullable=False),
        sa.Column("semantic_version", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("policy_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.Column("retirement_reason", sa.String(100)),
        sa.Column("retirement_reference", sa.String(200)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "policy_key ~ '^[a-z][a-z0-9._-]{0,99}$'",
            name=op.f("ck_decision_policy_versions_policy_key_valid"),
        ),
        sa.CheckConstraint(
            "semantic_version ~ '^[0-9]+[.][0-9]+[.][0-9]+(-[0-9A-Za-z.-]+)?$'",
            name=op.f("ck_decision_policy_versions_semantic_version_valid"),
        ),
        sa.CheckConstraint(
            "revision > 0",
            name=op.f("ck_decision_policy_versions_revision_positive"),
        ),
        sa.CheckConstraint(
            "content_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_decision_policy_versions_content_digest_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('Draft', 'Active', 'Retired')",
            name=op.f("ck_decision_policy_versions_status_valid"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(policy_snapshot) = 'object' AND policy_snapshot <> '{}'::jsonb",
            name=op.f("ck_decision_policy_versions_policy_snapshot_object"),
        ),
        sa.CheckConstraint(
            "(status IN ('Draft', 'Active') AND retired_at IS NULL "
            "AND retirement_reason IS NULL AND retirement_reference IS NULL) OR "
            "(status = 'Retired' AND retired_at IS NOT NULL "
            "AND retired_at >= effective_at AND retirement_reason IS NOT NULL "
            "AND retirement_reason ~ '^[A-Z][A-Z0-9_]{0,99}$')",
            name=op.f("ck_decision_policy_versions_retirement_fields_consistent"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_decision_policy_versions")),
        sa.UniqueConstraint(
            "policy_key",
            "semantic_version",
            "revision",
            name="uq_decision_policy_versions_identity",
        ),
        sa.UniqueConstraint(
            "content_digest",
            name="uq_decision_policy_versions_content_digest",
        ),
        sa.UniqueConstraint(
            "id",
            "semantic_version",
            "revision",
            "content_digest",
            name="uq_decision_policy_versions_evaluation_identity",
        ),
    )
    op.create_index(
        "uq_decision_policy_versions_one_active",
        "decision_policy_versions",
        ["policy_key"],
        unique=True,
        postgresql_where=sa.text("status = 'Active'"),
    )
    op.create_index(
        "ix_decision_policy_versions_status_effective",
        "decision_policy_versions",
        ["status", "effective_at"],
    )


def _seed_demonstration_policy() -> None:
    values = {
        "id": DEMO_POLICY_ID,
        "policy_key": DEMO_POLICY_KEY,
        "semantic_version": DEMO_POLICY_SEMANTIC_VERSION,
        "revision": DEMO_POLICY_REVISION,
        "content_digest": DEMO_POLICY_CONTENT_DIGEST,
        "effective_at": DEMO_POLICY_EFFECTIVE_AT,
        "status": "Active",
        "policy_snapshot": DEMO_POLICY_CONTENT,
    }
    if not context.is_offline_mode():
        policies = sa.table(
            "decision_policy_versions",
            sa.column("id", postgresql.UUID(as_uuid=True)),
            sa.column("policy_key", sa.String(100)),
            sa.column("semantic_version", sa.String(32)),
            sa.column("revision", sa.Integer()),
            sa.column("content_digest", sa.String(64)),
            sa.column("effective_at", sa.DateTime(timezone=True)),
            sa.column("status", sa.String(16)),
            sa.column("policy_snapshot", postgresql.JSONB()),
        )
        op.get_bind().execute(policies.insert().values(**values))
        return

    def sql_string(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    canonical = DEMO_POLICY_CANONICAL_JSON.replace(":", r"\:")
    op.execute(
        "INSERT INTO decision_policy_versions "
        "(id, policy_key, semantic_version, revision, content_digest, effective_at, "
        "status, policy_snapshot) VALUES ("
        f"{sql_string(str(DEMO_POLICY_ID))}::uuid, {sql_string(DEMO_POLICY_KEY)}, "
        f"{sql_string(DEMO_POLICY_SEMANTIC_VERSION)}, {DEMO_POLICY_REVISION}, "
        f"{sql_string(DEMO_POLICY_CONTENT_DIGEST)}, "
        f"{sql_string(DEMO_POLICY_EFFECTIVE_AT.isoformat())}::timestamptz, 'Active', "
        f"{sql_string(canonical)}::jsonb)"
    )


def _create_duplicate_candidates() -> None:
    op.create_table(
        "duplicate_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_type", sa.String(32), nullable=False),
        sa.Column("candidate_service_request_id", postgresql.UUID(as_uuid=True)),
        sa.Column("candidate_contact_id", postgresql.UUID(as_uuid=True)),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_semantic_version", sa.String(32), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column("source_evidence_hash", sa.String(64), nullable=False),
        sa.Column("candidate_evidence_hash", sa.String(64), nullable=False),
        sa.Column("reason_codes", postgresql.JSONB(), nullable=False),
        sa.Column("deterministic_score", sa.SmallInteger(), nullable=False),
        sa.Column("sanitized_display_evidence", postgresql.JSONB()),
        sa.Column(
            "resolution_status",
            sa.String(32),
            server_default=sa.text("'Pending'"),
            nullable=False,
        ),
        sa.Column("resolved_by_actor_id", postgresql.UUID(as_uuid=True)),
        sa.Column("resolution_rationale_reference", sa.String(200)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("stale_at", sa.DateTime(timezone=True)),
        sa.Column("superseded_by_candidate_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "candidate_type IN ('ServiceRequest', 'Contact')",
            name=op.f("ck_duplicate_candidates_candidate_type_valid"),
        ),
        sa.CheckConstraint(
            "(candidate_type = 'ServiceRequest' AND candidate_service_request_id IS NOT NULL "
            "AND candidate_contact_id IS NULL "
            "AND candidate_service_request_id <> service_request_id) OR "
            "(candidate_type = 'Contact' AND candidate_service_request_id IS NULL "
            "AND candidate_contact_id IS NOT NULL)",
            name=op.f("ck_duplicate_candidates_candidate_reference_valid"),
        ),
        sa.CheckConstraint(
            "source_evidence_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_duplicate_candidates_source_evidence_hash_valid"),
        ),
        sa.CheckConstraint(
            "candidate_evidence_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_duplicate_candidates_candidate_evidence_hash_valid"),
        ),
        sa.CheckConstraint(
            "deterministic_score BETWEEN 40 AND 100",
            name=op.f("ck_duplicate_candidates_score_valid"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(reason_codes) = 'array' AND jsonb_array_length(reason_codes) > 0",
            name=op.f("ck_duplicate_candidates_reason_codes_nonempty_array"),
        ),
        sa.CheckConstraint(
            "sanitized_display_evidence IS NULL "
            "OR jsonb_typeof(sanitized_display_evidence) = 'object'",
            name=op.f("ck_duplicate_candidates_sanitized_display_evidence_object"),
        ),
        sa.CheckConstraint(
            "resolution_status IN ('Pending', 'ConfirmedDuplicate', 'NotDuplicate')",
            name=op.f("ck_duplicate_candidates_resolution_status_valid"),
        ),
        sa.CheckConstraint(
            "(resolution_status = 'Pending' AND resolved_by_actor_id IS NULL "
            "AND resolution_rationale_reference IS NULL AND resolved_at IS NULL) OR "
            "(resolution_status IN ('ConfirmedDuplicate', 'NotDuplicate') "
            "AND resolved_by_actor_id IS NOT NULL "
            "AND resolution_rationale_reference IS NOT NULL "
            "AND char_length(trim(resolution_rationale_reference)) > 0 "
            "AND resolved_at IS NOT NULL AND resolved_at >= detected_at)",
            name=op.f("ck_duplicate_candidates_resolution_fields_consistent"),
        ),
        sa.CheckConstraint(
            "stale_at IS NULL OR stale_at >= detected_at",
            name=op.f("ck_duplicate_candidates_stale_at_valid"),
        ),
        sa.CheckConstraint(
            "superseded_by_candidate_id IS NULL OR stale_at IS NOT NULL",
            name=op.f("ck_duplicate_candidates_stale_supersession_consistent"),
        ),
        sa.CheckConstraint(
            "superseded_by_candidate_id IS NULL OR superseded_by_candidate_id <> id",
            name=op.f("ck_duplicate_candidates_supersession_not_self"),
        ),
        sa.ForeignKeyConstraint(
            ["service_request_id"],
            ["service_requests.id"],
            name="fk_duplicate_candidate_request",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_service_request_id"],
            ["service_requests.id"],
            name="fk_duplicate_candidate_candidate_request",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_contact_id"],
            ["contacts.id"],
            name="fk_duplicate_candidate_candidate_contact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_actor_id"],
            ["application_actors.id"],
            name="fk_duplicate_candidate_resolver",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_candidate_id"],
            ["duplicate_candidates.id"],
            name="fk_duplicate_candidate_superseding_observation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id", "policy_semantic_version", "policy_revision", "policy_digest"],
            [
                "decision_policy_versions.id",
                "decision_policy_versions.semantic_version",
                "decision_policy_versions.revision",
                "decision_policy_versions.content_digest",
            ],
            name="fk_duplicate_candidate_policy_identity",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_duplicate_candidates")),
        sa.UniqueConstraint(
            "id",
            "service_request_id",
            name="uq_duplicate_candidates_request_identity",
        ),
    )
    op.create_index(
        "uq_duplicate_candidates_request_observation",
        "duplicate_candidates",
        [
            "service_request_id",
            "candidate_service_request_id",
            "policy_id",
            "source_evidence_hash",
            "candidate_evidence_hash",
        ],
        unique=True,
        postgresql_where=sa.text("candidate_type = 'ServiceRequest'"),
    )
    op.create_index(
        "uq_duplicate_candidates_contact_observation",
        "duplicate_candidates",
        [
            "service_request_id",
            "candidate_contact_id",
            "policy_id",
            "source_evidence_hash",
            "candidate_evidence_hash",
        ],
        unique=True,
        postgresql_where=sa.text("candidate_type = 'Contact'"),
    )
    op.create_index(
        "ix_duplicate_candidates_current_pending",
        "duplicate_candidates",
        ["service_request_id", "deterministic_score"],
        postgresql_where=sa.text("resolution_status = 'Pending' AND stale_at IS NULL"),
    )
    op.create_index(
        "ix_duplicate_candidates_request_resolution",
        "duplicate_candidates",
        ["service_request_id", "resolution_status", "stale_at"],
    )


def _create_reviewed_fact_sets() -> None:
    op.create_table(
        "reviewed_fact_sets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewed_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("addressed_review_reason_codes", postgresql.JSONB(), nullable=False),
        sa.Column("fact_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("rationale_reference", sa.String(200), nullable=False),
        sa.Column("supporting_evidence_references", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(trim(schema_version)) > 0",
            name=op.f("ck_reviewed_fact_sets_schema_version_not_blank"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(addressed_review_reason_codes) = 'array' "
            "AND jsonb_array_length(addressed_review_reason_codes) > 0",
            name=op.f("ck_reviewed_fact_sets_addressed_review_reason_codes_nonempty_array"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(fact_snapshot) = 'object' AND fact_snapshot <> '{}'::jsonb",
            name=op.f("ck_reviewed_fact_sets_fact_snapshot_nonempty_object"),
        ),
        sa.CheckConstraint(
            "fact_snapshot - ARRAY["
            "'resolved_missing_information_codes', 'corrected_category', "
            "'custom_scope_confirmed', 'corrected_requested_deadline', "
            "'corrected_timing_preference_present', 'corrected_timing_is_flexible', "
            "'corrected_material_impact', "
            "'corrected_service_interruption', 'corrected_damage_or_deterioration', "
            "'corrected_safety_or_continuity_concern', 'urgent_review_disposition'"
            "]::text[] = '{}'::jsonb",
            name=op.f("ck_reviewed_fact_sets_fact_snapshot_allowlisted"),
        ),
        sa.CheckConstraint(
            "char_length(trim(rationale_reference)) > 0",
            name=op.f("ck_reviewed_fact_sets_rationale_reference_not_blank"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(supporting_evidence_references) = 'array' "
            "AND jsonb_array_length(supporting_evidence_references) > 0",
            name=op.f("ck_reviewed_fact_sets_supporting_evidence_references_nonempty_array"),
        ),
        sa.ForeignKeyConstraint(
            ["service_request_id"],
            ["service_requests.id"],
            name="fk_reviewed_fact_set_request",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_actor_id"],
            ["application_actors.id"],
            name="fk_reviewed_fact_set_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reviewed_fact_sets")),
        sa.UniqueConstraint(
            "id",
            "service_request_id",
            name="uq_reviewed_fact_sets_request_identity",
        ),
    )
    op.create_index(
        "ix_reviewed_fact_sets_request_created",
        "reviewed_fact_sets",
        ["service_request_id", "created_at"],
    )
    op.create_index(
        "ix_reviewed_fact_sets_actor_created",
        "reviewed_fact_sets",
        ["reviewed_actor_id", "created_at"],
    )


def _create_routing_decisions() -> None:
    op.create_unique_constraint(
        "uq_ai_interpretations_routing_identity",
        "ai_interpretations",
        ["id", "service_request_id", "interpretation_number"],
    )
    op.create_table(
        "routing_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_number", sa.Integer(), nullable=False),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_semantic_version", sa.String(32), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("canonical_input_hash", sa.String(64), nullable=False),
        sa.Column("canonical_input_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("ai_interpretation_id", postgresql.UUID(as_uuid=True)),
        sa.Column("ai_interpretation_number", sa.Integer()),
        sa.Column("ai_confidence", sa.Numeric(5, 4)),
        sa.Column("missing_information_codes", postgresql.JSONB(), nullable=False),
        sa.Column("prior_decision_id", postgresql.UUID(as_uuid=True)),
        sa.Column("reviewed_fact_set_id", postgresql.UUID(as_uuid=True)),
        sa.Column("final_category", sa.String(32), nullable=False),
        sa.Column("final_priority", sa.String(16), nullable=False),
        sa.Column("final_status", sa.String(32), nullable=False),
        sa.Column("final_queue", sa.String(32), nullable=False),
        sa.Column("review_required", sa.Boolean(), nullable=False),
        sa.Column("review_reason_codes", postgresql.JSONB(), nullable=False),
        sa.Column("category_reason_codes", postgresql.JSONB(), nullable=False),
        sa.Column("priority_reason_codes", postgresql.JSONB(), nullable=False),
        sa.Column("decision_source", sa.String(40), nullable=False),
        sa.Column("reviewed_actor_id", postgresql.UUID(as_uuid=True)),
        sa.Column("reviewed_rationale_reference", sa.String(200)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision_number > 0",
            name=op.f("ck_routing_decisions_decision_number_positive"),
        ),
        sa.CheckConstraint(
            "canonical_input_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_routing_decisions_canonical_input_hash_valid"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(canonical_input_snapshot) = 'object' "
            "AND canonical_input_snapshot <> '{}'::jsonb",
            name=op.f("ck_routing_decisions_canonical_input_snapshot_nonempty_object"),
        ),
        sa.CheckConstraint(
            "(ai_interpretation_id IS NULL AND ai_interpretation_number IS NULL "
            "AND ai_confidence IS NULL) OR "
            "(ai_interpretation_id IS NOT NULL AND ai_interpretation_number IS NOT NULL "
            "AND ai_interpretation_number > 0 AND ai_confidence BETWEEN 0 AND 1)",
            name=op.f("ck_routing_decisions_interpretation_evidence_consistent"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(missing_information_codes) = 'array'",
            name=op.f("ck_routing_decisions_missing_information_codes_array"),
        ),
        sa.CheckConstraint(
            "final_category IN ('Consultation', 'Installation', 'Repair', "
            "'RoutineMaintenance', 'Inspection', 'OtherCustomRequest')",
            name=op.f("ck_routing_decisions_final_category_valid"),
        ),
        sa.CheckConstraint(
            "final_priority IN ('Low', 'Normal', 'High', 'Urgent')",
            name=op.f("ck_routing_decisions_final_priority_valid"),
        ),
        sa.CheckConstraint(
            "final_status IN ('DuplicateReview', 'HumanReview', 'ReadyForAction')",
            name=op.f("ck_routing_decisions_final_status_valid"),
        ),
        sa.CheckConstraint(
            "final_queue IN ('StandardRequests', 'PriorityRequests', "
            "'HumanReview', 'DuplicateReview')",
            name=op.f("ck_routing_decisions_final_queue_valid"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(review_reason_codes) = 'array' "
            "AND ((review_required AND jsonb_array_length(review_reason_codes) > 0) "
            "OR (NOT review_required AND jsonb_array_length(review_reason_codes) = 0))",
            name=op.f("ck_routing_decisions_review_reason_codes_consistent"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(category_reason_codes) = 'array' "
            "AND jsonb_array_length(category_reason_codes) > 0",
            name=op.f("ck_routing_decisions_category_reason_codes_nonempty_array"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(priority_reason_codes) = 'array' "
            "AND jsonb_array_length(priority_reason_codes) > 0",
            name=op.f("ck_routing_decisions_priority_reason_codes_nonempty_array"),
        ),
        sa.CheckConstraint(
            "(final_status = 'DuplicateReview' AND final_queue = 'DuplicateReview' "
            "AND review_required) OR "
            "(final_status = 'HumanReview' AND final_queue = 'HumanReview' "
            "AND review_required) OR "
            "(final_status = 'ReadyForAction' AND NOT review_required "
            "AND ((final_priority IN ('Low', 'Normal') "
            "AND final_queue = 'StandardRequests') "
            "OR (final_priority = 'High' AND final_queue = 'PriorityRequests') "
            "OR (final_priority = 'Urgent' AND final_queue = 'HumanReview')))",
            name=op.f("ck_routing_decisions_result_summary_consistent"),
        ),
        sa.CheckConstraint(
            "decision_source IN ('InitialDeterministicCalculation', 'ReviewedFactRecalculation')",
            name=op.f("ck_routing_decisions_decision_source_valid"),
        ),
        sa.CheckConstraint(
            "(decision_source = 'InitialDeterministicCalculation' "
            "AND reviewed_fact_set_id IS NULL AND reviewed_actor_id IS NULL "
            "AND reviewed_rationale_reference IS NULL) OR "
            "(decision_source = 'ReviewedFactRecalculation' "
            "AND prior_decision_id IS NOT NULL AND reviewed_fact_set_id IS NOT NULL "
            "AND reviewed_actor_id IS NOT NULL "
            "AND reviewed_rationale_reference IS NOT NULL "
            "AND char_length(trim(reviewed_rationale_reference)) > 0)",
            name=op.f("ck_routing_decisions_review_provenance_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["service_request_id"],
            ["service_requests.id"],
            name="fk_routing_decision_request",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id", "policy_semantic_version", "policy_revision", "policy_digest"],
            [
                "decision_policy_versions.id",
                "decision_policy_versions.semantic_version",
                "decision_policy_versions.revision",
                "decision_policy_versions.content_digest",
            ],
            name="fk_routing_decision_policy_identity",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ai_interpretation_id", "service_request_id", "ai_interpretation_number"],
            [
                "ai_interpretations.id",
                "ai_interpretations.service_request_id",
                "ai_interpretations.interpretation_number",
            ],
            name="fk_routing_decision_interpretation_identity",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_fact_set_id", "service_request_id"],
            ["reviewed_fact_sets.id", "reviewed_fact_sets.service_request_id"],
            name="fk_routing_decision_reviewed_fact_identity",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_actor_id"],
            ["application_actors.id"],
            name="fk_routing_decision_reviewed_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_routing_decisions")),
        sa.UniqueConstraint(
            "service_request_id",
            "decision_number",
            name="uq_routing_decisions_request_number",
        ),
        sa.UniqueConstraint(
            "id",
            "service_request_id",
            name="uq_routing_decisions_request_identity",
        ),
    )
    op.create_foreign_key(
        "fk_routing_decision_prior_identity",
        "routing_decisions",
        "routing_decisions",
        ["prior_decision_id", "service_request_id"],
        ["id", "service_request_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_routing_decisions_request_created",
        "routing_decisions",
        ["service_request_id", "created_at"],
    )
    op.create_index(
        "ix_routing_decisions_policy_identity",
        "routing_decisions",
        ["policy_id", "policy_revision"],
    )
    op.create_index(
        "ix_routing_decisions_input_hash",
        "routing_decisions",
        ["canonical_input_hash"],
    )


def _create_decision_candidate_links() -> None:
    op.create_table(
        "routing_decision_duplicate_candidates",
        sa.Column("routing_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("service_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("duplicate_candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_role", sa.String(32), nullable=False),
        sa.CheckConstraint(
            "position > 0",
            name=op.f("ck_routing_decision_duplicate_candidates_position_positive"),
        ),
        sa.CheckConstraint(
            "evidence_role IN ('CurrentPending', 'ResolvedHistorical', 'StaleHistorical')",
            name=op.f("ck_routing_decision_duplicate_candidates_evidence_role_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["routing_decision_id", "service_request_id"],
            ["routing_decisions.id", "routing_decisions.service_request_id"],
            name="fk_routing_decision_candidate_decision_identity",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["duplicate_candidate_id", "service_request_id"],
            ["duplicate_candidates.id", "duplicate_candidates.service_request_id"],
            name="fk_routing_decision_candidate_evidence_identity",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "routing_decision_id",
            "position",
            name=op.f("pk_routing_decision_duplicate_candidates"),
        ),
        sa.UniqueConstraint(
            "routing_decision_id",
            "duplicate_candidate_id",
            name="uq_routing_decision_candidates_decision_candidate",
        ),
    )
    op.create_index(
        "ix_routing_decision_candidates_candidate_history",
        "routing_decision_duplicate_candidates",
        ["duplicate_candidate_id", "routing_decision_id"],
    )


def _add_request_routing_summary() -> None:
    op.add_column(
        "service_requests",
        sa.Column("current_routing_decision_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column("service_requests", sa.Column("review_required", sa.Boolean()))
    op.add_column(
        "service_requests",
        sa.Column("review_reason_codes", postgresql.JSONB()),
    )
    op.create_foreign_key(
        "fk_service_request_current_routing_decision_identity",
        "service_requests",
        "routing_decisions",
        ["current_routing_decision_id", "id"],
        ["id", "service_request_id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_service_requests_routing_summary_consistent"),
        "service_requests",
        "(current_routing_decision_id IS NULL AND review_required IS NULL "
        "AND review_reason_codes IS NULL) OR "
        "(current_routing_decision_id IS NOT NULL AND review_required IS NOT NULL "
        "AND review_reason_codes IS NOT NULL "
        "AND jsonb_typeof(review_reason_codes) = 'array' "
        "AND ((review_required AND jsonb_array_length(review_reason_codes) > 0) "
        "OR (NOT review_required AND jsonb_array_length(review_reason_codes) = 0)))",
    )
    op.create_index(
        "ix_service_requests_current_routing_decision_id",
        "service_requests",
        ["current_routing_decision_id"],
    )


def upgrade() -> None:
    _create_policy_table()
    _seed_demonstration_policy()
    _create_duplicate_candidates()
    _create_reviewed_fact_sets()
    _create_routing_decisions()
    _create_decision_candidate_links()
    _add_request_routing_summary()


def downgrade() -> None:
    op.drop_index(
        "ix_service_requests_current_routing_decision_id",
        table_name="service_requests",
    )
    op.drop_constraint(
        op.f("ck_service_requests_routing_summary_consistent"),
        "service_requests",
        type_="check",
    )
    op.drop_constraint(
        "fk_service_request_current_routing_decision_identity",
        "service_requests",
        type_="foreignkey",
    )
    for column in ("review_reason_codes", "review_required", "current_routing_decision_id"):
        op.drop_column("service_requests", column)

    op.drop_table("routing_decision_duplicate_candidates")
    op.drop_constraint(
        "fk_routing_decision_prior_identity",
        "routing_decisions",
        type_="foreignkey",
    )
    op.drop_table("routing_decisions")
    op.drop_constraint(
        "uq_ai_interpretations_routing_identity",
        "ai_interpretations",
        type_="unique",
    )
    op.drop_table("reviewed_fact_sets")
    op.drop_table("duplicate_candidates")
    op.drop_table("decision_policy_versions")
