"""Human application actor and append-oriented role history."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_operations_automation.db.base import Base


class ApplicationActor(Base):
    __tablename__ = "application_actors"
    __table_args__ = (
        CheckConstraint("status IN ('Active', 'Disabled')", name="status_valid"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            "(status = 'Active' AND disabled_at IS NULL AND disable_reason IS NULL) OR "
            "(status = 'Disabled' AND disabled_at IS NOT NULL)",
            name="disabled_fields_consistent",
        ),
        Index("uq_application_actors_supabase_subject", "supabase_subject", unique=True),
        Index("ix_application_actors_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supabase_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    display_label: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="Active")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disable_reason: Mapped[str | None] = mapped_column(String(200))


class ApplicationActorRoleAssignment(Base):
    __tablename__ = "application_actor_role_assignments"
    __table_args__ = (
        CheckConstraint(
            "role IN ('OperationsAgent', 'ManagerApprover', 'Administrator')",
            name="role_valid",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="interval_valid",
        ),
        CheckConstraint(
            "(effective_to IS NULL AND revoked_by_actor_id IS NULL) OR "
            "(effective_to IS NOT NULL AND revoked_by_actor_id IS NOT NULL)",
            name="revocation_consistent",
        ),
        Index(
            "uq_actor_role_assignment_open",
            "actor_id",
            unique=True,
            postgresql_where=text("effective_to IS NULL"),
        ),
        Index("ix_actor_role_assignment_current", "actor_id", "effective_from", "effective_to"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("application_actors.id", name="fk_role_assignment_actor", ondelete="RESTRICT"),
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    assigned_by_actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "application_actors.id", name="fk_role_assignment_assigner", ondelete="RESTRICT"
        ),
    )
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assignment_reason: Mapped[str] = mapped_column(Text, nullable=False)
    revoked_by_actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("application_actors.id", name="fk_role_assignment_revoker", ondelete="RESTRICT"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
