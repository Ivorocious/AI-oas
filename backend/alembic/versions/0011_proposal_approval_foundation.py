"""Proposal, approval, rejection, and material-revision foundation.

Revision ID: 0011_proposal_approval_foundation
Revises: 0010_deterministic_triage_foundation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011_proposal_approval_foundation"
down_revision: str | None = "0010_deterministic_triage_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _generalize_operations() -> None:
    for name in (
        "ck_logical_operations_operation_kind_valid",
        "ck_logical_operations_input_hash_valid",
        "ck_logical_operations_configuration_hash_valid",
        "ck_logical_operations_prompt_version_not_blank",
        "ck_logical_operations_result_schema_version_not_blank",
        "ck_logical_operations_provider_name_not_blank",
        "ck_logical_operations_model_name_not_blank",
        "ck_logical_operations_adapter_name_not_blank",
        "ck_logical_operations_adapter_version_not_blank",
    ):
        op.drop_constraint(name, "logical_operations", type_="check")
    op.add_column(
        "logical_operations", sa.Column("proposal_series_id", postgresql.UUID(as_uuid=True))
    )
    op.add_column("logical_operations", sa.Column("outbound_execution_key", sa.String(128)))
    for column in (
        "input_hash",
        "configuration_hash",
        "prompt_version",
        "result_schema_version",
        "provider_name",
        "model_name",
        "adapter_name",
        "adapter_version",
    ):
        op.alter_column("logical_operations", column, existing_type=sa.String(), nullable=True)
    op.create_unique_constraint(
        "uq_logical_operations_outbound_series",
        "logical_operations",
        ["service_request_id", "proposal_series_id"],
    )
    op.create_unique_constraint(
        "uq_logical_operations_outbound_identity",
        "logical_operations",
        ["id", "service_request_id", "proposal_series_id"],
    )
    op.create_check_constraint(
        "ck_logical_operations_operation_kind_valid",
        "logical_operations",
        "operation_kind IN ('AIInterpretation', 'OutboundAction')",
    )
    op.create_check_constraint(
        "ck_logical_operations_input_hash_valid",
        "logical_operations",
        "input_hash IS NULL OR input_hash ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_logical_operations_configuration_hash_valid",
        "logical_operations",
        "configuration_hash IS NULL OR configuration_hash ~ '^[0-9a-f]{64}$'",
    )
    for column in (
        "prompt_version",
        "result_schema_version",
        "provider_name",
        "model_name",
        "adapter_name",
        "adapter_version",
    ):
        op.create_check_constraint(
            f"ck_logical_operations_{column}_not_blank",
            "logical_operations",
            f"{column} IS NULL OR char_length(trim({column})) > 0",
        )
    op.create_check_constraint(
        "ck_logical_operations_kind_fields_consistent",
        "logical_operations",
        "(operation_kind = 'AIInterpretation' AND proposal_series_id IS NULL "
        "AND input_hash IS NOT NULL AND configuration_hash IS NOT NULL "
        "AND prompt_version IS NOT NULL AND result_schema_version IS NOT NULL "
        "AND provider_name IS NOT NULL AND model_name IS NOT NULL "
        "AND adapter_name IS NOT NULL AND adapter_version IS NOT NULL) OR "
        "(operation_kind = 'OutboundAction' AND proposal_series_id IS NOT NULL "
        "AND input_hash IS NULL AND configuration_hash IS NULL "
        "AND prompt_version IS NULL AND result_schema_version IS NULL "
        "AND provider_name IS NULL AND model_name IS NULL "
        "AND adapter_name IS NULL AND adapter_version IS NULL "
        "AND outbound_execution_key IS NULL)",
    )


def _create_proposals() -> None:
    op.create_table(
        "proposed_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposal_series_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposal_number", sa.Integer(), nullable=False),
        sa.Column("logical_operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("action_type", sa.String(32), nullable=False),
        sa.Column("destination_kind", sa.String(16), nullable=False),
        sa.Column("destination_value", sa.String(320), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("scheduling_window_start", sa.DateTime(timezone=True)),
        sa.Column("scheduling_window_end", sa.DateTime(timezone=True)),
        sa.Column("scheduling_notes", sa.Text()),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("creator_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True)),
        sa.Column("superseded_by_id", postgresql.UUID(as_uuid=True)),
        sa.Column("current_approval_id", postgresql.UUID(as_uuid=True)),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("terminal_at", sa.DateTime(timezone=True)),
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
            "proposal_number > 0", name="ck_proposed_actions_proposal_number_positive"
        ),
        sa.CheckConstraint("version > 0", name="ck_proposed_actions_version_positive"),
        sa.CheckConstraint(
            "state IN ('Draft','PendingApproval','Approved','Rejected','PendingExecution',"
            "'RetryableExecutionFailure','Executed','TerminalExecutionFailure','Superseded')",
            name="ck_proposed_actions_state_valid",
        ),
        sa.CheckConstraint(
            "action_type IN ('CustomerMessage','SchedulingInvitation')",
            name="ck_proposed_actions_action_type_valid",
        ),
        sa.CheckConstraint(
            "destination_kind IN ('Email','Phone')",
            name="ck_proposed_actions_destination_kind_valid",
        ),
        sa.CheckConstraint(
            "char_length(trim(destination_value)) BETWEEN 3 AND 320",
            name="ck_proposed_actions_destination_value_bounded",
        ),
        sa.CheckConstraint(
            "char_length(content) BETWEEN 1 AND 10000", name="ck_proposed_actions_content_bounded"
        ),
        sa.CheckConstraint(
            "payload_digest ~ '^[0-9a-f]{64}$'", name="ck_proposed_actions_payload_digest_valid"
        ),
        sa.CheckConstraint(
            "scheduling_window_end IS NULL OR scheduling_window_start IS NOT NULL",
            name="ck_proposed_actions_schedule_complete",
        ),
        sa.CheckConstraint(
            "scheduling_window_end IS NULL OR scheduling_window_end > scheduling_window_start",
            name="ck_proposed_actions_schedule_ordered",
        ),
        sa.CheckConstraint(
            "scheduling_notes IS NULL OR char_length(scheduling_notes) BETWEEN 1 AND 1000",
            name="ck_proposed_actions_scheduling_notes_bounded",
        ),
        sa.CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id",
            name="ck_proposed_actions_supersedes_not_self",
        ),
        sa.CheckConstraint(
            "superseded_by_id IS NULL OR superseded_by_id <> id",
            name="ck_proposed_actions_superseded_by_not_self",
        ),
        sa.CheckConstraint(
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
            name="ck_proposed_actions_lifecycle_fields_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["service_request_id"],
            ["service_requests.id"],
            name="fk_proposed_action_request",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["creator_actor_id"],
            ["application_actors.id"],
            name="fk_proposed_action_creator",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["logical_operation_id", "service_request_id", "proposal_series_id"],
            [
                "logical_operations.id",
                "logical_operations.service_request_id",
                "logical_operations.proposal_series_id",
            ],
            name="fk_proposed_action_operation_identity",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_proposed_actions"),
        sa.UniqueConstraint(
            "service_request_id",
            "proposal_series_id",
            "proposal_number",
            name="uq_proposed_actions_series_number",
        ),
        sa.UniqueConstraint(
            "id", "service_request_id", name="uq_proposed_actions_request_identity"
        ),
        sa.UniqueConstraint(
            "id",
            "service_request_id",
            "proposal_series_id",
            name="uq_proposed_actions_series_identity",
        ),
        sa.UniqueConstraint(
            "id", "proposal_number", "payload_digest", name="uq_proposed_actions_decision_identity"
        ),
    )
    op.create_foreign_key(
        "fk_proposed_action_supersedes_identity",
        "proposed_actions",
        "proposed_actions",
        ["supersedes_id", "service_request_id", "proposal_series_id"],
        ["id", "service_request_id", "proposal_series_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_proposed_action_superseded_by_identity",
        "proposed_actions",
        "proposed_actions",
        ["superseded_by_id", "service_request_id", "proposal_series_id"],
        ["id", "service_request_id", "proposal_series_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_proposed_actions_one_unsuperseded",
        "proposed_actions",
        ["service_request_id", "proposal_series_id"],
        unique=True,
        postgresql_where=sa.text("state NOT IN ('Rejected', 'Superseded')"),
    )
    op.create_index(
        "ix_proposed_actions_request_created",
        "proposed_actions",
        ["service_request_id", "created_at"],
    )


def _create_attribution_and_decisions() -> None:
    op.create_table(
        "proposed_action_contributors",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposed_action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposal_series_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contribution_kind", sa.String(24), nullable=False),
        sa.Column("carried_forward", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("source_proposal_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "contribution_kind IN ('Creator','MaterialEditor')",
            name="ck_proposed_action_contributors_kind_valid",
        ),
        sa.CheckConstraint(
            "(carried_forward AND source_proposal_id IS NOT NULL) OR "
            "(NOT carried_forward AND source_proposal_id IS NULL)",
            name="ck_proposed_action_contributors_carry_forward_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["application_actors.id"],
            name="fk_proposal_contributor_actor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["proposed_action_id", "service_request_id", "proposal_series_id"],
            [
                "proposed_actions.id",
                "proposed_actions.service_request_id",
                "proposed_actions.proposal_series_id",
            ],
            name="fk_proposal_contributor_action_identity",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_proposal_id", "service_request_id", "proposal_series_id"],
            [
                "proposed_actions.id",
                "proposed_actions.service_request_id",
                "proposed_actions.proposal_series_id",
            ],
            name="fk_proposal_contributor_source_identity",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_proposed_action_contributors"),
        sa.UniqueConstraint(
            "proposed_action_id", "actor_id", name="uq_proposal_contributors_action_actor"
        ),
        sa.UniqueConstraint(
            "id", "proposed_action_id", name="uq_proposal_contributors_action_identity"
        ),
    )
    op.create_table(
        "proposal_approval_exclusions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposed_action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("excluded_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_contributor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "frozen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["proposed_action_id"],
            ["proposed_actions.id"],
            name="fk_proposal_exclusion_action",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["excluded_actor_id"],
            ["application_actors.id"],
            name="fk_proposal_exclusion_actor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_contributor_id", "proposed_action_id"],
            ["proposed_action_contributors.id", "proposed_action_contributors.proposed_action_id"],
            name="fk_proposal_exclusion_source_contributor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_proposal_approval_exclusions"),
        sa.UniqueConstraint(
            "proposed_action_id", "excluded_actor_id", name="uq_proposal_exclusions_action_actor"
        ),
    )
    op.create_table(
        "approval_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposed_action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposal_number", sa.Integer(), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("approver_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_at_decision", sa.String(32), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rationale_digest", sa.String(64)),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('Approved','Rejected')", name="ck_approval_decisions_decision_valid"
        ),
        sa.CheckConstraint(
            "role_at_decision IN ('ManagerApprover','Administrator')",
            name="ck_approval_decisions_role_valid",
        ),
        sa.CheckConstraint(
            "payload_digest ~ '^[0-9a-f]{64}$'", name="ck_approval_decisions_payload_digest_valid"
        ),
        sa.CheckConstraint(
            "rationale_digest IS NULL OR rationale_digest ~ '^[0-9a-f]{64}$'",
            name="ck_approval_decisions_rationale_digest_valid",
        ),
        sa.CheckConstraint(
            "(decision = 'Approved' AND rationale_digest IS NULL) OR "
            "(decision = 'Rejected' AND rationale_digest IS NOT NULL)",
            name="ck_approval_decisions_rationale_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["proposed_action_id", "proposal_number", "payload_digest"],
            [
                "proposed_actions.id",
                "proposed_actions.proposal_number",
                "proposed_actions.payload_digest",
            ],
            name="fk_approval_decision_exact_proposal",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approver_actor_id"],
            ["application_actors.id"],
            name="fk_approval_decision_approver",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_approval_decisions"),
        sa.UniqueConstraint("proposed_action_id", name="uq_approval_decisions_proposed_action"),
        sa.UniqueConstraint(
            "id", "proposed_action_id", name="uq_approval_decisions_action_identity"
        ),
    )
    op.create_foreign_key(
        "fk_proposed_action_current_approval_identity",
        "proposed_actions",
        "approval_decisions",
        ["current_approval_id", "id"],
        ["id", "proposed_action_id"],
        ondelete="RESTRICT",
    )


def _add_request_reference() -> None:
    op.add_column(
        "service_requests", sa.Column("current_proposed_action_id", postgresql.UUID(as_uuid=True))
    )
    op.create_foreign_key(
        "fk_service_request_current_proposed_action_identity",
        "service_requests",
        "proposed_actions",
        ["current_proposed_action_id", "id"],
        ["id", "service_request_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_service_requests_current_proposed_action_id",
        "service_requests",
        ["current_proposed_action_id"],
    )


def upgrade() -> None:
    _generalize_operations()
    _create_proposals()
    _create_attribution_and_decisions()
    _add_request_reference()


def downgrade() -> None:
    op.drop_index("ix_service_requests_current_proposed_action_id", table_name="service_requests")
    op.drop_constraint(
        "fk_service_request_current_proposed_action_identity",
        "service_requests",
        type_="foreignkey",
    )
    op.drop_column("service_requests", "current_proposed_action_id")
    op.drop_constraint(
        "fk_proposed_action_current_approval_identity",
        "proposed_actions",
        type_="foreignkey",
    )
    op.drop_table("approval_decisions")
    op.drop_table("proposal_approval_exclusions")
    op.drop_table("proposed_action_contributors")
    op.drop_table("proposed_actions")
    op.drop_constraint(
        "ck_logical_operations_kind_fields_consistent", "logical_operations", type_="check"
    )
    for column in (
        "prompt_version",
        "result_schema_version",
        "provider_name",
        "model_name",
        "adapter_name",
        "adapter_version",
    ):
        op.drop_constraint(
            f"ck_logical_operations_{column}_not_blank", "logical_operations", type_="check"
        )
    op.drop_constraint(
        "ck_logical_operations_configuration_hash_valid", "logical_operations", type_="check"
    )
    op.drop_constraint(
        "ck_logical_operations_input_hash_valid", "logical_operations", type_="check"
    )
    op.drop_constraint(
        "ck_logical_operations_operation_kind_valid", "logical_operations", type_="check"
    )
    op.drop_constraint(
        "uq_logical_operations_outbound_identity", "logical_operations", type_="unique"
    )
    op.drop_constraint(
        "uq_logical_operations_outbound_series", "logical_operations", type_="unique"
    )
    op.execute("DELETE FROM logical_operations WHERE operation_kind = 'OutboundAction'")
    op.drop_column("logical_operations", "outbound_execution_key")
    op.drop_column("logical_operations", "proposal_series_id")
    for column in (
        "input_hash",
        "configuration_hash",
        "prompt_version",
        "result_schema_version",
        "provider_name",
        "model_name",
        "adapter_name",
        "adapter_version",
    ):
        op.alter_column("logical_operations", column, existing_type=sa.String(), nullable=False)
    op.create_check_constraint(
        "ck_logical_operations_operation_kind_valid",
        "logical_operations",
        "operation_kind = 'AIInterpretation'",
    )
    op.create_check_constraint(
        "ck_logical_operations_input_hash_valid",
        "logical_operations",
        "input_hash ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_logical_operations_configuration_hash_valid",
        "logical_operations",
        "configuration_hash ~ '^[0-9a-f]{64}$'",
    )
    for column in (
        "prompt_version",
        "result_schema_version",
        "provider_name",
        "model_name",
        "adapter_name",
        "adapter_version",
    ):
        op.create_check_constraint(
            f"ck_logical_operations_{column}_not_blank",
            "logical_operations",
            f"char_length(trim({column})) > 0",
        )
