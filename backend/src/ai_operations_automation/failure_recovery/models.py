"""Closed immutable contracts for deterministic failure assessment."""

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt


class ClosedImmutableModel(BaseModel):
    """Trusted policy values cannot gain fields or be changed after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class OperationKind(StrEnum):
    AI_INTERPRETATION = "AIInterpretation"
    OUTBOUND_ACTION = "OutboundAction"


class FailureStage(StrEnum):
    BEFORE_DISPATCH = "BeforeDispatch"
    DISPATCH = "Dispatch"
    PROVIDER_PROCESSING = "ProviderProcessing"
    RESPONSE_VALIDATION = "ResponseValidation"
    CALLBACK_DELIVERY = "CallbackDelivery"
    RECONCILIATION = "Reconciliation"
    INTERNAL_COMMIT = "InternalCommit"


class ProviderInvocation(StrEnum):
    NOT_INVOKED = "NotInvoked"
    INVOKED = "Invoked"
    INVOCATION_UNKNOWN = "InvocationUnknown"
    NOT_APPLICABLE = "NotApplicable"


class CustomerSideEffect(StrEnum):
    NOT_APPLICABLE = "NotApplicable"
    KNOWN_NOT_APPLIED = "KnownNotApplied"
    APPLIED = "Applied"
    UNKNOWN = "Unknown"


class RecoveryDisposition(StrEnum):
    RETRY_SAME_OPERATION = "RetrySameOperation"
    REVISE_PROPOSAL = "ReviseProposal"
    RECONCILE_BEFORE_RETRY = "ReconcileBeforeRetry"
    REPLAY_SAME_COMMAND = "ReplaySameCommand"
    TERMINAL = "Terminal"
    NO_DOMAIN_CHANGE = "NoDomainChange"


class FailureCode(StrEnum):
    WORKFLOW_FAILED_BEFORE_PROVIDER_INVOCATION = "WORKFLOW_FAILED_BEFORE_PROVIDER_INVOCATION"
    PROVIDER_CONNECTION_FAILED = "PROVIDER_CONNECTION_FAILED"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    PROVIDER_TEMPORARILY_UNAVAILABLE = "PROVIDER_TEMPORARILY_UNAVAILABLE"
    PROVIDER_AUTHENTICATION_FAILED = "PROVIDER_AUTHENTICATION_FAILED"
    PROVIDER_AUTHORIZATION_FAILED = "PROVIDER_AUTHORIZATION_FAILED"
    PROVIDER_CONFIGURATION_INVALID = "PROVIDER_CONFIGURATION_INVALID"
    PROVIDER_REQUEST_REJECTED = "PROVIDER_REQUEST_REJECTED"
    PROVIDER_RESPONSE_SCHEMA_INVALID = "PROVIDER_RESPONSE_SCHEMA_INVALID"
    OUTBOUND_DESTINATION_REJECTED = "OUTBOUND_DESTINATION_REJECTED"
    OUTBOUND_PAYLOAD_REJECTED = "OUTBOUND_PAYLOAD_REJECTED"
    CALLBACK_RESPONSE_LOST_AFTER_COMMIT = "CALLBACK_RESPONSE_LOST_AFTER_COMMIT"
    CALLBACK_AUTHENTICATION_FAILED = "CALLBACK_AUTHENTICATION_FAILED"
    CALLBACK_CREDENTIAL_INVALID = "CALLBACK_CREDENTIAL_INVALID"
    ATTEMPT_PENDING_STALE = "ATTEMPT_PENDING_STALE"
    AI_ATTEMPT_RUNNING_STALE = "AI_ATTEMPT_RUNNING_STALE"
    OUTBOUND_OUTCOME_UNCERTAIN = "OUTBOUND_OUTCOME_UNCERTAIN"
    RECONCILIATION_CONFIRMED_SUCCESS = "RECONCILIATION_CONFIRMED_SUCCESS"
    RECONCILIATION_CONFIRMED_NOT_APPLIED = "RECONCILIATION_CONFIRMED_NOT_APPLIED"
    RECONCILIATION_PERMANENT_REJECTION = "RECONCILIATION_PERMANENT_REJECTION"
    OUTBOUND_OUTCOME_UNRESOLVED = "OUTBOUND_OUTCOME_UNRESOLVED"
    RETRY_BUDGET_EXHAUSTED = "RETRY_BUDGET_EXHAUSTED"
    MANAGER_TERMINAL_DISPOSITION = "MANAGER_TERMINAL_DISPOSITION"
    ADMINISTRATOR_TERMINAL_DISPOSITION = "ADMINISTRATOR_TERMINAL_DISPOSITION"
    INTERNAL_TRANSACTION_FAILED_BEFORE_COMMIT = "INTERNAL_TRANSACTION_FAILED_BEFORE_COMMIT"


class PolicyStatus(StrEnum):
    DRAFT = "Draft"
    ACTIVE = "Active"
    RETIRED = "Retired"


class ReconciliationStatus(StrEnum):
    NOT_REQUIRED = "NotRequired"
    REQUIRED = "Required"


class FailurePolicyIdentity(ClosedImmutableModel):
    policy_id: uuid.UUID
    policy_key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9-]*$")
    semantic_version: str = Field(min_length=5, max_length=32, pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    revision: StrictInt = Field(gt=0)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class OperationKindRule(ClosedImmutableModel):
    operation_kind: OperationKind
    allowed_customer_side_effects: tuple[CustomerSideEffect, ...]
    success_closes_operation: bool
    material_revision_preserves_budget: bool


class EvidenceOutcomeRule(ClosedImmutableModel):
    operation_kind: OperationKind
    stages: tuple[FailureStage, ...]
    provider_invocations: tuple[ProviderInvocation, ...]
    customer_side_effects: tuple[CustomerSideEffect, ...]
    disposition: RecoveryDisposition


class FailureCodeRule(ClosedImmutableModel):
    code: FailureCode
    outcomes: tuple[EvidenceOutcomeRule, ...]
    consumes_attempt: bool
    consumes_budget: bool
    required_sanitized_evidence: tuple[str, ...]


class AttemptBudgetRule(ClosedImmutableModel):
    operation_kind: OperationKind
    maximum_attempts: StrictInt = Field(gt=0)


class RetryDelayRule(ClosedImmutableModel):
    operation_kind: OperationKind
    failed_attempt_number: StrictInt = Field(gt=0)
    delay_seconds: StrictInt = Field(gt=0)


class StaleAttemptThresholds(ClosedImmutableModel):
    pending_seconds: StrictInt = Field(gt=0)
    ai_running_seconds: StrictInt = Field(gt=0)


class ReconciliationRules(ClosedImmutableModel):
    operation_kind: OperationKind
    unknown_side_effect_disposition: RecoveryDisposition
    deadline_seconds_after_started: StrictInt = Field(gt=0)
    uncertainty_code: FailureCode
    confirmed_success_code: FailureCode
    confirmed_not_applied_code: FailureCode
    permanent_rejection_code: FailureCode
    unresolved_terminal_code: FailureCode


class RecoveryDispositionRule(ClosedImmutableModel):
    operation_kind: OperationKind
    customer_side_effect: CustomerSideEffect
    allowed_dispositions: tuple[RecoveryDisposition, ...]


class TerminalizationRules(ClosedImmutableModel):
    exhaustion_code: FailureCode
    final_attempt_is_terminal: bool
    success_permanently_closes_operation: bool
    terminal_work_can_reopen: bool
    unresolved_outbound_code: FailureCode


class FailureRecoveryPolicyContent(ClosedImmutableModel):
    operation_kind_rules: tuple[OperationKindRule, ...]
    failure_code_catalog: tuple[FailureCodeRule, ...]
    attempt_budgets: tuple[AttemptBudgetRule, ...]
    retry_delay_schedule: tuple[RetryDelayRule, ...]
    stale_attempt_thresholds: StaleAttemptThresholds
    reconciliation_rules: ReconciliationRules
    recovery_disposition_rules: tuple[RecoveryDispositionRule, ...]
    terminalization_rules: TerminalizationRules
    globally_forbidden_evidence: tuple[str, ...]


class FailureRecoveryPolicyVersion(ClosedImmutableModel):
    id: uuid.UUID
    policy_key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9-]*$")
    semantic_version: str = Field(min_length=5, max_length=32, pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    revision: StrictInt = Field(gt=0)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_at: AwareDatetime
    status: PolicyStatus
    content: FailureRecoveryPolicyContent

    @property
    def identity(self) -> FailurePolicyIdentity:
        return FailurePolicyIdentity(
            policy_id=self.id,
            policy_key=self.policy_key,
            semantic_version=self.semantic_version,
            revision=self.revision,
            content_digest=self.content_digest,
        )


class FailureAssessmentInput(ClosedImmutableModel):
    operation_kind: OperationKind
    failure_code: FailureCode
    failure_stage: FailureStage
    provider_invocation: ProviderInvocation
    customer_side_effect: CustomerSideEffect
    attempt_number: StrictInt = Field(gt=0)
    assessed_at: AwareDatetime
    attempt_started_at: AwareDatetime | None = None
    provider_retry_after_at: AwareDatetime | None = None


class FailureAssessment(ClosedImmutableModel):
    policy: FailurePolicyIdentity
    operation_kind: OperationKind
    failure_code: FailureCode
    failure_stage: FailureStage
    provider_invocation: ProviderInvocation
    customer_side_effect: CustomerSideEffect
    recovery_disposition: RecoveryDisposition
    attempt_number: StrictInt = Field(gt=0)
    maximum_attempts: StrictInt = Field(gt=0)
    remaining_attempts: StrictInt = Field(ge=0)
    next_eligible_at: AwareDatetime | None
    provider_retry_after_at: AwareDatetime | None
    reconciliation_status: ReconciliationStatus
    reconciliation_deadline: AwareDatetime | None
    terminal_reason: FailureCode | None
    assessed_at: AwareDatetime


def require_aware(value: datetime, *, field_name: str) -> datetime:
    """Reject implicit local/naive clocks at the deterministic policy boundary."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value
