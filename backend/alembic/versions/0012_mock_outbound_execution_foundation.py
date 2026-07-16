"""Mock outbound execution persistence and exact binding foundation.

Revision ID: 0012_mock_outbound_execution_foundation
Revises: 0011_proposal_approval_foundation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_mock_outbound_execution_foundation"
down_revision: str | None = "0011_proposal_approval_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _upgrade_operations() -> None:
    op.drop_constraint(
        "ck_logical_operations_kind_fields_consistent", "logical_operations", type_="check"
    )
    op.add_column("logical_operations", sa.Column("outbound_key_scope", sa.String(100)))
    op.add_column("logical_operations", sa.Column("outbound_key_digest", sa.String(64)))
    op.drop_column("logical_operations", "outbound_execution_key")
    op.create_unique_constraint(
        "uq_logical_operations_outbound_binding",
        "logical_operations",
        [
            "id",
            "service_request_id",
            "proposal_series_id",
            "outbound_key_scope",
            "outbound_key_digest",
        ],
    )
    op.create_unique_constraint(
        "uq_logical_operations_outbound_key",
        "logical_operations",
        ["outbound_key_scope", "outbound_key_digest"],
    )
    op.create_check_constraint(
        "ck_logical_operations_kind_fields_consistent",
        "logical_operations",
        "(operation_kind = 'AIInterpretation' AND proposal_series_id IS NULL "
        "AND input_hash IS NOT NULL AND configuration_hash IS NOT NULL "
        "AND prompt_version IS NOT NULL AND result_schema_version IS NOT NULL "
        "AND provider_name IS NOT NULL AND model_name IS NOT NULL "
        "AND adapter_name IS NOT NULL AND adapter_version IS NOT NULL "
        "AND outbound_key_scope IS NULL AND outbound_key_digest IS NULL) OR "
        "(operation_kind = 'OutboundAction' AND proposal_series_id IS NOT NULL "
        "AND input_hash IS NULL AND configuration_hash IS NULL "
        "AND prompt_version IS NULL AND result_schema_version IS NULL "
        "AND provider_name IS NULL AND model_name IS NULL "
        "AND adapter_name IS NULL AND adapter_version IS NULL "
        "AND ((outbound_key_scope IS NULL AND outbound_key_digest IS NULL) OR "
        "(char_length(trim(outbound_key_scope)) > 0 "
        "AND outbound_key_digest ~ '^[0-9a-f]{64}$')))",
    )


def _upgrade_attempts() -> None:
    for name in (
        "ck_integration_attempts_operation_kind_valid",
        "ck_integration_attempts_state_fields_consistent",
    ):
        op.drop_constraint(name, "integration_attempts", type_="check")
    op.drop_constraint(
        op.f("ck_integration_attempts_ai_recovery_assessment_valid"),
        "integration_attempts",
        type_="check",
    )

    for column in (
        sa.Column("proposal_series_id", postgresql.UUID(as_uuid=True)),
        sa.Column("proposed_action_id", postgresql.UUID(as_uuid=True)),
        sa.Column("proposal_number", sa.Integer()),
        sa.Column("proposal_payload_digest", sa.String(64)),
        sa.Column("approval_decision_id", postgresql.UUID(as_uuid=True)),
        sa.Column("stable_outbound_key_scope", sa.String(100)),
        sa.Column("stable_outbound_key_digest", sa.String(64)),
    ):
        op.add_column("integration_attempts", column)

    op.create_unique_constraint(
        "uq_integration_attempts_credential_identity",
        "integration_attempts",
        ["id", "operation_kind"],
    )

    op.create_unique_constraint(
        "uq_proposed_actions_execution_identity",
        "proposed_actions",
        [
            "id",
            "service_request_id",
            "proposal_series_id",
            "proposal_number",
            "payload_digest",
        ],
    )
    op.create_unique_constraint(
        "uq_approval_decisions_execution_identity",
        "approval_decisions",
        ["id", "proposed_action_id", "proposal_number", "payload_digest"],
    )
    op.create_foreign_key(
        "fk_attempt_exact_outbound_operation",
        "integration_attempts",
        "logical_operations",
        [
            "logical_operation_id",
            "service_request_id",
            "proposal_series_id",
            "stable_outbound_key_scope",
            "stable_outbound_key_digest",
        ],
        [
            "id",
            "service_request_id",
            "proposal_series_id",
            "outbound_key_scope",
            "outbound_key_digest",
        ],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_attempt_exact_outbound_proposal",
        "integration_attempts",
        "proposed_actions",
        [
            "proposed_action_id",
            "service_request_id",
            "proposal_series_id",
            "proposal_number",
            "proposal_payload_digest",
        ],
        ["id", "service_request_id", "proposal_series_id", "proposal_number", "payload_digest"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_attempt_exact_outbound_approval",
        "integration_attempts",
        "approval_decisions",
        [
            "approval_decision_id",
            "proposed_action_id",
            "proposal_number",
            "proposal_payload_digest",
        ],
        ["id", "proposed_action_id", "proposal_number", "payload_digest"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_integration_attempts_operation_kind_valid",
        "integration_attempts",
        "operation_kind IN ('AIInterpretation', 'OutboundAction')",
    )
    op.create_check_constraint(
        "ck_integration_attempts_kind_binding_consistent",
        "integration_attempts",
        "(operation_kind = 'AIInterpretation' AND proposal_series_id IS NULL "
        "AND proposed_action_id IS NULL AND proposal_number IS NULL "
        "AND proposal_payload_digest IS NULL AND approval_decision_id IS NULL "
        "AND stable_outbound_key_scope IS NULL AND stable_outbound_key_digest IS NULL) OR "
        "(operation_kind = 'OutboundAction' AND proposal_series_id IS NOT NULL "
        "AND proposed_action_id IS NOT NULL AND proposal_number IS NOT NULL "
        "AND proposal_number > 0 AND proposal_payload_digest ~ '^[0-9a-f]{64}$' "
        "AND approval_decision_id IS NOT NULL "
        "AND char_length(trim(stable_outbound_key_scope)) > 0 "
        "AND stable_outbound_key_digest ~ '^[0-9a-f]{64}$')",
    )
    op.create_check_constraint(
        op.f("ck_integration_attempts_ai_recovery_assessment_valid"),
        "integration_attempts",
        "failure_policy_id IS NULL OR operation_kind <> 'AIInterpretation' OR "
        "(customer_side_effect = 'NotApplicable' "
        "AND reconciliation_status = 'NotRequired' AND reconciliation_deadline IS NULL "
        "AND ((state = 'RetryableFailure' "
        "AND recovery_disposition = 'RetrySameOperation' "
        "AND remaining_attempts > 0 AND next_eligible_at IS NOT NULL "
        "AND next_eligible_at >= assessed_at "
        "AND (provider_retry_after_at IS NULL OR (provider_retry_after_at >= assessed_at "
        "AND next_eligible_at >= provider_retry_after_at)) AND terminal_reason IS NULL) OR "
        "(state = 'TerminalFailure' AND recovery_disposition = 'Terminal' "
        "AND next_eligible_at IS NULL AND provider_retry_after_at IS NULL "
        "AND terminal_reason IS NOT NULL)))",
    )
    op.create_check_constraint(
        "ck_integration_attempts_outbound_recovery_assessment_valid",
        "integration_attempts",
        "failure_policy_id IS NULL OR operation_kind <> 'OutboundAction' OR "
        "((state = 'Running' AND customer_side_effect = 'Unknown' "
        "AND recovery_disposition = 'ReconcileBeforeRetry' "
        "AND reconciliation_status = 'Required' AND reconciliation_deadline IS NOT NULL "
        "AND next_eligible_at IS NULL AND provider_retry_after_at IS NULL "
        "AND terminal_reason IS NULL) OR "
        "(state = 'RetryableFailure' AND customer_side_effect = 'KnownNotApplied' "
        "AND recovery_disposition IN ('RetrySameOperation', 'ReviseProposal') "
        "AND reconciliation_status = 'NotRequired' AND reconciliation_deadline IS NULL "
        "AND remaining_attempts > 0 "
        "AND ((recovery_disposition = 'RetrySameOperation' AND next_eligible_at IS NOT NULL) "
        "OR (recovery_disposition = 'ReviseProposal' AND next_eligible_at IS NULL)) "
        "AND terminal_reason IS NULL) OR "
        "(state = 'TerminalFailure' AND recovery_disposition = 'Terminal' "
        "AND reconciliation_status = 'NotRequired' AND reconciliation_deadline IS NULL "
        "AND next_eligible_at IS NULL AND terminal_reason IS NOT NULL))",
    )
    op.create_check_constraint(
        "ck_integration_attempts_state_fields_consistent",
        "integration_attempts",
        "(state = 'Pending' AND started_at IS NULL AND completed_at IS NULL "
        "AND result_hash IS NULL AND sanitized_error_code IS NULL) OR "
        "(state = 'Running' AND started_at IS NOT NULL AND completed_at IS NULL "
        "AND result_hash IS NULL AND (sanitized_error_code IS NULL OR "
        "(operation_kind = 'OutboundAction' AND failure_policy_id IS NOT NULL "
        "AND reconciliation_status = 'Required'))) OR "
        "(state = 'Succeeded' AND started_at IS NOT NULL AND completed_at IS NOT NULL "
        "AND result_hash IS NOT NULL AND sanitized_error_code IS NULL) OR "
        "(state IN ('RetryableFailure', 'TerminalFailure') AND completed_at IS NOT NULL "
        "AND result_hash IS NULL AND sanitized_error_code IS NOT NULL)",
    )


def _upgrade_credentials() -> None:
    op.drop_constraint(
        "ck_attempt_callback_credentials_operation_kind_valid",
        "attempt_callback_credentials",
        type_="check",
    )
    op.create_check_constraint(
        "ck_attempt_callback_credentials_operation_kind_valid",
        "attempt_callback_credentials",
        "operation_kind IN ('AIInterpretation', 'OutboundAction')",
    )
    op.create_foreign_key(
        "fk_callback_credential_attempt_kind",
        "attempt_callback_credentials",
        "integration_attempts",
        ["integration_attempt_id", "operation_kind"],
        ["id", "operation_kind"],
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    _upgrade_operations()
    _upgrade_attempts()
    _upgrade_credentials()


def downgrade() -> None:
    op.execute(
        "ALTER TABLE attempt_callback_credentials "
        "DROP CONSTRAINT IF EXISTS fk_callback_credential_attempt_kind"
    )
    op.execute(
        "DELETE FROM command_idempotency_records WHERE callback_credential_id IN "
        "(SELECT id FROM attempt_callback_credentials "
        "WHERE operation_kind = 'OutboundAction') OR "
        "callback_authorization_credential_id IN "
        "(SELECT id FROM attempt_callback_credentials "
        "WHERE operation_kind = 'OutboundAction')"
    )
    op.execute(
        "UPDATE service_requests SET status = 'ActionRevisionRequired', "
        "current_queue = CASE WHEN priority IN ('Low', 'Normal') THEN 'StandardRequests' "
        "WHEN priority = 'High' THEN 'PriorityRequests' ELSE 'HumanReview' END, "
        "recovery_target = NULL, recovery_attempt_id = NULL, "
        "failure_summary_code = NULL, terminal_at = NULL "
        "WHERE recovery_attempt_id IN "
        "(SELECT id FROM integration_attempts WHERE operation_kind = 'OutboundAction')"
    )
    op.execute(
        "UPDATE logical_operations SET succeeded_attempt_id = NULL "
        "WHERE succeeded_attempt_id IN "
        "(SELECT id FROM integration_attempts WHERE operation_kind = 'OutboundAction')"
    )
    op.execute("DELETE FROM attempt_callback_credentials WHERE operation_kind = 'OutboundAction'")
    op.execute("DELETE FROM integration_attempts WHERE operation_kind = 'OutboundAction'")
    op.drop_constraint(
        "ck_attempt_callback_credentials_operation_kind_valid",
        "attempt_callback_credentials",
        type_="check",
    )
    op.create_check_constraint(
        "ck_attempt_callback_credentials_operation_kind_valid",
        "attempt_callback_credentials",
        "operation_kind = 'AIInterpretation'",
    )

    for name in (
        "ck_integration_attempts_state_fields_consistent",
        "ck_integration_attempts_outbound_recovery_assessment_valid",
        "ck_integration_attempts_kind_binding_consistent",
        "ck_integration_attempts_operation_kind_valid",
    ):
        op.drop_constraint(name, "integration_attempts", type_="check")
    op.drop_constraint(
        op.f("ck_integration_attempts_ai_recovery_assessment_valid"),
        "integration_attempts",
        type_="check",
    )
    for name in (
        "fk_attempt_exact_outbound_approval",
        "fk_attempt_exact_outbound_proposal",
        "fk_attempt_exact_outbound_operation",
    ):
        op.drop_constraint(name, "integration_attempts", type_="foreignkey")
    op.drop_constraint(
        "uq_approval_decisions_execution_identity", "approval_decisions", type_="unique"
    )
    op.drop_constraint("uq_proposed_actions_execution_identity", "proposed_actions", type_="unique")
    op.execute(
        "ALTER TABLE integration_attempts "
        "DROP CONSTRAINT IF EXISTS uq_integration_attempts_credential_identity"
    )
    for column in (
        "stable_outbound_key_digest",
        "stable_outbound_key_scope",
        "approval_decision_id",
        "proposal_payload_digest",
        "proposal_number",
        "proposed_action_id",
        "proposal_series_id",
    ):
        op.drop_column("integration_attempts", column)
    op.create_check_constraint(
        "ck_integration_attempts_operation_kind_valid",
        "integration_attempts",
        "operation_kind = 'AIInterpretation'",
    )
    op.create_check_constraint(
        op.f("ck_integration_attempts_ai_recovery_assessment_valid"),
        "integration_attempts",
        "failure_policy_id IS NULL OR (operation_kind = 'AIInterpretation' "
        "AND customer_side_effect = 'NotApplicable' "
        "AND reconciliation_status = 'NotRequired' AND reconciliation_deadline IS NULL "
        "AND ((state = 'RetryableFailure' AND recovery_disposition = 'RetrySameOperation' "
        "AND remaining_attempts > 0 AND next_eligible_at IS NOT NULL "
        "AND next_eligible_at >= assessed_at "
        "AND (provider_retry_after_at IS NULL OR (provider_retry_after_at >= assessed_at "
        "AND next_eligible_at >= provider_retry_after_at)) AND terminal_reason IS NULL) OR "
        "(state = 'TerminalFailure' AND recovery_disposition = 'Terminal' "
        "AND next_eligible_at IS NULL AND provider_retry_after_at IS NULL "
        "AND terminal_reason IS NOT NULL)))",
    )
    op.create_check_constraint(
        "ck_integration_attempts_state_fields_consistent",
        "integration_attempts",
        "(state = 'Pending' AND started_at IS NULL AND completed_at IS NULL "
        "AND result_hash IS NULL AND sanitized_error_code IS NULL) OR "
        "(state = 'Running' AND started_at IS NOT NULL AND completed_at IS NULL "
        "AND result_hash IS NULL AND sanitized_error_code IS NULL) OR "
        "(state = 'Succeeded' AND started_at IS NOT NULL AND completed_at IS NOT NULL "
        "AND result_hash IS NOT NULL AND sanitized_error_code IS NULL) OR "
        "(state IN ('RetryableFailure', 'TerminalFailure') AND completed_at IS NOT NULL "
        "AND result_hash IS NULL AND sanitized_error_code IS NOT NULL)",
    )

    op.drop_constraint(
        "ck_logical_operations_kind_fields_consistent", "logical_operations", type_="check"
    )
    op.drop_constraint("uq_logical_operations_outbound_key", "logical_operations", type_="unique")
    op.drop_constraint(
        "uq_logical_operations_outbound_binding", "logical_operations", type_="unique"
    )
    op.add_column("logical_operations", sa.Column("outbound_execution_key", sa.String(128)))
    op.drop_column("logical_operations", "outbound_key_digest")
    op.drop_column("logical_operations", "outbound_key_scope")
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
