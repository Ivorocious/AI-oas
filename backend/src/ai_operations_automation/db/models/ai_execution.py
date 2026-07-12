"""AI-only logical operation, attempt, credential, and interpretation evidence."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
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


class LogicalOperation(Base):
    __tablename__ = "logical_operations"
    __table_args__ = (
        UniqueConstraint(
            "service_request_id",
            "input_hash",
            "configuration_hash",
            name="uq_logical_operations_ai_identity",
        ),
        CheckConstraint("operation_kind = 'AIInterpretation'", name="operation_kind_valid"),
        CheckConstraint(HASH_CHECK.format(column="input_hash"), name="input_hash_valid"),
        CheckConstraint(
            HASH_CHECK.format(column="configuration_hash"), name="configuration_hash_valid"
        ),
        CheckConstraint("version > 0", name="version_positive"),
        *(
            CheckConstraint(f"char_length(trim({field})) > 0", name=f"{field}_not_blank")
            for field in NONBLANK_FIELDS
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
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    result_schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(100), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    adapter_name: Mapped[str] = mapped_column(String(100), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(100), nullable=False)
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
        CheckConstraint("operation_kind = 'AIInterpretation'", name="operation_kind_valid"),
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
            "(state = 'Pending' AND started_at IS NULL AND completed_at IS NULL "
            "AND result_hash IS NULL AND sanitized_error_code IS NULL) OR "
            "(state = 'Running' AND started_at IS NOT NULL AND completed_at IS NULL "
            "AND result_hash IS NULL AND sanitized_error_code IS NULL) OR "
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


class AttemptCallbackCredential(Base):
    __tablename__ = "attempt_callback_credentials"
    __table_args__ = (
        UniqueConstraint(
            "integration_attempt_id",
            "credential_version",
            name="uq_attempt_callback_credentials_attempt_credential_version",
        ),
        UniqueConstraint("credential_hash", name="uq_attempt_callback_credentials_credential_hash"),
        CheckConstraint("operation_kind = 'AIInterpretation'", name="operation_kind_valid"),
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
