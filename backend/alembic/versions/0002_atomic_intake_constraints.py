"""Make reservation references safe for reservation-first atomic intake.

Revision ID: 0002_atomic_intake_constraints
Revises: 0001_intake_persistence
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_atomic_intake_constraints"
down_revision: str | None = "0001_intake_persistence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "fk_accepted_key_original_delivery", "accepted_intake_keys", type_="foreignkey"
    )
    op.drop_constraint("fk_accepted_key_request", "accepted_intake_keys", type_="foreignkey")
    op.create_foreign_key(
        "fk_accepted_key_original_delivery",
        "accepted_intake_keys",
        "inbound_deliveries",
        ["original_delivery_id"],
        ["id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_accepted_key_request",
        "accepted_intake_keys",
        "service_requests",
        ["request_id"],
        ["id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_accepted_key_original_delivery", "accepted_intake_keys", type_="foreignkey"
    )
    op.drop_constraint("fk_accepted_key_request", "accepted_intake_keys", type_="foreignkey")
    op.create_foreign_key(
        "fk_accepted_key_original_delivery",
        "accepted_intake_keys",
        "inbound_deliveries",
        ["original_delivery_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_accepted_key_request",
        "accepted_intake_keys",
        "service_requests",
        ["request_id"],
        ["id"],
        ondelete="RESTRICT",
    )
