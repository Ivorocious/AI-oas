"""Harden AI callback credential scope and replacement integrity.

Revision ID: 0005_ai_execution_constraint_hardening
Revises: 0004_ai_execution_foundation
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_ai_execution_constraint_hardening"
down_revision: str | None = "0004_ai_execution_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(32),
        type_=sa.String(64),
        existing_nullable=False,
    )
    op.create_check_constraint(
        op.f("ck_attempt_callback_credentials_workflow_service_identity_not_blank"),
        "attempt_callback_credentials",
        "char_length(trim(workflow_service_identity)) > 0",
    )
    op.create_check_constraint(
        op.f("ck_attempt_callback_credentials_workflow_environment_not_blank"),
        "attempt_callback_credentials",
        "char_length(trim(workflow_environment)) > 0",
    )
    op.create_check_constraint(
        op.f("ck_attempt_callback_credentials_replacement_not_self"),
        "attempt_callback_credentials",
        "replacement_credential_id IS NULL OR replacement_credential_id <> id",
    )
    op.drop_constraint(
        "fk_callback_credential_replacement",
        "attempt_callback_credentials",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_callback_credential_replacement",
        "attempt_callback_credentials",
        "attempt_callback_credentials",
        ["replacement_credential_id"],
        ["id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_callback_credential_replacement",
        "attempt_callback_credentials",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_callback_credential_replacement",
        "attempt_callback_credentials",
        "attempt_callback_credentials",
        ["replacement_credential_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        op.f("ck_attempt_callback_credentials_replacement_not_self"),
        "attempt_callback_credentials",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_attempt_callback_credentials_workflow_environment_not_blank"),
        "attempt_callback_credentials",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_attempt_callback_credentials_workflow_service_identity_not_blank"),
        "attempt_callback_credentials",
        type_="check",
    )
