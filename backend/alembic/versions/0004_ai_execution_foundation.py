"""Add the AI execution persistence foundation.

Revision ID: 0004_ai_execution_foundation
Revises: 0003_human_access_foundation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_ai_execution_foundation"
down_revision: str | None = "0003_human_access_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HASH_PATTERN = "^[0-9a-f]{64}$"


def _nonblank(table: str, fields: tuple[str, ...]) -> list[sa.CheckConstraint]:
    return [
        sa.CheckConstraint(
            f"char_length(trim({field})) > 0",
            name=f"ck_{table}_{field}_not_blank",
        )
        for field in fields
    ]


def upgrade() -> None:
    identity_fields = (
        "prompt_version",
        "result_schema_version",
        "provider_name",
        "model_name",
        "adapter_name",
        "adapter_version",
    )
    op.create_table(
        "logical_operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_kind", sa.String(32), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("configuration_hash", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("result_schema_version", sa.String(100), nullable=False),
        sa.Column("provider_name", sa.String(100), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("adapter_name", sa.String(100), nullable=False),
        sa.Column("adapter_version", sa.String(100), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("succeeded_attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("safe_outcome_summary", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "operation_kind = 'AIInterpretation'",
            name="ck_logical_operations_operation_kind_valid",
        ),
        sa.CheckConstraint(
            f"input_hash ~ '{HASH_PATTERN}'", name="ck_logical_operations_input_hash_valid"
        ),
        sa.CheckConstraint(
            f"configuration_hash ~ '{HASH_PATTERN}'",
            name="ck_logical_operations_configuration_hash_valid",
        ),
        sa.CheckConstraint("version > 0", name="ck_logical_operations_version_positive"),
        *_nonblank("logical_operations", identity_fields),
        sa.ForeignKeyConstraint(
            ["service_request_id"],
            ["service_requests.id"],
            name="fk_logical_operation_request",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_logical_operations"),
        sa.UniqueConstraint(
            "service_request_id",
            "input_hash",
            "configuration_hash",
            name="uq_logical_operations_ai_identity",
        ),
    )
    op.create_index(
        "ix_logical_operations_service_request_created",
        "logical_operations",
        ["service_request_id", "created_at"],
    )
    op.create_table(
        "integration_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("logical_operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_kind", sa.String(32), nullable=False),
        sa.Column("attempt_number", sa.SmallInteger(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("adapter_name", sa.String(100), nullable=False),
        sa.Column("adapter_version", sa.String(100), nullable=False),
        sa.Column("assigned_workflow_service", sa.String(100), nullable=False),
        sa.Column("workflow_environment", sa.String(100), nullable=False),
        sa.Column("callback_authorization_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("safe_provider_correlation", sa.String(200), nullable=True),
        sa.Column("result_hash", sa.String(64), nullable=True),
        sa.Column("sanitized_error_code", sa.String(100), nullable=True),
        sa.CheckConstraint(
            "operation_kind = 'AIInterpretation'",
            name="ck_integration_attempts_operation_kind_valid",
        ),
        sa.CheckConstraint(
            "attempt_number BETWEEN 1 AND 3",
            name="ck_integration_attempts_attempt_number_valid",
        ),
        sa.CheckConstraint(
            "state IN ('Pending', 'Running', 'Succeeded', 'RetryableFailure', 'TerminalFailure')",
            name="ck_integration_attempts_state_valid",
        ),
        sa.CheckConstraint("version > 0", name="ck_integration_attempts_version_positive"),
        sa.CheckConstraint(
            "callback_authorization_deadline > created_at",
            name="ck_integration_attempts_callback_deadline_valid",
        ),
        sa.CheckConstraint(
            f"result_hash IS NULL OR result_hash ~ '{HASH_PATTERN}'",
            name="ck_integration_attempts_result_hash_valid",
        ),
        sa.CheckConstraint(
            "sanitized_error_code IS NULL OR sanitized_error_code ~ '^[A-Z][A-Z0-9_]{0,99}$'",
            name="ck_integration_attempts_error_code_valid",
        ),
        sa.CheckConstraint(
            "(state = 'Pending' AND started_at IS NULL AND completed_at IS NULL "
            "AND result_hash IS NULL AND sanitized_error_code IS NULL) OR "
            "(state = 'Running' AND started_at IS NOT NULL AND completed_at IS NULL "
            "AND result_hash IS NULL AND sanitized_error_code IS NULL) OR "
            "(state = 'Succeeded' AND started_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND result_hash IS NOT NULL AND sanitized_error_code IS NULL) OR "
            "(state IN ('RetryableFailure', 'TerminalFailure') AND completed_at IS NOT NULL "
            "AND result_hash IS NULL AND sanitized_error_code IS NOT NULL)",
            name="ck_integration_attempts_state_fields_consistent",
        ),
        *_nonblank(
            "integration_attempts",
            (
                "adapter_name",
                "adapter_version",
                "assigned_workflow_service",
                "workflow_environment",
            ),
        ),
        sa.ForeignKeyConstraint(
            ["logical_operation_id"],
            ["logical_operations.id"],
            name="fk_attempt_operation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["service_request_id"],
            ["service_requests.id"],
            name="fk_attempt_request",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_integration_attempts"),
        sa.UniqueConstraint(
            "logical_operation_id",
            "attempt_number",
            name="uq_integration_attempts_operation_attempt",
        ),
    )
    op.create_index(
        "ix_integration_attempts_request_created",
        "integration_attempts",
        ["service_request_id", "created_at"],
    )
    op.create_index(
        "uq_integration_attempts_one_active",
        "integration_attempts",
        ["logical_operation_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('Pending', 'Running')"),
    )
    op.create_index(
        "uq_integration_attempts_one_succeeded",
        "integration_attempts",
        ["logical_operation_id"],
        unique=True,
        postgresql_where=sa.text("state = 'Succeeded'"),
    )
    op.create_foreign_key(
        "fk_logical_operation_succeeded_attempt",
        "logical_operations",
        "integration_attempts",
        ["succeeded_attempt_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_table(
        "attempt_callback_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("integration_attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_kind", sa.String(32), nullable=False),
        sa.Column("workflow_service_identity", sa.String(100), nullable=False),
        sa.Column("workflow_environment", sa.String(100), nullable=False),
        sa.Column("credential_version", sa.Integer(), nullable=False),
        sa.Column("credential_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column(
            "issued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replacement_credential_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "operation_kind = 'AIInterpretation'",
            name="ck_attempt_callback_credentials_operation_kind_valid",
        ),
        sa.CheckConstraint(
            "credential_version > 0",
            name="ck_attempt_callback_credentials_credential_version_positive",
        ),
        sa.CheckConstraint(
            f"credential_hash ~ '{HASH_PATTERN}'",
            name="ck_attempt_callback_credentials_credential_hash_valid",
        ),
        sa.CheckConstraint(
            "expires_at > issued_at", name="ck_attempt_callback_credentials_expiry_valid"
        ),
        sa.CheckConstraint(
            "state IN ('Active', 'Consumed', 'Replaced', 'Revoked')",
            name="ck_attempt_callback_credentials_state_valid",
        ),
        sa.CheckConstraint(
            "(state = 'Active' AND consumed_at IS NULL AND replaced_at IS NULL "
            "AND revoked_at IS NULL AND replacement_credential_id IS NULL) OR "
            "(state = 'Consumed' AND consumed_at IS NOT NULL AND replaced_at IS NULL "
            "AND revoked_at IS NULL AND replacement_credential_id IS NULL) OR "
            "(state = 'Replaced' AND consumed_at IS NULL AND replaced_at IS NOT NULL "
            "AND revoked_at IS NULL AND replacement_credential_id IS NOT NULL) OR "
            "(state = 'Revoked' AND consumed_at IS NULL AND replaced_at IS NULL "
            "AND revoked_at IS NOT NULL AND replacement_credential_id IS NULL)",
            name="ck_attempt_callback_credentials_state_fields_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["integration_attempt_id"],
            ["integration_attempts.id"],
            name="fk_callback_credential_attempt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["replacement_credential_id"],
            ["attempt_callback_credentials.id"],
            name="fk_callback_credential_replacement",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_attempt_callback_credentials"),
        sa.UniqueConstraint(
            "integration_attempt_id",
            "credential_version",
            name="uq_attempt_callback_credentials_attempt_credential_version",
        ),
        sa.UniqueConstraint(
            "credential_hash", name="uq_attempt_callback_credentials_credential_hash"
        ),
    )
    op.create_index(
        "uq_attempt_callback_credentials_one_active",
        "attempt_callback_credentials",
        ["integration_attempt_id"],
        unique=True,
        postgresql_where=sa.text("state = 'Active'"),
    )
    op.create_table(
        "ai_interpretations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("logical_operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("producing_attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("interpretation_number", sa.Integer(), nullable=False),
        sa.Column("summary", sa.String(2000), nullable=False),
        sa.Column("suggested_category", sa.String(32), nullable=False),
        sa.Column("missing_information", postgresql.JSONB(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("configuration_hash", sa.String(64), nullable=False),
        sa.Column("result_schema_version", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("provider_name", sa.String(100), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("adapter_name", sa.String(100), nullable=False),
        sa.Column("adapter_version", sa.String(100), nullable=False),
        sa.Column("safe_provider_correlation", sa.String(200), nullable=True),
        sa.Column("warnings", postgresql.JSONB(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("usage_metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "interpretation_number > 0",
            name="ck_ai_interpretations_interpretation_number_positive",
        ),
        sa.CheckConstraint(
            "char_length(trim(summary)) > 0", name="ck_ai_interpretations_summary_not_blank"
        ),
        sa.CheckConstraint(
            "suggested_category IN ('Consultation', 'Installation', 'Repair', "
            "'RoutineMaintenance', 'Inspection', 'OtherCustomRequest')",
            name="ck_ai_interpretations_suggested_category_valid",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(missing_information) = 'array'",
            name="ck_ai_interpretations_missing_information_array",
        ),
        sa.CheckConstraint(
            "warnings IS NULL OR jsonb_typeof(warnings) = 'array'",
            name="ck_ai_interpretations_warnings_array",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_ai_interpretations_confidence_valid"
        ),
        sa.CheckConstraint(
            f"input_hash ~ '{HASH_PATTERN}'", name="ck_ai_interpretations_input_hash_valid"
        ),
        sa.CheckConstraint(
            f"configuration_hash ~ '{HASH_PATTERN}'",
            name="ck_ai_interpretations_configuration_hash_valid",
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_ai_interpretations_latency_nonnegative",
        ),
        *_nonblank("ai_interpretations", identity_fields),
        sa.ForeignKeyConstraint(
            ["service_request_id"],
            ["service_requests.id"],
            name="fk_interpretation_request",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["logical_operation_id"],
            ["logical_operations.id"],
            name="fk_interpretation_operation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["producing_attempt_id"],
            ["integration_attempts.id"],
            name="fk_interpretation_attempt",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_interpretations"),
        sa.UniqueConstraint(
            "service_request_id",
            "interpretation_number",
            name="uq_ai_interpretations_request_number",
        ),
        sa.UniqueConstraint("logical_operation_id", name="uq_ai_interpretations_logical_operation"),
        sa.UniqueConstraint("producing_attempt_id", name="uq_ai_interpretations_producing_attempt"),
    )
    op.create_index(
        "ix_ai_interpretations_request_created",
        "ai_interpretations",
        ["service_request_id", "created_at"],
    )
    op.add_column(
        "service_requests",
        sa.Column("current_interpretation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_service_requests_current_interpretation_id",
        "service_requests",
        ["current_interpretation_id"],
    )
    op.create_foreign_key(
        "fk_service_request_current_interpretation",
        "service_requests",
        "ai_interpretations",
        ["current_interpretation_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_service_request_current_interpretation", "service_requests", type_="foreignkey"
    )
    op.drop_index("ix_service_requests_current_interpretation_id", table_name="service_requests")
    op.drop_column("service_requests", "current_interpretation_id")
    op.drop_table("ai_interpretations")
    op.drop_table("attempt_callback_credentials")
    op.drop_constraint(
        "fk_logical_operation_succeeded_attempt", "logical_operations", type_="foreignkey"
    )
    op.drop_table("integration_attempts")
    op.drop_table("logical_operations")
