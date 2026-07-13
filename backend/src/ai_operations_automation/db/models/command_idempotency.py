"""Reusable non-intake command-idempotency persistence."""

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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from ai_operations_automation.db.base import Base


class CommandIdempotencyRecord(Base):
    """Processing reservation or immutable safe completed command result."""

    __tablename__ = "command_idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "actor_class",
            "actor_id",
            "command_intent",
            "route_template",
            "target_type",
            "target_id",
            "idempotency_key_digest",
            name="uq_command_idempotency_scope_key",
        ),
        UniqueConstraint("command_id", name="uq_command_idempotency_command_id"),
        CheckConstraint(
            "actor_class IN ('HumanActor', 'MachineService', 'BackendService')",
            name=conv("ck_command_idem_actor_class_valid"),
        ),
        CheckConstraint(
            "command_intent ~ '^[A-Za-z][A-Za-z0-9._:-]{0,99}$'",
            name=conv("ck_command_idem_command_intent_valid"),
        ),
        CheckConstraint(
            "target_type ~ '^[A-Za-z][A-Za-z0-9._:-]{0,99}$'",
            name=conv("ck_command_idem_target_type_valid"),
        ),
        CheckConstraint(
            "route_template ~ '^/[^[:space:][:cntrl:]?#]*$'",
            name=conv("ck_command_idem_route_template_valid"),
        ),
        CheckConstraint(
            "idempotency_key_digest ~ '^[0-9a-f]{64}$'",
            name=conv("ck_command_idem_key_digest_valid"),
        ),
        CheckConstraint(
            "canonical_body_hash ~ '^[0-9a-f]{64}$'",
            name=conv("ck_command_idem_body_hash_valid"),
        ),
        CheckConstraint(
            "status IN ('Processing', 'Completed')", name=conv("ck_command_idem_status_valid")
        ),
        CheckConstraint(
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
            "AND completed_at IS NOT NULL AND completed_at >= created_at)",
            name=conv("ck_command_idem_status_fields_consistent"),
        ),
        CheckConstraint(
            "(callback_credential_id IS NULL AND callback_credential_version IS NULL "
            "AND callback_credential_expires_at IS NULL AND secret_delivery_receipt IS NULL) OR "
            "(callback_credential_id IS NOT NULL AND callback_credential_version > 0 "
            "AND callback_credential_expires_at IS NOT NULL "
            "AND secret_delivery_receipt = 'PlaintextIssued')",
            name=conv("ck_command_idem_secret_delivery_consistent"),
        ),
        CheckConstraint(
            "(callback_authorization_credential_id IS NULL "
            "AND callback_authorization_credential_version IS NULL) OR "
            "(callback_authorization_credential_id IS NOT NULL "
            "AND callback_authorization_credential_version IS NOT NULL "
            "AND callback_authorization_credential_version > 0)",
            name=conv("ck_command_idem_callback_authorization_consistent"),
        ),
        Index(
            "ix_command_idempotency_target_created",
            "target_type",
            "target_id",
            "created_at",
        ),
        Index("ix_command_idempotency_created_at", "created_at"),
        Index(
            "ix_command_idempotency_callback_authorization_credential",
            "callback_authorization_credential_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_class: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    command_intent: Mapped[str] = mapped_column(String(100), nullable=False)
    route_template: Mapped[str] = mapped_column(String(300), nullable=False)
    target_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    idempotency_key_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_body_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    command_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    logical_http_status: Mapped[int | None] = mapped_column(SmallInteger)
    safe_response_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    callback_credential_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "attempt_callback_credentials.id",
            name="fk_command_idempotency_callback_credential",
            ondelete="RESTRICT",
        ),
    )
    callback_credential_version: Mapped[int | None] = mapped_column(Integer)
    callback_credential_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    secret_delivery_receipt: Mapped[str | None] = mapped_column(String(32))
    callback_authorization_credential_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "attempt_callback_credentials.id",
            name="fk_command_idempotency_callback_authorization_credential",
            ondelete="RESTRICT",
        ),
    )
    callback_authorization_credential_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
