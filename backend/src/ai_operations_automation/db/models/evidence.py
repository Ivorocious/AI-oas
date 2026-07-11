"""Append-oriented audit and transactional-outbox models."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_operations_automation.db.base import Base

PUBLICATION_STATE_VALUES = ("Pending", "Publishing", "Published", "DeadLetter")


class AuditEvent(Base):
    """Sanitized, append-oriented evidence for a future atomic intake command."""

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("aggregate_version > 0", name="aggregate_version_positive"),
        Index(
            "ix_audit_events_aggregate_version_occurred",
            "aggregate_type",
            "aggregate_id",
            "aggregate_version",
            "occurred_at",
        ),
        Index("ix_audit_events_correlation_id", "correlation_id"),
        Index("ix_audit_events_event_name_occurred_at", "event_name", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    event_name: Mapped[str] = mapped_column(String(150), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_reference_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    outcome: Mapped[str] = mapped_column(String(50), nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    causation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    command_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reason_codes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    safe_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class OutboxMessage(Base):
    """Durable integration-message data; publication behavior is not implemented."""

    __tablename__ = "outbox_messages"
    __table_args__ = (
        CheckConstraint("aggregate_version > 0", name="aggregate_version_positive"),
        CheckConstraint(
            "publication_state IN ('Pending', 'Publishing', 'Published', 'DeadLetter')",
            name="publication_state_valid",
        ),
        Index("ix_outbox_messages_state_available_at", "publication_state", "available_at"),
        Index("ix_outbox_messages_lease_until", "lease_until"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(150), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    audit_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("audit_events.id", name="fk_outbox_audit_event", ondelete="RESTRICT"),
        nullable=False,
    )
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    causation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    publication_state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="Pending", server_default="Pending"
    )
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dead_letter_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_reason: Mapped[str | None] = mapped_column(String(100))
