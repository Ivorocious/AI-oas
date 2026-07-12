"""Add WorkflowService identity, credential metadata, and nonce evidence.

Revision ID: 0006_workflow_authentication_foundation
Revises: 0005_ai_execution_constraint_hardening
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_workflow_authentication_foundation"
down_revision: str | None = "0005_ai_execution_constraint_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "machine_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_type", sa.String(32), nullable=False),
        sa.Column("environment", sa.String(64), nullable=False),
        sa.Column("stable_service_id", sa.String(128), nullable=False),
        sa.Column("display_label", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
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
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disable_reason", sa.String(200), nullable=True),
        sa.CheckConstraint(
            "service_type IN ('BackendService', 'WorkflowService', 'EventPublisher')",
            name="ck_machine_identities_service_type_valid",
        ),
        sa.CheckConstraint(
            "char_length(trim(environment)) > 0",
            name="ck_machine_identities_environment_not_blank",
        ),
        sa.CheckConstraint(
            "char_length(trim(stable_service_id)) > 0 AND stable_service_id ~ '^[A-Za-z0-9._:-]+$'",
            name="ck_machine_identities_stable_service_id_valid",
        ),
        sa.CheckConstraint(
            "char_length(trim(display_label)) > 0",
            name="ck_machine_identities_display_label_not_blank",
        ),
        sa.CheckConstraint(
            "status IN ('Active', 'Disabled')", name="ck_machine_identities_status_valid"
        ),
        sa.CheckConstraint("version > 0", name="ck_machine_identities_version_positive"),
        sa.CheckConstraint(
            "(status = 'Active' AND disabled_at IS NULL AND disable_reason IS NULL) OR "
            "(status = 'Disabled' AND disabled_at IS NOT NULL AND "
            "disable_reason IS NOT NULL AND char_length(trim(disable_reason)) > 0)",
            name="ck_machine_identities_disabled_fields_consistent",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_machine_identities"),
        sa.UniqueConstraint(
            "environment",
            "stable_service_id",
            name="uq_machine_identities_environment_service",
        ),
    )
    op.create_index(
        "ix_machine_identities_type_status",
        "machine_identities",
        ["service_type", "status"],
    )
    op.create_table(
        "machine_credential_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("machine_identity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_version", sa.Integer(), nullable=False),
        sa.Column("external_secret_reference", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_verification_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("safe_rotation_reason", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "credential_version > 0",
            name="ck_machine_credential_versions_credential_version_positive",
        ),
        sa.CheckConstraint(
            "char_length(trim(external_secret_reference)) > 0",
            name="ck_machine_credential_versions_external_reference_not_blank",
        ),
        sa.CheckConstraint(
            "status IN ('Current', 'Previous', 'Retired', 'Revoked')",
            name="ck_machine_credential_versions_status_valid",
        ),
        sa.CheckConstraint(
            "(status = 'Current' AND previous_verification_until IS NULL AND "
            "retired_at IS NULL AND revoked_at IS NULL) OR "
            "(status = 'Previous' AND previous_verification_until IS NOT NULL AND "
            "previous_verification_until > activated_at AND retired_at IS NULL AND "
            "revoked_at IS NULL) OR "
            "(status = 'Retired' AND previous_verification_until IS NULL AND "
            "retired_at IS NOT NULL AND revoked_at IS NULL) OR "
            "(status = 'Revoked' AND previous_verification_until IS NULL AND "
            "retired_at IS NULL AND revoked_at IS NOT NULL)",
            name="ck_machine_credential_versions_state_fields_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["machine_identity_id"],
            ["machine_identities.id"],
            name="fk_machine_credential_identity",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_machine_credential_versions"),
        sa.UniqueConstraint(
            "machine_identity_id",
            "credential_version",
            name="uq_machine_credential_versions_identity_version",
        ),
        sa.UniqueConstraint(
            "external_secret_reference",
            name="uq_machine_credential_versions_external_reference",
        ),
    )
    op.create_index(
        "uq_machine_credential_versions_one_current",
        "machine_credential_versions",
        ["machine_identity_id"],
        unique=True,
        postgresql_where=sa.text("status = 'Current'"),
    )
    op.create_index(
        "uq_machine_credential_versions_one_previous",
        "machine_credential_versions",
        ["machine_identity_id"],
        unique=True,
        postgresql_where=sa.text("status = 'Previous'"),
    )
    op.create_table(
        "machine_request_nonces",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("machine_identity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("machine_credential_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("environment", sa.String(64), nullable=False),
        sa.Column("verified_credential_version", sa.Integer(), nullable=False),
        sa.Column("nonce_digest", sa.String(64), nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "char_length(trim(environment)) > 0",
            name="ck_machine_request_nonces_environment_not_blank",
        ),
        sa.CheckConstraint(
            "verified_credential_version > 0",
            name="ck_machine_request_nonces_credential_version_positive",
        ),
        sa.CheckConstraint(
            "nonce_digest ~ '^[0-9a-f]{64}$'",
            name="ck_machine_request_nonces_nonce_digest_valid",
        ),
        sa.CheckConstraint(
            "expires_at > received_at", name="ck_machine_request_nonces_expiry_valid"
        ),
        sa.ForeignKeyConstraint(
            ["machine_identity_id"],
            ["machine_identities.id"],
            name="fk_machine_nonce_identity",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["machine_credential_version_id"],
            ["machine_credential_versions.id"],
            name="fk_machine_nonce_credential",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_machine_request_nonces"),
        sa.UniqueConstraint(
            "machine_identity_id",
            "environment",
            "nonce_digest",
            name="uq_machine_request_nonces_identity_environment_digest",
        ),
    )
    op.create_index(
        "ix_machine_request_nonces_expires_at",
        "machine_request_nonces",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_table("machine_request_nonces")
    op.drop_table("machine_credential_versions")
    op.drop_table("machine_identities")
