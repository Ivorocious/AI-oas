"""Small explicit writer for atomic audit and outbox evidence."""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ai_operations_automation.db.models.evidence import AuditEvent, OutboxMessage


@dataclass(frozen=True, slots=True)
class AuditSpec:
    event_name: str
    aggregate_type: str
    aggregate_id: uuid.UUID
    aggregate_version: int
    actor_type: str
    actor_reference_id: uuid.UUID
    outcome: str
    correlation_id: uuid.UUID
    causation_id: uuid.UUID | None
    command_id: uuid.UUID | None
    reason_codes: tuple[str, ...] = ()
    safe_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OutboxSpec:
    event_type: str
    payload: dict[str, Any]


def write_outbox_for_audit(
    session: Session,
    audit: AuditEvent,
    outbox_spec: OutboxSpec,
) -> OutboxMessage:
    """Append another PII-minimized integration event for an existing audit fact."""
    outbox = OutboxMessage(
        id=uuid.uuid4(),
        event_type=outbox_spec.event_type,
        schema_version="1.0",
        aggregate_type=audit.aggregate_type,
        aggregate_id=audit.aggregate_id,
        aggregate_version=audit.aggregate_version,
        audit_event_id=audit.id,
        correlation_id=audit.correlation_id,
        causation_id=audit.causation_id,
        payload=dict(outbox_spec.payload),
        publication_state="Pending",
    )
    session.add(outbox)
    session.flush()
    return outbox


def write_audit_and_optional_outbox(
    session: Session,
    audit_spec: AuditSpec,
    outbox_spec: OutboxSpec | None = None,
) -> tuple[AuditEvent, OutboxMessage | None]:
    """Append one audit record and its optional PII-minimized outbox message."""
    audit = AuditEvent(
        id=uuid.uuid4(),
        schema_version="1.0",
        event_name=audit_spec.event_name,
        aggregate_type=audit_spec.aggregate_type,
        aggregate_id=audit_spec.aggregate_id,
        aggregate_version=audit_spec.aggregate_version,
        actor_type=audit_spec.actor_type,
        actor_reference_id=audit_spec.actor_reference_id,
        outcome=audit_spec.outcome,
        correlation_id=audit_spec.correlation_id,
        causation_id=audit_spec.causation_id,
        command_id=audit_spec.command_id,
        reason_codes=list(audit_spec.reason_codes),
        safe_metadata=dict(audit_spec.safe_metadata),
    )
    session.add(audit)
    session.flush()
    if outbox_spec is None:
        return audit, None
    outbox = write_outbox_for_audit(session, audit, outbox_spec)
    return audit, outbox
