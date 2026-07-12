"""Minimum accepted-intake relational models."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_operations_automation.db.base import Base

DELIVERY_PROCESSING_VALUES = ("Received", "Accepted", "Rejected", "ProcessingFailure")
INTAKE_OUTCOME_VALUES = ("New", "IdempotentReplay", "Invalid", "IdempotencyConflict")
SERVICE_REQUEST_STATUS_VALUES = (
    "TriagePending",
    "HumanReview",
    "DuplicateReview",
    "ReadyForAction",
    "AwaitingApproval",
    "ActionRevisionRequired",
    "ActionPendingExecution",
    "RetryableFailure",
    "Completed",
    "TerminalFailure",
    "ClosedDuplicate",
)
SERVICE_CATEGORY_VALUES = (
    "Consultation",
    "Installation",
    "Repair",
    "RoutineMaintenance",
    "Inspection",
    "OtherCustomRequest",
)
PRIORITY_VALUES = ("Low", "Normal", "High", "Urgent")
QUEUE_VALUES = (
    "InvalidSubmissions",
    "StandardRequests",
    "PriorityRequests",
    "HumanReview",
    "DuplicateReview",
    "FailedRetryRequired",
)


def _sql_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class InboundDelivery(Base):
    """One physical intake delivery and its eventual safe classification evidence."""

    __tablename__ = "inbound_deliveries"
    __table_args__ = (
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            f"processing_status IN ({_sql_values(DELIVERY_PROCESSING_VALUES)})",
            name="processing_status_valid",
        ),
        CheckConstraint(
            f"intake_outcome IS NULL OR intake_outcome IN ({_sql_values(INTAKE_OUTCOME_VALUES)})",
            name="intake_outcome_valid",
        ),
        Index(
            "ix_inbound_deliveries_status_outcome_received",
            "processing_status",
            "intake_outcome",
            "received_at",
        ),
        Index("ix_inbound_deliveries_logical_result_request_id", "logical_result_request_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processing_status: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    canonical_payload_hash: Mapped[str | None] = mapped_column(String(128))
    raw_body_fingerprint: Mapped[str | None] = mapped_column(String(128))
    intake_outcome: Mapped[str | None] = mapped_column(String(32))
    original_delivery_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "inbound_deliveries.id", name="fk_inbound_original_delivery", ondelete="RESTRICT"
        ),
    )
    created_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "service_requests.id",
            name="fk_inbound_created_request",
            ondelete="RESTRICT",
            use_alter=True,
        ),
    )
    logical_result_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "service_requests.id",
            name="fk_inbound_logical_request",
            ondelete="RESTRICT",
            use_alter=True,
        ),
    )
    accepted_intake_key_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "accepted_intake_keys.id",
            name="fk_inbound_accepted_key",
            ondelete="RESTRICT",
            use_alter=True,
        ),
    )
    sanitized_issues: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    sanitized_error_code: Mapped[str | None] = mapped_column(String(100))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Contact(Base):
    """Minimum contact record required to own an accepted request."""

    __tablename__ = "contacts"
    __table_args__ = (
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint("char_length(trim(display_label)) > 0", name="display_label_not_blank"),
        Index("ix_contacts_normalized_email", "normalized_email"),
        Index("ix_contacts_normalized_phone", "normalized_phone"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    display_label: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_email: Mapped[str | None] = mapped_column(String(320))
    normalized_phone: Mapped[str | None] = mapped_column(String(32))
    preferred_channel: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ServiceRequest(Base):
    """Structural service-request root created by a future accepted-intake command."""

    __tablename__ = "service_requests"
    __table_args__ = (
        UniqueConstraint("originating_delivery_id", name="uq_service_request_origin_delivery"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            "char_length(trim(normalized_request_description)) > 0",
            name="description_not_blank",
        ),
        CheckConstraint(
            f"status IN ({_sql_values(SERVICE_REQUEST_STATUS_VALUES)})", name="status_valid"
        ),
        CheckConstraint(
            f"category IS NULL OR category IN ({_sql_values(SERVICE_CATEGORY_VALUES)})",
            name="category_valid",
        ),
        CheckConstraint(
            f"priority IS NULL OR priority IN ({_sql_values(PRIORITY_VALUES)})",
            name="priority_valid",
        ),
        CheckConstraint(
            f"current_queue IS NULL OR current_queue IN ({_sql_values(QUEUE_VALUES)})",
            name="current_queue_valid",
        ),
        Index("ix_service_requests_contact_id_created_at", "contact_id", "created_at"),
        Index("ix_service_requests_status_queue_priority", "status", "current_queue", "priority"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    originating_delivery_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "inbound_deliveries.id", name="fk_service_request_origin_delivery", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contacts.id", name="fk_service_request_contact", ondelete="RESTRICT"),
        nullable=False,
    )
    normalized_request_description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    category: Mapped[str | None] = mapped_column(String(32))
    priority: Mapped[str | None] = mapped_column(String(16))
    current_queue: Mapped[str | None] = mapped_column(String(32))
    location_context: Mapped[str | None] = mapped_column(Text)
    timing_preference: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AcceptedIntakeKey(Base):
    """Accepted logical-intake reservation; raw keys never enter this table."""

    __tablename__ = "accepted_intake_keys"
    __table_args__ = (
        UniqueConstraint(
            "scope", "idempotency_key_digest", name="uq_accepted_intake_scope_key_digest"
        ),
        UniqueConstraint("original_delivery_id", name="uq_accepted_intake_original_delivery"),
        UniqueConstraint("request_id", name="uq_accepted_intake_request"),
        CheckConstraint(
            "original_http_status >= 100 AND original_http_status <= 599",
            name="original_http_status_valid",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_payload_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    original_delivery_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "inbound_deliveries.id",
            name="fk_accepted_key_original_delivery",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "service_requests.id",
            name="fk_accepted_key_request",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
    )
    original_http_status: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    safe_response_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
