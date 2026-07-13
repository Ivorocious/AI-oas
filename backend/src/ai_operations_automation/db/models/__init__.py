"""Deliberately bounded application persistence models."""

from ai_operations_automation.db.models.ai_execution import (
    AiInterpretation,
    AttemptCallbackCredential,
    IntegrationAttempt,
    LogicalOperation,
)
from ai_operations_automation.db.models.command_idempotency import CommandIdempotencyRecord
from ai_operations_automation.db.models.decision import (
    DecisionPolicyVersion,
    DuplicateCandidate,
    ReviewedFactSet,
    RoutingDecision,
    RoutingDecisionDuplicateCandidate,
)
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
from ai_operations_automation.db.models.proposal import (
    ApprovalDecision,
    ProposalApprovalExclusion,
    ProposedAction,
    ProposedActionContributor,
)

__all__ = [
    "AcceptedIntakeKey",
    "AiInterpretation",
    "ApplicationActor",
    "ApplicationActorRoleAssignment",
    "ApprovalDecision",
    "AuditEvent",
    "AttemptCallbackCredential",
    "Contact",
    "CommandIdempotencyRecord",
    "DecisionPolicyVersion",
    "DuplicateCandidate",
    "FailureRecoveryPolicyVersion",
    "InboundDelivery",
    "IntegrationAttempt",
    "LogicalOperation",
    "MachineCredentialVersion",
    "MachineIdentity",
    "MachineRequestNonce",
    "OutboxMessage",
    "ProposalApprovalExclusion",
    "ProposedAction",
    "ProposedActionContributor",
    "ReviewedFactSet",
    "RoutingDecision",
    "RoutingDecisionDuplicateCandidate",
    "ServiceRequest",
]
