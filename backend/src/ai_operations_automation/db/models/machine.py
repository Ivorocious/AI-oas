"""Machine identity, external credential metadata, and nonce evidence."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_operations_automation.db.base import Base


class MachineIdentity(Base):
    __tablename__ = "machine_identities"
    __table_args__ = (
        UniqueConstraint(
            "environment", "stable_service_id", name="uq_machine_identities_environment_service"
        ),
        CheckConstraint(
            "service_type IN ('BackendService', 'WorkflowService', 'EventPublisher')",
            name="service_type_valid",
        ),
        CheckConstraint("char_length(trim(environment)) > 0", name="environment_not_blank"),
        CheckConstraint(
            "char_length(trim(stable_service_id)) > 0 AND stable_service_id ~ '^[A-Za-z0-9._:-]+$'",
            name="stable_service_id_valid",
        ),
        CheckConstraint("char_length(trim(display_label)) > 0", name="display_label_not_blank"),
        CheckConstraint("status IN ('Active', 'Disabled')", name="status_valid"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            "(status = 'Active' AND disabled_at IS NULL AND disable_reason IS NULL) OR "
            "(status = 'Disabled' AND disabled_at IS NOT NULL AND "
            "disable_reason IS NOT NULL AND char_length(trim(disable_reason)) > 0)",
            name="disabled_fields_consistent",
        ),
        Index("ix_machine_identities_type_status", "service_type", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_type: Mapped[str] = mapped_column(String(32), nullable=False)
    environment: Mapped[str] = mapped_column(String(64), nullable=False)
    stable_service_id: Mapped[str] = mapped_column(String(128), nullable=False)
    display_label: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disable_reason: Mapped[str | None] = mapped_column(String(200))


class MachineCredentialVersion(Base):
    __tablename__ = "machine_credential_versions"
    __table_args__ = (
        UniqueConstraint(
            "machine_identity_id",
            "credential_version",
            name="uq_machine_credential_versions_identity_version",
        ),
        UniqueConstraint(
            "external_secret_reference",
            name="uq_machine_credential_versions_external_reference",
        ),
        CheckConstraint("credential_version > 0", name="credential_version_positive"),
        CheckConstraint(
            "char_length(trim(external_secret_reference)) > 0",
            name="external_reference_not_blank",
        ),
        CheckConstraint(
            "status IN ('Current', 'Previous', 'Retired', 'Revoked')", name="status_valid"
        ),
        CheckConstraint(
            "(status = 'Current' AND previous_verification_until IS NULL AND "
            "retired_at IS NULL AND revoked_at IS NULL) OR "
            "(status = 'Previous' AND previous_verification_until IS NOT NULL AND "
            "previous_verification_until > activated_at AND retired_at IS NULL AND "
            "revoked_at IS NULL) OR "
            "(status = 'Retired' AND previous_verification_until IS NULL AND "
            "retired_at IS NOT NULL AND revoked_at IS NULL) OR "
            "(status = 'Revoked' AND previous_verification_until IS NULL AND "
            "retired_at IS NULL AND revoked_at IS NOT NULL)",
            name="state_fields_consistent",
        ),
        Index(
            "uq_machine_credential_versions_one_current",
            "machine_identity_id",
            unique=True,
            postgresql_where=text("status = 'Current'"),
        ),
        Index(
            "uq_machine_credential_versions_one_previous",
            "machine_identity_id",
            unique=True,
            postgresql_where=text("status = 'Previous'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    machine_identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "machine_identities.id", name="fk_machine_credential_identity", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    credential_version: Mapped[int] = mapped_column(Integer, nullable=False)
    external_secret_reference: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    previous_verification_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    safe_rotation_reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MachineRequestNonce(Base):
    __tablename__ = "machine_request_nonces"
    __table_args__ = (
        UniqueConstraint(
            "machine_identity_id",
            "environment",
            "nonce_digest",
            name="uq_machine_request_nonces_identity_environment_digest",
        ),
        CheckConstraint("char_length(trim(environment)) > 0", name="environment_not_blank"),
        CheckConstraint("verified_credential_version > 0", name="credential_version_positive"),
        CheckConstraint("nonce_digest ~ '^[0-9a-f]{64}$'", name="nonce_digest_valid"),
        CheckConstraint("expires_at > received_at", name="expiry_valid"),
        Index("ix_machine_request_nonces_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    machine_identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("machine_identities.id", name="fk_machine_nonce_identity", ondelete="RESTRICT"),
        nullable=False,
    )
    machine_credential_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "machine_credential_versions.id",
            name="fk_machine_nonce_credential",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    environment: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_credential_version: Mapped[int] = mapped_column(Integer, nullable=False)
    nonce_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
