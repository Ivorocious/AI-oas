"""Separate callback command authorization from secret delivery.

Revision ID: 0008_callback_command_authorization_binding
Revises: 0007_command_idempotency_foundation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008_callback_command_authorization_binding"
down_revision: str | None = "0007_command_idempotency_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS_FIELDS_WITH_AUTHORIZATION = (
    "(status = 'Processing' AND logical_http_status IS NULL "
    "AND safe_response_snapshot IS NULL AND completed_at IS NULL "
    "AND callback_credential_id IS NULL AND callback_credential_version IS NULL "
    "AND callback_credential_expires_at IS NULL AND secret_delivery_receipt IS NULL "
    "AND callback_authorization_credential_id IS NULL "
    "AND callback_authorization_credential_version IS NULL) OR "
    "(status = 'Completed' AND logical_http_status IS NOT NULL "
    "AND logical_http_status BETWEEN 200 AND 599 "
    "AND safe_response_snapshot IS NOT NULL "
    "AND jsonb_typeof(safe_response_snapshot) = 'object' "
    "AND completed_at IS NOT NULL AND completed_at >= created_at)"
)

PRIOR_STATUS_FIELDS = (
    "(status = 'Processing' AND logical_http_status IS NULL "
    "AND safe_response_snapshot IS NULL AND completed_at IS NULL "
    "AND callback_credential_id IS NULL AND callback_credential_version IS NULL "
    "AND callback_credential_expires_at IS NULL AND secret_delivery_receipt IS NULL) OR "
    "(status = 'Completed' AND logical_http_status IS NOT NULL "
    "AND logical_http_status BETWEEN 200 AND 599 "
    "AND safe_response_snapshot IS NOT NULL "
    "AND jsonb_typeof(safe_response_snapshot) = 'object' "
    "AND completed_at IS NOT NULL AND completed_at >= created_at)"
)


def upgrade() -> None:
    op.add_column(
        "command_idempotency_records",
        sa.Column(
            "callback_authorization_credential_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "command_idempotency_records",
        sa.Column("callback_authorization_credential_version", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_command_idempotency_callback_authorization_credential",
        "command_idempotency_records",
        "attempt_callback_credentials",
        ["callback_authorization_credential_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_command_idem_callback_authorization_consistent"),
        "command_idempotency_records",
        "(callback_authorization_credential_id IS NULL "
        "AND callback_authorization_credential_version IS NULL) OR "
        "(callback_authorization_credential_id IS NOT NULL "
        "AND callback_authorization_credential_version IS NOT NULL "
        "AND callback_authorization_credential_version > 0)",
    )
    op.drop_constraint(
        op.f("ck_command_idem_status_fields_consistent"),
        "command_idempotency_records",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_command_idem_status_fields_consistent"),
        "command_idempotency_records",
        STATUS_FIELDS_WITH_AUTHORIZATION,
    )
    op.create_index(
        "ix_command_idempotency_callback_authorization_credential",
        "command_idempotency_records",
        ["callback_authorization_credential_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_command_idempotency_callback_authorization_credential",
        table_name="command_idempotency_records",
    )
    op.drop_constraint(
        op.f("ck_command_idem_status_fields_consistent"),
        "command_idempotency_records",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_command_idem_status_fields_consistent"),
        "command_idempotency_records",
        PRIOR_STATUS_FIELDS,
    )
    op.drop_constraint(
        op.f("ck_command_idem_callback_authorization_consistent"),
        "command_idempotency_records",
        type_="check",
    )
    op.drop_constraint(
        "fk_command_idempotency_callback_authorization_credential",
        "command_idempotency_records",
        type_="foreignkey",
    )
    op.drop_column("command_idempotency_records", "callback_authorization_credential_version")
    op.drop_column("command_idempotency_records", "callback_authorization_credential_id")
