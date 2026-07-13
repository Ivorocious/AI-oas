"""Immutable demonstration failure policy and its deterministic evaluator."""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

from ai_operations_automation.failure_recovery.models import (
    AttemptBudgetRule,
    CustomerSideEffect,
    EvidenceOutcomeRule,
    FailureAssessment,
    FailureAssessmentInput,
    FailureCode,
    FailureCodeRule,
    FailurePolicyIdentity,
    FailureRecoveryPolicyContent,
    FailureRecoveryPolicyVersion,
    FailureStage,
    OperationKind,
    OperationKindRule,
    PolicyStatus,
    ProviderInvocation,
    ReconciliationRules,
    ReconciliationStatus,
    RecoveryDisposition,
    RecoveryDispositionRule,
    RetryDelayRule,
    StaleAttemptThresholds,
    TerminalizationRules,
    require_aware,
)

DEMO_POLICY_ID = uuid.UUID("6e862dfd-25c4-5f9b-873d-011f86c4bab5")
DEMO_POLICY_KEY = "phase2-demonstration-failure-recovery"
DEMO_POLICY_SEMANTIC_VERSION = "1.0.0"
DEMO_POLICY_REVISION = 1
DEMO_POLICY_EFFECTIVE_AT = datetime(2026, 7, 13, tzinfo=UTC)

AI = OperationKind.AI_INTERPRETATION
OUTBOUND = OperationKind.OUTBOUND_ACTION
NOT_APPLICABLE = CustomerSideEffect.NOT_APPLICABLE
KNOWN_NOT_APPLIED = CustomerSideEffect.KNOWN_NOT_APPLIED
APPLIED = CustomerSideEffect.APPLIED
UNKNOWN = CustomerSideEffect.UNKNOWN
RETRY = RecoveryDisposition.RETRY_SAME_OPERATION
REVISE = RecoveryDisposition.REVISE_PROPOSAL
RECONCILE = RecoveryDisposition.RECONCILE_BEFORE_RETRY
REPLAY = RecoveryDisposition.REPLAY_SAME_COMMAND
TERMINAL = RecoveryDisposition.TERMINAL
NO_CHANGE = RecoveryDisposition.NO_DOMAIN_CHANGE


