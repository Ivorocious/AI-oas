"""Deliberately bounded application persistence models."""

from ai_operations_automation.db.models.ai_execution import (
    AiInterpretation,
    AttemptCallbackCredential,
    IntegrationAttempt,
    LogicalOperation,
)
from ai_operations_automation.db.models.command_idempotency import CommandIdempotencyRecord
from ai_operations_automation.db.models.evidence import AuditEvent, OutboxMessage
from ai_operations_automation.db.models.failure_recovery import FailureRecoveryPolicyVersion
from ai_operations_automation.db.models.identity import (
    ApplicationActor,
    ApplicationActorRoleAssignment,
)
from ai_operations_automation.db.models.intake import (
    AcceptedIntakeKey,
    Contact,
    InboundDelivery,
    ServiceRequest,
)
from ai_operations_automation.db.models.machine import (
    MachineCredentialVersion,
    MachineIdentity,
    MachineRequestNonce,
)

__all__ = [
    "AcceptedIntakeKey",
    "AiInterpretation",
    "ApplicationActor",
    "ApplicationActorRoleAssignment",
    "AuditEvent",
    "AttemptCallbackCredential",
    "Contact",
    "CommandIdempotencyRecord",
    "FailureRecoveryPolicyVersion",
    "InboundDelivery",
    "IntegrationAttempt",
    "LogicalOperation",
    "MachineCredentialVersion",
    "MachineIdentity",
    "MachineRequestNonce",
    "OutboxMessage",
    "ServiceRequest",
]
