"""AI and mock-outbound operation, attempt, credential, and result evidence."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_operations_automation.db.base import Base
from ai_operations_automation.db.models.intake import SERVICE_CATEGORY_VALUES, _sql_values

HASH_CHECK = "{column} ~ '^[0-9a-f]{{64}}$'"
NONBLANK_FIELDS = (
    "prompt_version",
    "result_schema_version",
    "provider_name",
    "model_name",
    "adapter_name",
    "adapter_version",
)
FAILURE_STAGE_VALUES = (
    "BeforeDispatch",
    "Dispatch",
    "ProviderProcessing",
    "ResponseValidation",
    "CallbackDelivery",
    "Reconciliation",
    "InternalCommit",
)
PROVIDER_INVOCATION_VALUES = ("NotInvoked", "Invoked", "InvocationUnknown", "NotApplicable")
CUSTOMER_SIDE_EFFECT_VALUES = ("NotApplicable", "KnownNotApplied", "Applied", "Unknown")
RECOVERY_DISPOSITION_VALUES = (
    "RetrySameOperation",
    "ReviseProposal",
    "ReconcileBeforeRetry",
    "ReplaySameCommand",
    "Terminal",
    "NoDomainChange",
)
RECONCILIATION_STATUS_VALUES = (
    "NotRequired",
    "Required",
)


class LogicalOperation(Base):
    __tablename__ = "logical_operations"
    __table_args__ = (
        UniqueConstraint(
            "service_request_id",
            "input_hash",
            "configuration_hash",
            name="uq_logical_operations_ai_identity",
        ),
        UniqueConstraint(
            "service_request_id",
            "proposal_series_id",
            name="uq_logical_operations_outbound_series",
        ),
        UniqueConstraint(
            "id",
            "service_request_id",
            "proposal_series_id",
            name="uq_logical_operations_outbound_identity",
        ),
        UniqueConstraint(
            "id",
            "service_request_id",
            "proposal_series_id",
            "outbound_key_scope",
            "outbound_key_digest",
            name="uq_logical_operations_outbound_binding",
        ),
        UniqueConstraint(
            "outbound_key_scope",
            "outbound_key_digest",
            name="uq_logical_operations_outbound_key",
        ),
        CheckConstraint(
            "operation_kind IN ('AIInterpretation', 'OutboundAction')",
            name="operation_kind_valid",
        ),
        CheckConstraint(
            "input_hash IS NULL OR " + HASH_CHECK.format(column="input_hash"),
            name="input_hash_valid",
        ),
        CheckConstraint(
            HASH_CHECK.format(column="configuration_hash"), name="configuration_hash_valid"
        ),
        CheckConstraint("version > 0", name="version_positive"),
        *(
            CheckConstraint(
                f"{field} IS NULL OR char_length(trim({field})) > 0",
                name=f"{field}_not_blank",
            )
            for field in NONBLANK_FIELDS
        ),
        CheckConstraint(
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
            "AND ((outbound_key_scope IS NULL AND outbound_key_digest IS NULL) OR "
            "(char_length(trim(outbound_key_scope)) > 0 AND "
            "outbound_key_digest ~ '^[0-9a-f]{64}$')))",
            name="kind_fields_consistent",
        ),
        Index("ix_logical_operations_service_request_created", "service_request_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("service_requests.id", name="fk_logical_operation_request", ondelete="RESTRICT"),
        nullable=False,
    )
    operation_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    proposal_series_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    outbound_key_scope: Mapped[str | None] = mapped_column(String(100))
    outbound_key_digest: Mapped[str | None] = mapped_column(String(64))
    input_hash: Mapped[str | None] = mapped_column(String(64))
    configuration_hash: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(100))
    result_schema_version: Mapped[str | None] = mapped_column(String(100))
    provider_name: Mapped[str | None] = mapped_column(String(100))
    model_name: Mapped[str | None] = mapped_column(String(100))
    adapter_name: Mapped[str | None] = mapped_column(String(100))
    adapter_version: Mapped[str | None] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    succeeded_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "integration_attempts.id",
            name="fk_logical_operation_succeeded_attempt",
            ondelete="RESTRICT",
            use_alter=True,
        ),
    )
    safe_outcome_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class IntegrationAttempt(Base):
    __tablename__ = "integration_attempts"
    __table_args__ = (
        UniqueConstraint(
            "logical_operation_id",
            "attempt_number",
            name="uq_integration_attempts_operation_attempt",
        ),
        UniqueConstraint(
            "id",
            "operation_kind",
            name="uq_integration_attempts_credential_identity",
        ),
        ForeignKeyConstraint(
            (
                "failure_policy_id",
                "failure_policy_semantic_version",
                "failure_policy_revision",
                "failure_policy_digest",
            ),
            (
                "failure_recovery_policy_versions.id",
                "failure_recovery_policy_versions.semantic_version",
                "failure_recovery_policy_versions.revision",
                "failure_recovery_policy_versions.content_digest",
            ),
            name="fk_attempt_failure_recovery_policy_identity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "logical_operation_id",
                "service_request_id",
                "proposal_series_id",
                "stable_outbound_key_scope",
                "stable_outbound_key_digest",
            ),
            (
                "logical_operations.id",
                "logical_operations.service_request_id",
                "logical_operations.proposal_series_id",
                "logical_operations.outbound_key_scope",
                "logical_operations.outbound_key_digest",
            ),
            name="fk_attempt_exact_outbound_operation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "proposed_action_id",
                "service_request_id",
                "proposal_series_id",
                "proposal_number",
                "proposal_payload_digest",
            ),
            (
                "proposed_actions.id",
                "proposed_actions.service_request_id",
                "proposed_actions.proposal_series_id",
                "proposed_actions.proposal_number",
                "proposed_actions.payload_digest",
            ),
            name="fk_attempt_exact_outbound_proposal",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "approval_decision_id",
                "proposed_action_id",
                "proposal_number",
                "proposal_payload_digest",
            ),
            (
                "approval_decisions.id",
                "approval_decisions.proposed_action_id",
                "approval_decisions.proposal_number",
                "approval_decisions.payload_digest",
            ),
            name="fk_attempt_exact_outbound_approval",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "operation_kind IN ('AIInterpretation', 'OutboundAction')",
            name="operation_kind_valid",
        ),
        CheckConstraint("attempt_number BETWEEN 1 AND 3", name="attempt_number_valid"),
        CheckConstraint(
            "state IN ('Pending', 'Running', 'Succeeded', 'RetryableFailure', 'TerminalFailure')",
            name="state_valid",
        ),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            "callback_authorization_deadline > created_at", name="callback_deadline_valid"
        ),
        CheckConstraint(
            f"result_hash IS NULL OR {HASH_CHECK.format(column='result_hash')}",
            name="result_hash_valid",
        ),
        CheckConstraint(
            "sanitized_error_code IS NULL OR sanitized_error_code ~ '^[A-Z][A-Z0-9_]{0,99}$'",
            name="error_code_valid",
        ),
        CheckConstraint(
            "failure_policy_digest IS NULL OR failure_policy_digest ~ '^[0-9a-f]{64}$'",
            name="failure_policy_digest_valid",
        ),
        CheckConstraint(
            "sanitized_evidence_hash IS NULL OR sanitized_evidence_hash ~ '^[0-9a-f]{64}$'",
            name="sanitized_evidence_hash_valid",
        ),
        CheckConstraint(
            "terminal_reason IS NULL OR terminal_reason ~ '^[A-Z][A-Z0-9_]{0,99}$'",
            name="terminal_reason_valid",
        ),
        CheckConstraint(
            f"failure_stage IS NULL OR failure_stage IN ({_sql_values(FAILURE_STAGE_VALUES)})",
            name="failure_stage_valid",
        ),
        CheckConstraint(
            "provider_invocation IS NULL OR provider_invocation IN "
            f"({_sql_values(PROVIDER_INVOCATION_VALUES)})",
            name="provider_invocation_valid",
        ),
        CheckConstraint(
            "customer_side_effect IS NULL OR customer_side_effect IN "
            f"({_sql_values(CUSTOMER_SIDE_EFFECT_VALUES)})",
            name="customer_side_effect_valid",
        ),
        CheckConstraint(
            "recovery_disposition IS NULL OR recovery_disposition IN "
            f"({_sql_values(RECOVERY_DISPOSITION_VALUES)})",
            name="recovery_disposition_valid",
        ),
        CheckConstraint(
            "reconciliation_status IS NULL OR reconciliation_status IN "
            f"({_sql_values(RECONCILIATION_STATUS_VALUES)})",
            name="reconciliation_status_valid",
        ),
        CheckConstraint(
            "(failure_policy_id IS NULL AND failure_policy_semantic_version IS NULL "
            "AND failure_policy_revision IS NULL AND failure_policy_digest IS NULL "
            "AND failure_stage IS NULL AND provider_invocation IS NULL "
            "AND customer_side_effect IS NULL AND recovery_disposition IS NULL "
            "AND maximum_attempts IS NULL AND remaining_attempts IS NULL "
            "AND next_eligible_at IS NULL AND provider_retry_after_at IS NULL "
            "AND reconciliation_status IS NULL AND reconciliation_deadline IS NULL "
            "AND sanitized_evidence_reference IS NULL AND sanitized_evidence_hash IS NULL "
            "AND terminal_reason IS NULL AND assessed_at IS NULL) OR "
            "(failure_policy_id IS NOT NULL "
            "AND failure_policy_semantic_version IS NOT NULL "
            "AND char_length(trim(failure_policy_semantic_version)) > 0 "
            "AND failure_policy_revision IS NOT NULL AND failure_policy_revision > 0 "
            "AND failure_policy_digest IS NOT NULL "
            "AND failure_stage IS NOT NULL AND provider_invocation IS NOT NULL "
            "AND customer_side_effect IS NOT NULL AND recovery_disposition IS NOT NULL "
            "AND maximum_attempts IS NOT NULL "
            "AND maximum_attempts BETWEEN attempt_number AND 3 "
            "AND remaining_attempts IS NOT NULL "
            "AND remaining_attempts = maximum_attempts - attempt_number "
            "AND reconciliation_status IS NOT NULL "
            "AND sanitized_evidence_hash IS NOT NULL AND assessed_at IS NOT NULL "
            "AND assessed_at >= created_at)",
            name="recovery_assessment_complete",
        ),
        CheckConstraint(
            "failure_policy_id IS NULL OR (operation_kind = 'AIInterpretation' "
            "AND customer_side_effect = 'NotApplicable' "
            "AND reconciliation_status = 'NotRequired' AND reconciliation_deadline IS NULL "
            "AND ((state = 'RetryableFailure' "
            "AND recovery_disposition = 'RetrySameOperation' "
            "AND remaining_attempts > 0 AND next_eligible_at IS NOT NULL "
            "AND next_eligible_at >= assessed_at "
            "AND (provider_retry_after_at IS NULL "
            "OR (provider_retry_after_at >= assessed_at "
            "AND next_eligible_at >= provider_retry_after_at)) "
            "AND terminal_reason IS NULL) OR "
            "(state = 'TerminalFailure' AND recovery_disposition = 'Terminal' "
            "AND next_eligible_at IS NULL AND provider_retry_after_at IS NULL "
            "AND terminal_reason IS NOT NULL)))",
            name="ai_recovery_assessment_valid",
        ),
        CheckConstraint(
            "failure_policy_id IS NULL OR operation_kind = 'AIInterpretation' OR "
            "(operation_kind = 'OutboundAction' AND "
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
            "AND next_eligible_at IS NULL AND terminal_reason IS NOT NULL)))",
            name="outbound_recovery_assessment_valid",
        ),
        CheckConstraint(
            "(operation_kind = 'AIInterpretation' AND proposal_series_id IS NULL "
            "AND proposed_action_id IS NULL AND proposal_number IS NULL "
            "AND proposal_payload_digest IS NULL "
            "AND approval_decision_id IS NULL AND stable_outbound_key_scope IS NULL "
            "AND stable_outbound_key_digest IS NULL) OR "
            "(operation_kind = 'OutboundAction' AND proposal_series_id IS NOT NULL "
            "AND proposed_action_id IS NOT NULL AND proposal_number IS NOT NULL "
            "AND proposal_number > 0 "
            "AND proposal_payload_digest ~ '^[0-9a-f]{64}$' "
            "AND approval_decision_id IS NOT NULL "
            "AND char_length(trim(stable_outbound_key_scope)) > 0 "
            "AND stable_outbound_key_digest ~ '^[0-9a-f]{64}$')",
            name="kind_binding_consistent",
        ),
        CheckConstraint(
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
            name="state_fields_consistent",
        ),
        *(
            CheckConstraint(f"char_length(trim({field})) > 0", name=f"{field}_not_blank")
            for field in (
                "adapter_name",
                "adapter_version",
                "assigned_workflow_service",
                "workflow_environment",
            )
        ),
        Index(
            "uq_integration_attempts_one_active",
            "logical_operation_id",
            unique=True,
            postgresql_where=text("state IN ('Pending', 'Running')"),
        ),
        Index(
            "uq_integration_attempts_one_succeeded",
            "logical_operation_id",
            unique=True,
            postgresql_where=text("state = 'Succeeded'"),
        ),
        Index("ix_integration_attempts_request_created", "service_request_id", "created_at"),
        Index(
            "ix_integration_attempts_policy_assessed",
            "failure_policy_id",
            "assessed_at",
        ),
        Index(
            "ix_integration_attempts_recovery_eligibility",
            "state",
            "next_eligible_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    logical_operation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("logical_operations.id", name="fk_attempt_operation", ondelete="RESTRICT"),
        nullable=False,
    )
    service_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("service_requests.id", name="fk_attempt_request", ondelete="RESTRICT"),
        nullable=False,
    )
    operation_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    proposal_series_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    proposed_action_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    proposal_number: Mapped[int | None] = mapped_column(Integer)
    proposal_payload_digest: Mapped[str | None] = mapped_column(String(64))
    approval_decision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    stable_outbound_key_scope: Mapped[str | None] = mapped_column(String(100))
    stable_outbound_key_digest: Mapped[str | None] = mapped_column(String(64))
    attempt_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    adapter_name: Mapped[str] = mapped_column(String(100), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(100), nullable=False)
    assigned_workflow_service: Mapped[str] = mapped_column(String(100), nullable=False)
    workflow_environment: Mapped[str] = mapped_column(String(100), nullable=False)
    callback_authorization_deadline: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    safe_provider_correlation: Mapped[str | None] = mapped_column(String(200))
    result_hash: Mapped[str | None] = mapped_column(String(64))
    sanitized_error_code: Mapped[str | None] = mapped_column(String(100))
    failure_policy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    failure_policy_semantic_version: Mapped[str | None] = mapped_column(String(32))
    failure_policy_revision: Mapped[int | None] = mapped_column(Integer)
    failure_policy_digest: Mapped[str | None] = mapped_column(String(64))
    failure_stage: Mapped[str | None] = mapped_column(String(32))
    provider_invocation: Mapped[str | None] = mapped_column(String(32))
    customer_side_effect: Mapped[str | None] = mapped_column(String(32))
    recovery_disposition: Mapped[str | None] = mapped_column(String(32))
    maximum_attempts: Mapped[int | None] = mapped_column(SmallInteger)
    remaining_attempts: Mapped[int | None] = mapped_column(SmallInteger)
    next_eligible_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_retry_after_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconciliation_status: Mapped[str | None] = mapped_column(String(32))
    reconciliation_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sanitized_evidence_reference: Mapped[str | None] = mapped_column(String(200))
    sanitized_evidence_hash: Mapped[str | None] = mapped_column(String(64))
    terminal_reason: Mapped[str | None] = mapped_column(String(100))
    assessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AttemptCallbackCredential(Base):
    __tablename__ = "attempt_callback_credentials"
    __table_args__ = (
        UniqueConstraint(
            "integration_attempt_id",
            "credential_version",
            name="uq_attempt_callback_credentials_attempt_credential_version",
        ),
        UniqueConstraint("credential_hash", name="uq_attempt_callback_credentials_credential_hash"),
        ForeignKeyConstraint(
            ("integration_attempt_id", "operation_kind"),
            ("integration_attempts.id", "integration_attempts.operation_kind"),
            name="fk_callback_credential_attempt_kind",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "operation_kind IN ('AIInterpretation', 'OutboundAction')",
            name="operation_kind_valid",
        ),
        CheckConstraint("credential_version > 0", name="credential_version_positive"),
        CheckConstraint(HASH_CHECK.format(column="credential_hash"), name="credential_hash_valid"),
        CheckConstraint("expires_at > issued_at", name="expiry_valid"),
        CheckConstraint(
            "char_length(trim(workflow_service_identity)) > 0",
            name="workflow_service_identity_not_blank",
        ),
        CheckConstraint(
            "char_length(trim(workflow_environment)) > 0",
            name="workflow_environment_not_blank",
        ),
        CheckConstraint(
            "replacement_credential_id IS NULL OR replacement_credential_id <> id",
            name="replacement_not_self",
        ),
        CheckConstraint(
            "state IN ('Active', 'Consumed', 'Replaced', 'Revoked')", name="state_valid"
        ),
        CheckConstraint(
            "(state = 'Active' AND consumed_at IS NULL AND replaced_at IS NULL "
            "AND revoked_at IS NULL AND replacement_credential_id IS NULL) OR "
            "(state = 'Consumed' AND consumed_at IS NOT NULL AND replaced_at IS NULL "
            "AND revoked_at IS NULL AND replacement_credential_id IS NULL) OR "
            "(state = 'Replaced' AND consumed_at IS NULL AND replaced_at IS NOT NULL "
            "AND revoked_at IS NULL AND replacement_credential_id IS NOT NULL) OR "
            "(state = 'Revoked' AND consumed_at IS NULL AND replaced_at IS NULL "
            "AND revoked_at IS NOT NULL AND replacement_credential_id IS NULL)",
            name="state_fields_consistent",
        ),
        Index(
            "uq_attempt_callback_credentials_one_active",
            "integration_attempt_id",
            unique=True,
            postgresql_where=text("state = 'Active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    integration_attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "integration_attempts.id", name="fk_callback_credential_attempt", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    operation_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    workflow_service_identity: Mapped[str] = mapped_column(String(100), nullable=False)
    workflow_environment: Mapped[str] = mapped_column(String(100), nullable=False)
    credential_version: Mapped[int] = mapped_column(Integer, nullable=False)
    credential_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replacement_credential_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "attempt_callback_credentials.id",
            name="fk_callback_credential_replacement",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
    )


class AiInterpretation(Base):
    __tablename__ = "ai_interpretations"
    __table_args__ = (
        UniqueConstraint(
            "service_request_id",
            "interpretation_number",
            name="uq_ai_interpretations_request_number",
        ),
        UniqueConstraint(
            "id",
            "service_request_id",
            "interpretation_number",
            name="uq_ai_interpretations_routing_identity",
        ),
        UniqueConstraint("logical_operation_id", name="uq_ai_interpretations_logical_operation"),
        UniqueConstraint("producing_attempt_id", name="uq_ai_interpretations_producing_attempt"),
        CheckConstraint("interpretation_number > 0", name="interpretation_number_positive"),
        CheckConstraint("char_length(trim(summary)) > 0", name="summary_not_blank"),
        CheckConstraint(
            f"suggested_category IN ({_sql_values(SERVICE_CATEGORY_VALUES)})",
            name="suggested_category_valid",
        ),
        CheckConstraint(
            "jsonb_typeof(missing_information) = 'array'", name="missing_information_array"
        ),
        CheckConstraint(
            "warnings IS NULL OR jsonb_typeof(warnings) = 'array'", name="warnings_array"
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_valid"),
        CheckConstraint(HASH_CHECK.format(column="input_hash"), name="input_hash_valid"),
        CheckConstraint(
            HASH_CHECK.format(column="configuration_hash"), name="configuration_hash_valid"
        ),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="latency_nonnegative"),
        *(
            CheckConstraint(f"char_length(trim({field})) > 0", name=f"{field}_not_blank")
            for field in NONBLANK_FIELDS
        ),
        Index("ix_ai_interpretations_request_created", "service_request_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("service_requests.id", name="fk_interpretation_request", ondelete="RESTRICT"),
        nullable=False,
    )
    logical_operation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "logical_operations.id", name="fk_interpretation_operation", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    producing_attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "integration_attempts.id", name="fk_interpretation_attempt", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    interpretation_number: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(String(2000), nullable=False)
    suggested_category: Mapped[str] = mapped_column(String(32), nullable=False)
    missing_information: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(100), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    adapter_name: Mapped[str] = mapped_column(String(100), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(100), nullable=False)
    safe_provider_correlation: Mapped[str | None] = mapped_column(String(200))
    warnings: Mapped[list[Any] | None] = mapped_column(JSONB)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    usage_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