class FailurePolicyError(ValueError):
    """A stable deterministic policy rejection without unsafe evidence."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _outcome(
    operation_kind: OperationKind,
    stage: FailureStage,
    invocation: ProviderInvocation | tuple[ProviderInvocation, ...],
    side_effect: CustomerSideEffect | tuple[CustomerSideEffect, ...],
    disposition: RecoveryDisposition,
) -> EvidenceOutcomeRule:
    return EvidenceOutcomeRule(
        operation_kind=operation_kind,
        stages=(stage,),
        provider_invocations=(invocation,)
        if isinstance(invocation, ProviderInvocation)
        else invocation,
        customer_side_effects=(side_effect,)
        if isinstance(side_effect, CustomerSideEffect)
        else side_effect,
        disposition=disposition,
    )


def _rule(
    code: FailureCode,
    *outcomes: EvidenceOutcomeRule,
    consumes_attempt: bool,
    consumes_budget: bool,
    evidence: tuple[str, ...],
) -> FailureCodeRule:
    return FailureCodeRule(
        code=code,
        outcomes=outcomes,
        consumes_attempt=consumes_attempt,
        consumes_budget=consumes_budget,
        required_sanitized_evidence=evidence,
    )


def _catalog() -> tuple[FailureCodeRule, ...]:
    before = FailureStage.BEFORE_DISPATCH
    dispatch = FailureStage.DISPATCH
    processing = FailureStage.PROVIDER_PROCESSING
    validation = FailureStage.RESPONSE_VALIDATION
    callback = FailureStage.CALLBACK_DELIVERY
    reconciliation = FailureStage.RECONCILIATION
    internal = FailureStage.INTERNAL_COMMIT
    not_invoked = ProviderInvocation.NOT_INVOKED
    invoked = ProviderInvocation.INVOKED
    invocation_unknown = ProviderInvocation.INVOCATION_UNKNOWN
    invocation_na = ProviderInvocation.NOT_APPLICABLE
    both = (AI, OUTBOUND)

    def same_for_both(
        code: FailureCode,
        stage: FailureStage,
        invocation: ProviderInvocation,
        side_effect: CustomerSideEffect,
        disposition: RecoveryDisposition,
        *,
        consumes_attempt: bool,
        consumes_budget: bool,
        evidence: tuple[str, ...],
    ) -> FailureCodeRule:
        return _rule(
            code,
            *(_outcome(kind, stage, invocation, side_effect, disposition) for kind in both),
            consumes_attempt=consumes_attempt,
            consumes_budget=consumes_budget,
            evidence=evidence,
        )

    return (
        _rule(
            FailureCode.WORKFLOW_FAILED_BEFORE_PROVIDER_INVOCATION,
            _outcome(AI, before, not_invoked, NOT_APPLICABLE, RETRY),
            _outcome(OUTBOUND, before, not_invoked, KNOWN_NOT_APPLIED, RETRY),
            consumes_attempt=True,
            consumes_budget=True,
            evidence=("workflow_error_class", "adapter_version"),
        ),
        _rule(
            FailureCode.PROVIDER_CONNECTION_FAILED,
            _outcome(AI, dispatch, invocation_unknown, NOT_APPLICABLE, RETRY),
            _outcome(OUTBOUND, dispatch, invocation_unknown, UNKNOWN, RECONCILE),
            consumes_attempt=True,
            consumes_budget=True,
            evidence=("correlation_hash", "timeout_class"),
        ),
        _rule(
            FailureCode.PROVIDER_TIMEOUT,
            _outcome(AI, processing, invoked, NOT_APPLICABLE, RETRY),
            _outcome(OUTBOUND, processing, invoked, UNKNOWN, RECONCILE),
            consumes_attempt=True,
            consumes_budget=True,
            evidence=("duration_ms", "correlation_hash", "safe_hint"),
        ),
        _rule(
            FailureCode.PROVIDER_RATE_LIMITED,
            _outcome(AI, processing, invoked, NOT_APPLICABLE, RETRY),
            _outcome(OUTBOUND, processing, invoked, KNOWN_NOT_APPLIED, RETRY),
            _outcome(OUTBOUND, processing, invoked, UNKNOWN, RECONCILE),
            consumes_attempt=True,
            consumes_budget=True,
            evidence=("rate_limit_class", "retry_after_at"),
        ),
        _rule(
            FailureCode.PROVIDER_TEMPORARILY_UNAVAILABLE,
            _outcome(AI, processing, invoked, NOT_APPLICABLE, RETRY),
            _outcome(OUTBOUND, processing, invoked, KNOWN_NOT_APPLIED, RETRY),
            _outcome(OUTBOUND, processing, invoked, UNKNOWN, RECONCILE),
            consumes_attempt=True,
            consumes_budget=True,
            evidence=("provider_status_class", "correlation_hash"),
        ),
        _rule(
            FailureCode.PROVIDER_AUTHENTICATION_FAILED,
            _outcome(AI, dispatch, not_invoked, NOT_APPLICABLE, TERMINAL),
            _outcome(OUTBOUND, dispatch, not_invoked, KNOWN_NOT_APPLIED, TERMINAL),
            consumes_attempt=True,
            consumes_budget=True,
            evidence=("credential_version_reference", "adapter_version"),
        ),
        _rule(
            FailureCode.PROVIDER_AUTHORIZATION_FAILED,
            _outcome(AI, processing, invoked, NOT_APPLICABLE, TERMINAL),
            _outcome(OUTBOUND, processing, invoked, KNOWN_NOT_APPLIED, TERMINAL),
            consumes_attempt=True,
            consumes_budget=True,
            evidence=("provider_decision_class", "correlation_hash"),
        ),
        _rule(
            FailureCode.PROVIDER_CONFIGURATION_INVALID,
            _outcome(AI, before, not_invoked, NOT_APPLICABLE, TERMINAL),
            _outcome(OUTBOUND, before, not_invoked, KNOWN_NOT_APPLIED, TERMINAL),
            consumes_attempt=True,
            consumes_budget=True,
            evidence=("configuration_version", "configuration_digest"),
        ),
        _rule(
            FailureCode.PROVIDER_REQUEST_REJECTED,
            _outcome(AI, processing, invoked, NOT_APPLICABLE, TERMINAL),
            _outcome(OUTBOUND, processing, invoked, KNOWN_NOT_APPLIED, TERMINAL),
            consumes_attempt=True,
            consumes_budget=True,
            evidence=("rejection_class", "field_reason_codes"),
        ),
        _rule(
            FailureCode.PROVIDER_RESPONSE_SCHEMA_INVALID,
            _outcome(AI, validation, invoked, NOT_APPLICABLE, RETRY),
            _outcome(OUTBOUND, validation, invoked, KNOWN_NOT_APPLIED, RETRY),
            _outcome(OUTBOUND, validation, invoked, UNKNOWN, RECONCILE),
            consumes_attempt=True,
            consumes_budget=True,
            evidence=("schema_version", "validation_reason_codes", "response_hash"),
        ),
        _rule(
            FailureCode.OUTBOUND_DESTINATION_REJECTED,
            _outcome(OUTBOUND, processing, invoked, KNOWN_NOT_APPLIED, REVISE),
            consumes_attempt=True,
            consumes_budget=True,
            evidence=("destination_reference_hash", "rejection_code"),
        ),
        _rule(
            FailureCode.OUTBOUND_PAYLOAD_REJECTED,
            _outcome(OUTBOUND, processing, invoked, KNOWN_NOT_APPLIED, REVISE),
            consumes_attempt=True,
            consumes_budget=True,
            evidence=("proposal_digest", "field_reason_codes"),
        ),
        same_for_both(
            FailureCode.CALLBACK_RESPONSE_LOST_AFTER_COMMIT,
            callback,
            invocation_na,
            NOT_APPLICABLE,
            REPLAY,
            consumes_attempt=False,
            consumes_budget=False,
            evidence=("callback_command_id", "command_key_digest", "response_loss_class"),
        ),
        same_for_both(
            FailureCode.CALLBACK_AUTHENTICATION_FAILED,
            callback,
            invocation_na,
            NOT_APPLICABLE,
            NO_CHANGE,
            consumes_attempt=False,
            consumes_budget=False,
            evidence=("credential_version_reference", "denial_code"),
        ),
        same_for_both(
            FailureCode.CALLBACK_CREDENTIAL_INVALID,
            callback,
            invocation_na,
            NOT_APPLICABLE,
            NO_CHANGE,
            consumes_attempt=False,
            consumes_budget=False,
            evidence=("attempt_id", "credential_version", "expiry_class"),
        ),
        _rule(
            FailureCode.ATTEMPT_PENDING_STALE,
            _outcome(AI, before, not_invoked, NOT_APPLICABLE, RETRY),
            _outcome(OUTBOUND, before, not_invoked, KNOWN_NOT_APPLIED, RETRY),
            consumes_attempt=True,
            consumes_budget=True,
            evidence=("created_at", "assessed_at", "unclaimed_proof"),
        ),
        _rule(
            FailureCode.AI_ATTEMPT_RUNNING_STALE,
            _outcome(AI, processing, invocation_unknown, NOT_APPLICABLE, RETRY),
            consumes_attempt=True,
            consumes_budget=True,
            evidence=("started_at", "assessed_at", "callback_absence"),
        ),
        _rule(
            FailureCode.OUTBOUND_OUTCOME_UNCERTAIN,
            _outcome(
                OUTBOUND,
                processing,
                (invocation_unknown, invoked),
                UNKNOWN,
                RECONCILE,
            ),
            consumes_attempt=True,
            consumes_budget=True,
            evidence=("started_at", "correlation_hash", "uncertainty_reason"),
        ),
        _rule(
            FailureCode.RECONCILIATION_CONFIRMED_SUCCESS,
            _outcome(OUTBOUND, reconciliation, invoked, APPLIED, NO_CHANGE),
            consumes_attempt=False,
            consumes_budget=False,
            evidence=("provider_correlation", "evidence_hash", "confirmed_at"),
        ),
        _rule(
            FailureCode.RECONCILIATION_CONFIRMED_NOT_APPLIED,
            _outcome(
                OUTBOUND,
                reconciliation,
                (invoked, invocation_unknown),
                KNOWN_NOT_APPLIED,
                RETRY,
            ),
            consumes_attempt=False,
            consumes_budget=False,
            evidence=("provider_correlation", "evidence_hash", "confirmed_at"),
        ),
        _rule(
            FailureCode.RECONCILIATION_PERMANENT_REJECTION,
            _outcome(OUTBOUND, reconciliation, invoked, KNOWN_NOT_APPLIED, TERMINAL),
            consumes_attempt=False,
            consumes_budget=False,
            evidence=("rejection_class", "evidence_hash"),
        ),
        _rule(
            FailureCode.OUTBOUND_OUTCOME_UNRESOLVED,
            _outcome(
                OUTBOUND,
                reconciliation,
                (invoked, invocation_unknown),
                UNKNOWN,
                TERMINAL,
            ),
            consumes_attempt=False,
            consumes_budget=False,
            evidence=("deadline", "assessed_at", "correlation_hash", "evidence_hash"),
        ),
        _rule(
            FailureCode.RETRY_BUDGET_EXHAUSTED,
            *(
                EvidenceOutcomeRule(
                    operation_kind=kind,
                    stages=tuple(FailureStage),
                    provider_invocations=tuple(ProviderInvocation),
                    customer_side_effects=tuple(CustomerSideEffect),
                    disposition=TERMINAL,
                )
                for kind in both
            ),
            consumes_attempt=False,
            consumes_budget=False,
            evidence=("attempt_number", "maximum_attempts", "policy_identity"),
        ),
        _rule(
            FailureCode.MANAGER_TERMINAL_DISPOSITION,
            *(
                EvidenceOutcomeRule(
                    operation_kind=kind,
                    stages=tuple(FailureStage),
                    provider_invocations=tuple(ProviderInvocation),
                    customer_side_effects=tuple(CustomerSideEffect),
                    disposition=TERMINAL,
                )
                for kind in both
            ),
            consumes_attempt=False,
            consumes_budget=False,
            evidence=("actor_id", "actor_role", "rationale_reference", "prior_failure_code"),
        ),
        _rule(
            FailureCode.ADMINISTRATOR_TERMINAL_DISPOSITION,
            *(
                EvidenceOutcomeRule(
                    operation_kind=kind,
                    stages=tuple(FailureStage),
                    provider_invocations=tuple(ProviderInvocation),
                    customer_side_effects=tuple(CustomerSideEffect),
                    disposition=TERMINAL,
                )
                for kind in both
            ),
            consumes_attempt=False,
            consumes_budget=False,
            evidence=("actor_id", "actor_role", "rationale_reference", "prior_failure_code"),
        ),
        same_for_both(
            FailureCode.INTERNAL_TRANSACTION_FAILED_BEFORE_COMMIT,
            internal,
            invocation_na,
            NOT_APPLICABLE,
            REPLAY,
            consumes_attempt=False,
            consumes_budget=False,
            evidence=("command_id", "correlation_id", "safe_error_class"),
        ),
    )


DEMO_POLICY_CONTENT = FailureRecoveryPolicyContent(
    operation_kind_rules=(
        OperationKindRule(
            operation_kind=AI,
            allowed_customer_side_effects=(NOT_APPLICABLE,),
            success_closes_operation=True,
            material_revision_preserves_budget=False,
        ),
        OperationKindRule(
            operation_kind=OUTBOUND,
            allowed_customer_side_effects=(NOT_APPLICABLE, KNOWN_NOT_APPLIED, APPLIED, UNKNOWN),
            success_closes_operation=True,
            material_revision_preserves_budget=True,
        ),
    ),
    failure_code_catalog=_catalog(),
    attempt_budgets=(
        AttemptBudgetRule(operation_kind=AI, maximum_attempts=3),
        AttemptBudgetRule(operation_kind=OUTBOUND, maximum_attempts=3),
    ),
    retry_delay_schedule=(
        RetryDelayRule(operation_kind=AI, failed_attempt_number=1, delay_seconds=30),
        RetryDelayRule(operation_kind=AI, failed_attempt_number=2, delay_seconds=120),
        RetryDelayRule(operation_kind=OUTBOUND, failed_attempt_number=1, delay_seconds=60),
        RetryDelayRule(operation_kind=OUTBOUND, failed_attempt_number=2, delay_seconds=300),
    ),
    stale_attempt_thresholds=StaleAttemptThresholds(
        pending_seconds=120,
        ai_running_seconds=300,
    ),
    reconciliation_rules=ReconciliationRules(
        operation_kind=OUTBOUND,
        unknown_side_effect_disposition=RECONCILE,
        deadline_seconds_after_started=900,
        uncertainty_code=FailureCode.OUTBOUND_OUTCOME_UNCERTAIN,
        confirmed_success_code=FailureCode.RECONCILIATION_CONFIRMED_SUCCESS,
        confirmed_not_applied_code=FailureCode.RECONCILIATION_CONFIRMED_NOT_APPLIED,
        permanent_rejection_code=FailureCode.RECONCILIATION_PERMANENT_REJECTION,
        unresolved_terminal_code=FailureCode.OUTBOUND_OUTCOME_UNRESOLVED,
    ),
    recovery_disposition_rules=(
        RecoveryDispositionRule(
            operation_kind=AI,
            customer_side_effect=NOT_APPLICABLE,
            allowed_dispositions=(RETRY, REPLAY, TERMINAL, NO_CHANGE),
        ),
        RecoveryDispositionRule(
            operation_kind=OUTBOUND,
            customer_side_effect=NOT_APPLICABLE,
            allowed_dispositions=(REPLAY, TERMINAL, NO_CHANGE),
        ),
        RecoveryDispositionRule(
            operation_kind=OUTBOUND,
            customer_side_effect=KNOWN_NOT_APPLIED,
            allowed_dispositions=(RETRY, REVISE, TERMINAL),
        ),
        RecoveryDispositionRule(
            operation_kind=OUTBOUND,
            customer_side_effect=APPLIED,
            allowed_dispositions=(NO_CHANGE,),
        ),
        RecoveryDispositionRule(
            operation_kind=OUTBOUND,
            customer_side_effect=UNKNOWN,
            allowed_dispositions=(RECONCILE, TERMINAL),
        ),
    ),
    terminalization_rules=TerminalizationRules(
        exhaustion_code=FailureCode.RETRY_BUDGET_EXHAUSTED,
        final_attempt_is_terminal=True,
        success_permanently_closes_operation=True,
        terminal_work_can_reopen=False,
        unresolved_outbound_code=FailureCode.OUTBOUND_OUTCOME_UNRESOLVED,
    ),
    globally_forbidden_evidence=(
        "secrets",
        "credentials",
        "unrestricted_provider_bodies",
        "stack_traces",
        "customer_text",
        "raw_contact_details",
        "arbitrary_metadata",
    ),
)


def canonical_policy_bytes(content: FailureRecoveryPolicyContent) -> bytes:
    """Return the migration-compatible canonical JSON representation."""
    return json.dumps(
        content.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def policy_content_digest(content: FailureRecoveryPolicyContent) -> str:
    return hashlib.sha256(canonical_policy_bytes(content)).hexdigest()


DEMO_POLICY_CONTENT_DIGEST = policy_content_digest(DEMO_POLICY_CONTENT)
DEMO_FAILURE_RECOVERY_POLICY = FailureRecoveryPolicyVersion(
    id=DEMO_POLICY_ID,
    policy_key=DEMO_POLICY_KEY,
    semantic_version=DEMO_POLICY_SEMANTIC_VERSION,
    revision=DEMO_POLICY_REVISION,
    content_digest=DEMO_POLICY_CONTENT_DIGEST,
    effective_at=DEMO_POLICY_EFFECTIVE_AT,
    status=PolicyStatus.ACTIVE,
    content=DEMO_POLICY_CONTENT,
)


def policy_identities_equal(left: FailurePolicyIdentity, right: FailurePolicyIdentity) -> bool:
    """Compare every persisted identity component, including the content digest."""
    return left == right


def require_policy_identity(expected: FailurePolicyIdentity, actual: FailurePolicyIdentity) -> None:
    if not policy_identities_equal(expected, actual):
        raise FailurePolicyError(
            "FAILURE_POLICY_VERSION_CONFLICT",
            "failure policy identity does not match the expected immutable version",
        )


def maximum_attempts_for(
    operation_kind: OperationKind,
    policy: FailureRecoveryPolicyVersion = DEMO_FAILURE_RECOVERY_POLICY,
) -> int:
    return next(
        rule.maximum_attempts
        for rule in policy.content.attempt_budgets
        if rule.operation_kind is operation_kind
    )


def retry_delay_for(
    operation_kind: OperationKind,
    failed_attempt_number: int,
    policy: FailureRecoveryPolicyVersion = DEMO_FAILURE_RECOVERY_POLICY,
) -> timedelta:
    for rule in policy.content.retry_delay_schedule:
        if (
            rule.operation_kind is operation_kind
            and rule.failed_attempt_number == failed_attempt_number
        ):
            return timedelta(seconds=rule.delay_seconds)
    raise FailurePolicyError(
        "RETRY_BUDGET_EXHAUSTED",
        "no retry delay exists after the final permitted attempt",
    )


def later_time(policy_time: datetime, provider_retry_after_at: datetime | None) -> datetime:
    """A Retry-After hint may lengthen, but can never shorten, policy delay."""
    require_aware(policy_time, field_name="policy_time")
    if provider_retry_after_at is None:
        return policy_time
    require_aware(provider_retry_after_at, field_name="provider_retry_after_at")
    return max(policy_time, provider_retry_after_at)


def is_at_or_after(database_now: datetime, boundary: datetime) -> bool:
    require_aware(database_now, field_name="database_now")
    require_aware(boundary, field_name="boundary")
    return database_now >= boundary


def is_retry_eligible(database_now: datetime, next_eligible_at: datetime) -> bool:
    return is_at_or_after(database_now, next_eligible_at)


def pending_stale_at(
    created_at: datetime,
    policy: FailureRecoveryPolicyVersion = DEMO_FAILURE_RECOVERY_POLICY,
) -> datetime:
    require_aware(created_at, field_name="created_at")
    return created_at + timedelta(seconds=policy.content.stale_attempt_thresholds.pending_seconds)


def ai_running_stale_at(
    started_at: datetime,
    policy: FailureRecoveryPolicyVersion = DEMO_FAILURE_RECOVERY_POLICY,
) -> datetime:
    require_aware(started_at, field_name="started_at")
    return started_at + timedelta(
        seconds=policy.content.stale_attempt_thresholds.ai_running_seconds
    )


def outbound_reconciliation_deadline(
    started_at: datetime,
    policy: FailureRecoveryPolicyVersion = DEMO_FAILURE_RECOVERY_POLICY,
) -> datetime:
    require_aware(started_at, field_name="started_at")
    return started_at + timedelta(
        seconds=policy.content.reconciliation_rules.deadline_seconds_after_started
    )


def is_pending_stale(
    created_at: datetime,
    database_now: datetime,
    policy: FailureRecoveryPolicyVersion = DEMO_FAILURE_RECOVERY_POLICY,
) -> bool:
    return is_at_or_after(database_now, pending_stale_at(created_at, policy))


def is_ai_running_stale(
    started_at: datetime,
    database_now: datetime,
    policy: FailureRecoveryPolicyVersion = DEMO_FAILURE_RECOVERY_POLICY,
) -> bool:
    return is_at_or_after(database_now, ai_running_stale_at(started_at, policy))


def is_outbound_reconciliation_due(
    started_at: datetime,
    database_now: datetime,
    policy: FailureRecoveryPolicyVersion = DEMO_FAILURE_RECOVERY_POLICY,
) -> bool:
    return is_at_or_after(database_now, outbound_reconciliation_deadline(started_at, policy))


def _matching_rule(
    assessment: FailureAssessmentInput,
    policy: FailureRecoveryPolicyVersion,
) -> tuple[FailureCodeRule, EvidenceOutcomeRule]:
    code_rule = next(
        (
            rule
            for rule in policy.content.failure_code_catalog
            if rule.code is assessment.failure_code
        ),
        None,
    )
    if code_rule is None:
        raise FailurePolicyError("RECOVERY_DISPOSITION_CONFLICT", "unknown failure code")
    matches = tuple(
        outcome
        for outcome in code_rule.outcomes
        if outcome.operation_kind is assessment.operation_kind
        and assessment.failure_stage in outcome.stages
        and assessment.provider_invocation in outcome.provider_invocations
        and assessment.customer_side_effect in outcome.customer_side_effects
    )
    if len(matches) != 1:
        raise FailurePolicyError(
            "RECOVERY_DISPOSITION_CONFLICT",
            "failure evidence is inconsistent with the immutable policy catalog",
        )
    return code_rule, matches[0]


def assess_failure(
    assessment: FailureAssessmentInput,
    policy: FailureRecoveryPolicyVersion = DEMO_FAILURE_RECOVERY_POLICY,
) -> FailureAssessment:
    """Derive classification, disposition, budget, and time without reading a clock."""
    maximum_attempts = maximum_attempts_for(assessment.operation_kind, policy)
    if assessment.attempt_number > maximum_attempts:
        raise FailurePolicyError(
            "RETRY_BUDGET_EXHAUSTED", "attempt number exceeds the immutable operation budget"
        )
    _, outcome = _matching_rule(assessment, policy)
    disposition = outcome.disposition
    remaining_attempts = maximum_attempts - assessment.attempt_number
    next_eligible_at = None
    reconciliation_status = ReconciliationStatus.NOT_REQUIRED
    reconciliation_deadline = None
    terminal_reason = assessment.failure_code if disposition is TERMINAL else None

    if disposition is RETRY:
        if remaining_attempts == 0:
            disposition = TERMINAL
            terminal_reason = policy.content.terminalization_rules.exhaustion_code
        else:
            policy_time = assessment.assessed_at + retry_delay_for(
                assessment.operation_kind, assessment.attempt_number, policy
            )
            next_eligible_at = later_time(policy_time, assessment.provider_retry_after_at)
    elif disposition is RECONCILE:
        if assessment.operation_kind is not OUTBOUND or assessment.attempt_started_at is None:
            raise FailurePolicyError(
                "RECOVERY_DISPOSITION_CONFLICT",
                "outbound reconciliation requires the attempt start time",
            )
        reconciliation_status = ReconciliationStatus.REQUIRED
        reconciliation_deadline = outbound_reconciliation_deadline(
            assessment.attempt_started_at, policy
        )

    return FailureAssessment(
        policy=policy.identity,
        operation_kind=assessment.operation_kind,
        failure_code=assessment.failure_code,
        failure_stage=assessment.failure_stage,
        provider_invocation=assessment.provider_invocation,
        customer_side_effect=assessment.customer_side_effect,
        recovery_disposition=disposition,
        attempt_number=assessment.attempt_number,
        maximum_attempts=maximum_attempts,
        remaining_attempts=remaining_attempts,
        next_eligible_at=next_eligible_at,
        provider_retry_after_at=assessment.provider_retry_after_at,
        reconciliation_status=reconciliation_status,
        reconciliation_deadline=reconciliation_deadline,
        terminal_reason=terminal_reason,
        assessed_at=assessment.assessed_at,
    )
