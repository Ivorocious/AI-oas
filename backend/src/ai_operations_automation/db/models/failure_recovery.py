"""Immutable, deployment-controlled failure-recovery policy versions."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_operations_automation.db.base import Base


class FailureRecoveryPolicyVersion(Base):
    """One immutable policy snapshot used to assess integration-attempt failures."""

    __tablename__ = "failure_recovery_policy_versions"
    __table_args__ = (
        UniqueConstraint(
            "policy_key",
            "semantic_version",
            "revision",
            name="uq_failure_recovery_policy_versions_identity",
        ),
        UniqueConstraint(
            "content_digest",
            name="uq_failure_recovery_policy_versions_content_digest",
        ),
        UniqueConstraint(
            "id",
            "semantic_version",
            "revision",
            "content_digest",
            name="uq_failure_recovery_policy_versions_assessment_identity",
        ),
        CheckConstraint(
            "policy_key ~ '^[a-z][a-z0-9._-]{0,99}$'",
            name="policy_key_valid",
        ),
        CheckConstraint(
            "semantic_version ~ '^[0-9]+[.][0-9]+[.][0-9]+(-[0-9A-Za-z.-]+)?$'",
            name="semantic_version_valid",
        ),
        CheckConstraint("revision > 0", name="revision_positive"),
        CheckConstraint(
            "content_digest ~ '^[0-9a-f]{64}$'",
            name="content_digest_valid",
        ),
        CheckConstraint(
            "status IN ('Draft', 'Active', 'Retired')",
            name="status_valid",
        ),
        *(
            CheckConstraint(
                f"jsonb_typeof({field}) = 'array'",
                name=f"{field}_array",
            )
            for field in (
                "operation_kind_rules",
                "failure_code_catalog",
                "attempt_budgets",
                "retry_delay_schedule",
                "recovery_disposition_rules",
            )
        ),
        *(
            CheckConstraint(
                f"jsonb_typeof({field}) = 'object'",
                name=f"{field}_object",
            )
            for field in (
                "policy_snapshot",
                "stale_attempt_thresholds",
                "reconciliation_rules",
                "terminalization_rules",
            )
        ),
        CheckConstraint(
            "(status IN ('Draft', 'Active') AND retired_at IS NULL "
            "AND retirement_reason IS NULL AND retirement_reference IS NULL) OR "
            "(status = 'Retired' AND retired_at IS NOT NULL "
            "AND retired_at >= effective_at AND retirement_reason IS NOT NULL "
            "AND retirement_reason ~ '^[A-Z][A-Z0-9_]{0,99}$')",
            name="retirement_fields_consistent",
        ),
        Index(
            "uq_failure_recovery_policy_versions_one_active",
            "policy_key",
            unique=True,
            postgresql_where=text("status = 'Active'"),
        ),
        Index(
            "ix_failure_recovery_policy_versions_status_effective",
            "status",
            "effective_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    policy_key: Mapped[str] = mapped_column(String(100), nullable=False)
    semantic_version: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    operation_kind_rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    failure_code_catalog: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    attempt_budgets: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    retry_delay_schedule: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    stale_attempt_thresholds: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reconciliation_rules: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    recovery_disposition_rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    terminalization_rules: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retirement_reason: Mapped[str | None] = mapped_column(String(100))
    retirement_reference: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
