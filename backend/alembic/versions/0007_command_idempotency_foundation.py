"""Add reusable non-intake command idempotency.

Revision ID: 0007_command_idempotency_foundation
Revises: 0006_workflow_authentication_foundation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_command_idempotency_foundation"
down_revision: str | None = "0006_workflow_authentication_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "command_idempotency_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_class", sa.String(32), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("command_intent", sa.String(100), nullable=False),
        sa.Column("route_template", sa.String(300), nullable=False),
        sa.Column("target_type", sa.String(100), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key_digest", sa.String(64), nullable=False),
        sa.Column("canonical_body_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("logical_http_status", sa.SmallInteger(), nullable=True),
        sa.Column("safe_response_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("callback_credential_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("callback_credential_version", sa.Integer(), nullable=True),
        sa.Column("callback_credential_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("secret_delivery_receipt", sa.String(32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor_class IN ('HumanActor', 'MachineService', 'BackendService')",
            name=op.f("ck_command_idem_actor_class_valid"),
        ),
        sa.CheckConstraint(
            "command_intent ~ '^[A-Za-z][A-Za-z0-9._:-]{0,99}$'",
            name=op.f("ck_command_idem_command_intent_valid"),
        ),
        sa.CheckConstraint(
            "target_type ~ '^[A-Za-z][A-Za-z0-9._:-]{0,99}$'",
            name=op.f("ck_command_idem_target_type_valid"),
        ),
        sa.CheckConstraint(
            "route_template ~ '^/[^[:space:][:cntrl:]?#]*$'",
            name=op.f("ck_command_idem_route_template_valid"),
        ),
        sa.CheckConstraint(
            "idempotency_key_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_command_idem_key_digest_valid"),
        ),
        sa.CheckConstraint(
            "canonical_body_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_command_idem_body_hash_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('Processing', 'Completed')",
            name=op.f("ck_command_idem_status_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'Processing' AND logical_http_status IS NULL "
            "AND safe_response_snapshot IS NULL AND completed_at IS NULL "
            "AND callback_credential_id IS NULL AND callback_credential_version IS NULL "
            "AND callback_credential_expires_at IS NULL AND secret_delivery_receipt IS NULL) OR "
            "(status = 'Completed' AND logical_http_status IS NOT NULL "
            "AND logical_http_status BETWEEN 200 AND 599 "
            "AND safe_response_snapshot IS NOT NULL "
            "AND jsonb_typeof(safe_response_snapshot) = 'object' "
            "AND completed_at IS NOT NULL AND completed_at >= created_at)",
            name=op.f("ck_command_idem_status_fields_consistent"),
        ),
        sa.CheckConstraint(
            "(callback_credential_id IS NULL AND callback_credential_version IS NULL "
            "AND callback_credential_expires_at IS NULL AND secret_delivery_receipt IS NULL) OR "
            "(callback_credential_id IS NOT NULL AND callback_credential_version > 0 "
            "AND callback_credential_expires_at IS NOT NULL "
            "AND secret_delivery_receipt = 'PlaintextIssued')",
            name=op.f("ck_command_idem_secret_delivery_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["callback_credential_id"],
            ["attempt_callback_credentials.id"],
            name="fk_command_idempotency_callback_credential",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_command_idempotency_records"),
        sa.UniqueConstraint(
            "actor_class",
            "actor_id",
            "command_intent",
            "route_template",
            "target_type",
            "target_id",
            "idempotency_key_digest",
            name="uq_command_idempotency_scope_key",
        ),
        sa.UniqueConstraint("command_id", name="uq_command_idempotency_command_id"),
    )
    op.create_index(
        "ix_command_idempotency_target_created",
        "command_idempotency_records",
        ["target_type", "target_id", "created_at"],
    )
    op.create_index(
        "ix_command_idempotency_created_at",
        "command_idempotency_records",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_table("command_idempotency_records")
