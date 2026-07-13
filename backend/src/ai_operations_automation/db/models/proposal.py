"""Proposal versions, immutable contributors, exclusions, and decisions."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_operations_automation.db.base import Base


class ProposedAction(Base):
    __tablename__ = "proposed_actions"
    __table_args__ = (
        UniqueConstraint(
            "service_request_id",
            "proposal_series_id",
            "proposal_number",
            name="uq_proposed_actions_series_number",
        ),
        UniqueConstraint("id", "service_request_id", name="uq_proposed_actions_request_identity"),
        UniqueConstraint(
            "id",
            "service_request_id",
            "proposal_series_id",
            name="uq_proposed_actions_series_identity",
        ),
        UniqueConstraint(
            "id", "proposal_number", "payload_digest", name="uq_proposed_actions_decision_identity"
        ),
        ForeignKeyConstraint(
            ("logical_operation_id", "service_request_id", "proposal_series_id"),
            (
                "logical_operations.id",
                "logical_operations.service_request_id",
                "logical_operations.proposal_series_id",
            ),
            name="fk_proposed_action_operation_identity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("supersedes_id", "service_request_id", "proposal_series_id"),
            (
                "proposed_actions.id",
                "proposed_actions.service_request_id",
                "proposed_actions.proposal_series_id",
            ),
            name="fk_proposed_action_supersedes_identity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("superseded_by_id", "service_request_id", "proposal_series_id"),
            (
                "proposed_actions.id",
                "proposed_actions.service_request_id",
                "proposed_actions.proposal_series_id",
            ),
            name="fk_proposed_action_superseded_by_identity",
            ondelete="RESTRICT",
        ),
        CheckConstraint("proposal_number > 0", name="proposal_number_positive"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            "state IN ('Draft','PendingApproval','Approved','Rejected','PendingExecution',"
            "'RetryableExecutionFailure','Executed','TerminalExecutionFailure','Superseded')",
            name="state_valid",
        ),
        CheckConstraint(
            "action_type IN ('CustomerMessage','SchedulingInvitation')", name="action_type_valid"
        ),
        CheckConstraint("destination_kind IN ('Email','Phone')", name="destination_kind_valid"),
        CheckConstraint(
            "char_length(trim(destination_value)) BETWEEN 3 AND 320",
            name="destination_value_bounded",
        ),
        CheckConstraint("char_length(content) BETWEEN 1 AND 10000", name="content_bounded"),
        CheckConstraint("payload_digest ~ '^[0-9a-f]{64}$'", name="payload_digest_valid"),
        CheckConstraint(
            "scheduling_window_end IS NULL OR scheduling_window_start IS NOT NULL",
            name="schedule_complete",
        ),
        CheckConstraint(
            "scheduling_window_end IS NULL OR scheduling_window_end > scheduling_window_start",
            name="schedule_ordered",
        ),
        CheckConstraint(
            "scheduling_notes IS NULL OR char_length(scheduling_notes) BETWEEN 1 AND 1000",
            name="scheduling_notes_bounded",
        ),
        CheckConstraint("supersedes_id IS NULL OR supersedes_id <> id", name="supersedes_not_self"),
        CheckConstraint(
            "superseded_by_id IS NULL OR superseded_by_id <> id", name="superseded_by_not_self"
        ),
        CheckConstraint(
            "(state = 'Draft' AND submitted_at IS NULL AND terminal_at IS NULL "
            "AND current_approval_id IS NULL) OR (state = 'PendingApproval' "
            "AND submitted_at IS NOT NULL AND terminal_at IS NULL "
            "AND current_approval_id IS NULL) OR (state = 'Approved' "
            "AND submitted_at IS NOT NULL AND terminal_at IS NULL "
            "AND current_approval_id IS NOT NULL) OR (state = 'Rejected' "
            "AND submitted_at IS NOT NULL AND terminal_at IS NOT NULL "
            "AND current_approval_id IS NULL) OR (state = 'Superseded' "
            "AND terminal_at IS NOT NULL AND current_approval_id IS NULL) OR "
            "(state IN ('PendingExecution','RetryableExecutionFailure','Executed',"
            "'TerminalExecutionFailure') AND submitted_at IS NOT NULL)",
            name="lifecycle_fields_consistent",
        ),
        Index(
            "uq_proposed_actions_one_unsuperseded",
            "service_request_id",
            "proposal_series_id",
            unique=True,
            postgresql_where=text("state NOT IN ('Rejected', 'Superseded')"),
        ),
        Index("ix_proposed_actions_request_created", "service_request_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("service_requests.id", name="fk_proposed_action_request", ondelete="RESTRICT"),
        nullable=False,
    )
    proposal_series_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    proposal_number: Mapped[int] = mapped_column(Integer, nullable=False)
    logical_operation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    destination_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    destination_value: Mapped[str] = mapped_column(String(320), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    scheduling_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduling_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduling_notes: Mapped[str | None] = mapped_column(Text)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    creator_actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("application_actors.id", name="fk_proposed_action_creator", ondelete="RESTRICT"),
        nullable=False,
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    current_approval_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ProposedActionContributor(Base):
    __tablename__ = "proposed_action_contributors"
    __table_args__ = (
        UniqueConstraint(
            "proposed_action_id", "actor_id", name="uq_proposal_contributors_action_actor"
        ),
        UniqueConstraint(
            "id", "proposed_action_id", name="uq_proposal_contributors_action_identity"
        ),
        ForeignKeyConstraint(
            ("source_proposal_id", "service_request_id", "proposal_series_id"),
            (
                "proposed_actions.id",
                "proposed_actions.service_request_id",
                "proposed_actions.proposal_series_id",
            ),
            name="fk_proposal_contributor_source_identity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("proposed_action_id", "service_request_id", "proposal_series_id"),
            (
                "proposed_actions.id",
                "proposed_actions.service_request_id",
                "proposed_actions.proposal_series_id",
            ),
            name="fk_proposal_contributor_action_identity",
            ondelete="RESTRICT",
        ),
        CheckConstraint("contribution_kind IN ('Creator','MaterialEditor')", name="kind_valid"),
        CheckConstraint(
            "(carried_forward AND source_proposal_id IS NOT NULL) OR "
            "(NOT carried_forward AND source_proposal_id IS NULL)",
            name="carry_forward_consistent",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    proposed_action_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    service_request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    proposal_series_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "application_actors.id", name="fk_proposal_contributor_actor", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    contribution_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    carried_forward: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    source_proposal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProposalApprovalExclusion(Base):
    __tablename__ = "proposal_approval_exclusions"
    __table_args__ = (
        UniqueConstraint(
            "proposed_action_id", "excluded_actor_id", name="uq_proposal_exclusions_action_actor"
        ),
        ForeignKeyConstraint(
            ("source_contributor_id", "proposed_action_id"),
            ("proposed_action_contributors.id", "proposed_action_contributors.proposed_action_id"),
            name="fk_proposal_exclusion_source_contributor",
            ondelete="RESTRICT",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    proposed_action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("proposed_actions.id", name="fk_proposal_exclusion_action", ondelete="RESTRICT"),
        nullable=False,
    )
    excluded_actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "application_actors.id", name="fk_proposal_exclusion_actor", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    source_contributor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    frozen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ApprovalDecision(Base):
    __tablename__ = "approval_decisions"
    __table_args__ = (
        UniqueConstraint("proposed_action_id", name="uq_approval_decisions_proposed_action"),
        UniqueConstraint("id", "proposed_action_id", name="uq_approval_decisions_action_identity"),
        ForeignKeyConstraint(
            ("proposed_action_id", "proposal_number", "payload_digest"),
            (
                "proposed_actions.id",
                "proposed_actions.proposal_number",
                "proposed_actions.payload_digest",
            ),
            name="fk_approval_decision_exact_proposal",
            ondelete="RESTRICT",
        ),
        CheckConstraint("decision IN ('Approved','Rejected')", name="decision_valid"),
        CheckConstraint(
            "role_at_decision IN ('ManagerApprover','Administrator')", name="role_valid"
        ),
        CheckConstraint("payload_digest ~ '^[0-9a-f]{64}$'", name="payload_digest_valid"),
        CheckConstraint(
            "rationale_digest IS NULL OR rationale_digest ~ '^[0-9a-f]{64}$'",
            name="rationale_digest_valid",
        ),
        CheckConstraint(
            "(decision = 'Approved' AND rationale_digest IS NULL) OR "
            "(decision = 'Rejected' AND rationale_digest IS NOT NULL)",
            name="rationale_consistent",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    proposed_action_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    proposal_number: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    approver_actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "application_actors.id", name="fk_approval_decision_approver", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    role_at_decision: Mapped[str] = mapped_column(String(32), nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    command_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    rationale_digest: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# Break the exact approval cycle after both tables exist.
ProposedAction.__table__.append_constraint(
    ForeignKeyConstraint(
        [ProposedAction.__table__.c.current_approval_id, ProposedAction.__table__.c.id],
        [ApprovalDecision.__table__.c.id, ApprovalDecision.__table__.c.proposed_action_id],
        name="fk_proposed_action_current_approval_identity",
        ondelete="RESTRICT",
        use_alter=True,
    )
)
