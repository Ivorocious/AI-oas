"""Add human actor and active-role persistence.

Revision ID: 0003_human_access_foundation
Revises: 0002_atomic_intake_constraints
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_human_access_foundation"
down_revision: str | None = "0002_atomic_intake_constraints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "application_actors",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("supabase_subject", sa.String(255), nullable=False),
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
            "status IN ('Active', 'Disabled')", name="ck_application_actors_status_valid"
        ),
        sa.CheckConstraint("version > 0", name="ck_application_actors_version_positive"),
        sa.CheckConstraint(
            "(status = 'Active' AND disabled_at IS NULL AND disable_reason IS NULL) OR "
            "(status = 'Disabled' AND disabled_at IS NOT NULL)",
            name="ck_application_actors_disabled_fields_consistent",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_application_actors"),
    )
    op.create_index(
        "uq_application_actors_supabase_subject",
        "application_actors",
        ["supabase_subject"],
        unique=True,
    )
    op.create_index("ix_application_actors_status", "application_actors", ["status"])
    op.create_table(
        "application_actor_role_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("assigned_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assignment_reason", sa.Text(), nullable=False),
        sa.Column("revoked_by_actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('OperationsAgent', 'ManagerApprover', 'Administrator')",
            name="ck_application_actor_role_assignments_role_valid",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_application_actor_role_assignments_interval_valid",
        ),
        sa.CheckConstraint(
            "(effective_to IS NULL AND revoked_by_actor_id IS NULL) OR "
            "(effective_to IS NOT NULL AND revoked_by_actor_id IS NOT NULL)",
            name="ck_application_actor_role_assignments_revocation_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["application_actors.id"],
            name="fk_role_assignment_actor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_actor_id"],
            ["application_actors.id"],
            name="fk_role_assignment_assigner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_actor_id"],
            ["application_actors.id"],
            name="fk_role_assignment_revoker",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_application_actor_role_assignments"),
    )
    op.create_index(
        "uq_actor_role_assignment_open",
        "application_actor_role_assignments",
        ["actor_id"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
    )
    op.create_index(
        "ix_actor_role_assignment_current",
        "application_actor_role_assignments",
        ["actor_id", "effective_from", "effective_to"],
    )


def downgrade() -> None:
    op.drop_table("application_actor_role_assignments")
    op.drop_table("application_actors")
