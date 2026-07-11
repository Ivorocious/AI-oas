"""Create the accepted-intake persistence foundation.

Revision ID: 0001_intake_persistence
Revises: None
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_intake_persistence"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "contacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_label", sa.String(length=200), nullable=False),
        sa.Column("normalized_email", sa.String(length=320), nullable=True),
        sa.Column("normalized_phone", sa.String(length=32), nullable=True),
        sa.Column("preferred_channel", sa.String(length=32), nullable=True),
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
        sa.CheckConstraint(
            "char_length(trim(display_label)) > 0",
            name="ck_contacts_display_label_not_blank",
        ),
        sa.CheckConstraint("version > 0", name="ck_contacts_version_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_contacts"),
    )
    op.create_index("ix_contacts_normalized_email", "contacts", ["normalized_email"])
    op.create_index("ix_contacts_normalized_phone", "contacts", ["normalized_phone"])

    op.create_table(
        "inbound_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.String(length=100), nullable=False),
        sa.Column("idempotency_key_digest", sa.String(length=128), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processing_status", sa.String(length=32), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_payload_hash", sa.String(length=128), nullable=True),
        sa.Column("raw_body_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("intake_outcome", sa.String(length=32), nullable=True),
        sa.Column("original_delivery_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("logical_result_request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("accepted_intake_key_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sanitized_issues", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("sanitized_error_code", sa.String(length=100), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "intake_outcome IS NULL OR intake_outcome IN "
            "('New', 'IdempotentReplay', 'Invalid', 'IdempotencyConflict')",
            name="ck_inbound_deliveries_intake_outcome_valid",
        ),
        sa.CheckConstraint(
            "processing_status IN ('Received', 'Accepted', 'Rejected', 'ProcessingFailure')",
            name="ck_inbound_deliveries_processing_status_valid",
        ),
        sa.CheckConstraint("version > 0", name="ck_inbound_deliveries_version_positive"),
        sa.ForeignKeyConstraint(
            ["original_delivery_id"],
            ["inbound_deliveries.id"],
            name="fk_inbound_original_delivery",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_inbound_deliveries"),
    )
    op.create_index(
        "ix_inbound_deliveries_logical_result_request_id",
        "inbound_deliveries",
        ["logical_result_request_id"],
    )
    op.create_index(
        "ix_inbound_deliveries_status_outcome_received",
        "inbound_deliveries",
        ["processing_status", "intake_outcome", "received_at"],
    )

    op.create_table(
        "service_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("originating_delivery_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("normalized_request_description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("category", sa.String(length=32), nullable=True),
        sa.Column("priority", sa.String(length=16), nullable=True),
        sa.Column("current_queue", sa.String(length=32), nullable=True),
        sa.Column("location_context", sa.Text(), nullable=True),
        sa.Column("timing_preference", sa.Text(), nullable=True),
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
            "category IS NULL OR category IN "
            "('Consultation', 'Installation', 'Repair', 'RoutineMaintenance', "
            "'Inspection', 'OtherCustomRequest')",
            name="ck_service_requests_category_valid",
        ),
        sa.CheckConstraint(
            "current_queue IS NULL OR current_queue IN "
            "('InvalidSubmissions', 'StandardRequests', 'PriorityRequests', 'HumanReview', "
            "'DuplicateReview', 'FailedRetryRequired')",
            name="ck_service_requests_current_queue_valid",
        ),
        sa.CheckConstraint(
            "char_length(trim(normalized_request_description)) > 0",
            name="ck_service_requests_description_not_blank",
        ),
        sa.CheckConstraint(
            "priority IS NULL OR priority IN ('Low', 'Normal', 'High', 'Urgent')",
            name="ck_service_requests_priority_valid",
        ),
        sa.CheckConstraint(
            "status IN ('TriagePending', 'HumanReview', 'DuplicateReview', 'ReadyForAction', "
            "'AwaitingApproval', 'ActionRevisionRequired', 'ActionPendingExecution', "
            "'RetryableFailure', 'Completed', 'TerminalFailure', 'ClosedDuplicate')",
            name="ck_service_requests_status_valid",
        ),
        sa.CheckConstraint("version > 0", name="ck_service_requests_version_positive"),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contacts.id"],
            name="fk_service_request_contact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["originating_delivery_id"],
            ["inbound_deliveries.id"],
            name="fk_service_request_origin_delivery",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_service_requests"),
        sa.UniqueConstraint(
            "originating_delivery_id",
            name="uq_service_request_origin_delivery",
        ),
    )
    op.create_index(
        "ix_service_requests_contact_id_created_at",
        "service_requests",
        ["contact_id", "created_at"],
    )
    op.create_index(
        "ix_service_requests_status_queue_priority",
        "service_requests",
        ["status", "current_queue", "priority"],
    )

    op.create_table(
        "accepted_intake_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.String(length=100), nullable=False),
        sa.Column("idempotency_key_digest", sa.String(length=128), nullable=False),
        sa.Column("canonical_payload_hash", sa.String(length=128), nullable=False),
        sa.Column("original_delivery_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_http_status", sa.SmallInteger(), nullable=False),
        sa.Column(
            "safe_response_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "original_http_status >= 100 AND original_http_status <= 599",
            name="ck_accepted_intake_keys_original_http_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["original_delivery_id"],
            ["inbound_deliveries.id"],
            name="fk_accepted_key_original_delivery",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["service_requests.id"],
            name="fk_accepted_key_request",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_accepted_intake_keys"),
        sa.UniqueConstraint(
            "original_delivery_id",
            name="uq_accepted_intake_original_delivery",
        ),
        sa.UniqueConstraint("request_id", name="uq_accepted_intake_request"),
        sa.UniqueConstraint(
            "scope",
            "idempotency_key_digest",
            name="uq_accepted_intake_scope_key_digest",
        ),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("event_name", sa.String(length=150), nullable=False),
        sa.Column("aggregate_type", sa.String(length=100), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("actor_type", sa.String(length=50), nullable=False),
        sa.Column("actor_reference_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("outcome", sa.String(length=50), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("causation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "reason_codes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "safe_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "aggregate_version > 0", name="ck_audit_events_aggregate_version_positive"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
    )
    op.create_index(
        "ix_audit_events_aggregate_version_occurred",
        "audit_events",
        ["aggregate_type", "aggregate_id", "aggregate_version", "occurred_at"],
    )
    op.create_index("ix_audit_events_correlation_id", "audit_events", ["correlation_id"])
    op.create_index(
        "ix_audit_events_event_name_occurred_at",
        "audit_events",
        ["event_name", "occurred_at"],
    )

    op.create_table(
        "outbox_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=150), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("aggregate_type", sa.String(length=100), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("audit_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("causation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "publication_state", sa.String(length=20), server_default="Pending", nullable=False
        ),
        sa.Column("lease_owner", sa.String(length=100), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_letter_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_reason", sa.String(length=100), nullable=True),
        sa.CheckConstraint(
            "aggregate_version > 0", name="ck_outbox_messages_aggregate_version_positive"
        ),
        sa.CheckConstraint(
            "publication_state IN ('Pending', 'Publishing', 'Published', 'DeadLetter')",
            name="ck_outbox_messages_publication_state_valid",
        ),
        sa.ForeignKeyConstraint(
            ["audit_event_id"],
            ["audit_events.id"],
            name="fk_outbox_audit_event",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_outbox_messages"),
    )
    op.create_index("ix_outbox_messages_lease_until", "outbox_messages", ["lease_until"])
    op.create_index(
        "ix_outbox_messages_state_available_at",
        "outbox_messages",
        ["publication_state", "available_at"],
    )

    op.create_foreign_key(
        "fk_inbound_created_request",
        "inbound_deliveries",
        "service_requests",
        ["created_request_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_inbound_logical_request",
        "inbound_deliveries",
        "service_requests",
        ["logical_result_request_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_inbound_accepted_key",
        "inbound_deliveries",
        "accepted_intake_keys",
        ["accepted_intake_key_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_inbound_accepted_key",
        "inbound_deliveries",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_inbound_logical_request",
        "inbound_deliveries",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_inbound_created_request",
        "inbound_deliveries",
        type_="foreignkey",
    )
    op.drop_table("outbox_messages")
    op.drop_table("audit_events")
    op.drop_table("accepted_intake_keys")
    op.drop_table("service_requests")
    op.drop_table("inbound_deliveries")
    op.drop_table("contacts")
