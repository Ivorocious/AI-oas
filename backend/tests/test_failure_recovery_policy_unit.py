from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from ai_operations_automation.failure_recovery import (
    DEMO_FAILURE_RECOVERY_POLICY,
    DEMO_POLICY_CONTENT,
    DEMO_POLICY_CONTENT_DIGEST,
    DEMO_POLICY_EFFECTIVE_AT,
    DEMO_POLICY_ID,
    DEMO_POLICY_KEY,
    DEMO_POLICY_REVISION,
    DEMO_POLICY_SEMANTIC_VERSION,
    CustomerSideEffect,
    FailureAssessmentInput,
    FailureCode,
    FailurePolicyError,
    FailureStage,
    OperationKind,
    ProviderInvocation,
    ReconciliationStatus,
    RecoveryDisposition,
    ai_running_stale_at,
    assess_failure,
    canonical_policy_bytes,
    is_ai_running_stale,
    is_outbound_reconciliation_due,
    is_pending_stale,
    is_retry_eligible,
    later_time,
    maximum_attempts_for,
    outbound_reconciliation_deadline,
    pending_stale_at,
    policy_content_digest,
    policy_identities_equal,
    require_policy_identity,
    retry_delay_for,
)

NOW = datetime(2026, 7, 13, 12, tzinfo=UTC)


def assessment_input(**changes) -> FailureAssessmentInput:
    values = {
        "operation_kind": "AIInterpretation",
        "failure_code": "PROVIDER_TIMEOUT",
        "failure_stage": "ProviderProcessing",
        "provider_invocation": "Invoked",
        "customer_side_effect": "NotApplicable",
        "attempt_number": 1,
        "assessed_at": NOW,
        **changes,
    }
    return FailureAssessmentInput.model_validate(values)


def test_demo_policy_identity_and_canonical_digest_are_exact_and_repeatable() -> None:
    assert DEMO_POLICY_ID.hex == "6e862dfd25c45f9b873d011f86c4bab5"
    assert DEMO_POLICY_KEY == "phase2-demonstration-failure-recovery"
    assert DEMO_POLICY_SEMANTIC_VERSION == "1.0.0"
    assert DEMO_POLICY_REVISION == 1
    assert DEMO_POLICY_EFFECTIVE_AT == datetime(2026, 7, 13, tzinfo=UTC)
    assert DEMO_POLICY_CONTENT_DIGEST == (
        "7eca0e59bbb41878817c52db02350b2e271b254e65e399e77bea4073ade4d1f0"
    )
    assert policy_content_digest(DEMO_POLICY_CONTENT) == DEMO_POLICY_CONTENT_DIGEST
    assert policy_content_digest(DEMO_POLICY_CONTENT) == policy_content_digest(
        DEMO_FAILURE_RECOVERY_POLICY.content
    )
    assert b" " not in canonical_policy_bytes(DEMO_POLICY_CONTENT)
    assert DEMO_FAILURE_RECOVERY_POLICY.identity.content_digest == DEMO_POLICY_CONTENT_DIGEST


def test_policy_snapshot_is_closed_deeply_immutable_and_complete() -> None:
    with pytest.raises(ValidationError):
        DEMO_FAILURE_RECOVERY_POLICY.revision = 2
    with pytest.raises(ValidationError):
        FailureAssessmentInput.model_validate(
            {**assessment_input().model_dump(), "request_status": "TerminalFailure"}
        )
    assert isinstance(DEMO_POLICY_CONTENT.failure_code_catalog, tuple)
    assert {rule.code for rule in DEMO_POLICY_CONTENT.failure_code_catalog} == set(FailureCode)
    assert len(DEMO_POLICY_CONTENT.failure_code_catalog) == 26
    assert DEMO_POLICY_CONTENT.globally_forbidden_evidence == (
        "secrets",
        "credentials",
        "unrestricted_provider_bodies",
        "stack_traces",
        "customer_text",
        "raw_contact_details",
        "arbitrary_metadata",
    )


def test_outbound_and_reconciliation_policy_are_part_of_the_same_snapshot() -> None:
    outbound = next(
        rule
        for rule in DEMO_POLICY_CONTENT.operation_kind_rules
        if rule.operation_kind is OperationKind.OUTBOUND_ACTION
    )
    assert outbound.material_revision_preserves_budget is True
    assert outbound.allowed_customer_side_effects == (
        CustomerSideEffect.NOT_APPLICABLE,
        CustomerSideEffect.KNOWN_NOT_APPLIED,
        CustomerSideEffect.APPLIED,
        CustomerSideEffect.UNKNOWN,
    )
    reconciliation = DEMO_POLICY_CONTENT.reconciliation_rules
    assert reconciliation.deadline_seconds_after_started == 15 * 60
    assert reconciliation.unknown_side_effect_disposition is (
        RecoveryDisposition.RECONCILE_BEFORE_RETRY
    )
    assert reconciliation.unresolved_terminal_code is FailureCode.OUTBOUND_OUTCOME_UNRESOLVED


@pytest.mark.parametrize(
    ("operation_kind", "attempt_number", "expected_seconds"),
    [
        (OperationKind.AI_INTERPRETATION, 1, 30),
        (OperationKind.AI_INTERPRETATION, 2, 120),
        (OperationKind.OUTBOUND_ACTION, 1, 60),
        (OperationKind.OUTBOUND_ACTION, 2, 300),
    ],
)
def test_exact_attempt_budgets_and_retry_delay_schedules(
    operation_kind, attempt_number, expected_seconds
) -> None:
    assert maximum_attempts_for(operation_kind) == 3
    assert retry_delay_for(operation_kind, attempt_number) == timedelta(seconds=expected_seconds)


@pytest.mark.parametrize("operation_kind", list(OperationKind))
def test_no_delay_exists_after_final_attempt(operation_kind) -> None:
    with pytest.raises(FailurePolicyError) as captured:
        retry_delay_for(operation_kind, 3)
    assert captured.value.code == "RETRY_BUDGET_EXHAUSTED"


@pytest.mark.parametrize(
    ("failure_code", "failure_stage", "provider_invocation"),
    [
        (
            FailureCode.WORKFLOW_FAILED_BEFORE_PROVIDER_INVOCATION,
            FailureStage.BEFORE_DISPATCH,
            ProviderInvocation.NOT_INVOKED,
        ),
        (
            FailureCode.PROVIDER_CONNECTION_FAILED,
            FailureStage.DISPATCH,
            ProviderInvocation.INVOCATION_UNKNOWN,
        ),
        (
            FailureCode.PROVIDER_TIMEOUT,
            FailureStage.PROVIDER_PROCESSING,
            ProviderInvocation.INVOKED,
        ),
        (
            FailureCode.PROVIDER_RATE_LIMITED,
            FailureStage.PROVIDER_PROCESSING,
            ProviderInvocation.INVOKED,
        ),
        (
            FailureCode.PROVIDER_TEMPORARILY_UNAVAILABLE,
            FailureStage.PROVIDER_PROCESSING,
            ProviderInvocation.INVOKED,
        ),
        (
            FailureCode.PROVIDER_RESPONSE_SCHEMA_INVALID,
            FailureStage.RESPONSE_VALIDATION,
            ProviderInvocation.INVOKED,
        ),
    ],
)
def test_ai_transient_failure_mapping_is_backend_derived(
    failure_code, failure_stage, provider_invocation
) -> None:
    result = assess_failure(
        assessment_input(
            failure_code=failure_code,
            failure_stage=failure_stage,
            provider_invocation=provider_invocation,
        )
    )
    assert result.recovery_disposition is RecoveryDisposition.RETRY_SAME_OPERATION
    assert result.maximum_attempts == 3
    assert result.remaining_attempts == 2
    assert result.next_eligible_at == NOW + timedelta(seconds=30)
    assert result.customer_side_effect is CustomerSideEffect.NOT_APPLICABLE
    assert result.policy == DEMO_FAILURE_RECOVERY_POLICY.identity


@pytest.mark.parametrize(
    ("failure_code", "failure_stage", "provider_invocation"),
    [
        (
            FailureCode.PROVIDER_AUTHENTICATION_FAILED,
            FailureStage.DISPATCH,
            ProviderInvocation.NOT_INVOKED,
        ),
        (
            FailureCode.PROVIDER_AUTHORIZATION_FAILED,
            FailureStage.PROVIDER_PROCESSING,
            ProviderInvocation.INVOKED,
        ),
        (
            FailureCode.PROVIDER_CONFIGURATION_INVALID,
            FailureStage.BEFORE_DISPATCH,
            ProviderInvocation.NOT_INVOKED,
        ),
        (
            FailureCode.PROVIDER_REQUEST_REJECTED,
            FailureStage.PROVIDER_PROCESSING,
            ProviderInvocation.INVOKED,
        ),
    ],
)
def test_ai_permanent_failure_mapping_is_terminal(
    failure_code, failure_stage, provider_invocation
) -> None:
    result = assess_failure(
        assessment_input(
            failure_code=failure_code,
            failure_stage=failure_stage,
            provider_invocation=provider_invocation,
        )
    )
    assert result.recovery_disposition is RecoveryDisposition.TERMINAL
    assert result.terminal_reason is failure_code
    assert result.next_eligible_at is None


def test_final_ai_transient_attempt_is_terminal_exhaustion() -> None:
    result = assess_failure(assessment_input(attempt_number=3))
    assert result.failure_code is FailureCode.PROVIDER_TIMEOUT
    assert result.recovery_disposition is RecoveryDisposition.TERMINAL
    assert result.terminal_reason is FailureCode.RETRY_BUDGET_EXHAUSTED
    assert result.remaining_attempts == 0
    assert result.next_eligible_at is None


def test_retry_after_may_lengthen_but_never_shorten_policy_delay() -> None:
    policy_time = NOW + timedelta(seconds=30)
    shorter = NOW + timedelta(seconds=10)
    longer = NOW + timedelta(minutes=4)
    assert later_time(policy_time, None) == policy_time
    assert later_time(policy_time, shorter) == policy_time
    assert later_time(policy_time, longer) == longer
    assert (
        assess_failure(assessment_input(provider_retry_after_at=shorter)).next_eligible_at
        == policy_time
    )
    assert (
        assess_failure(assessment_input(provider_retry_after_at=longer)).next_eligible_at == longer
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"customer_side_effect": "KnownNotApplied"},
        {"customer_side_effect": "Applied"},
        {"customer_side_effect": "Unknown"},
        {"failure_stage": "BeforeDispatch"},
        {"provider_invocation": "NotInvoked"},
        {"operation_kind": "OutboundAction"},
    ],
)
def test_inconsistent_evidence_cannot_choose_an_ai_disposition(changes) -> None:
    with pytest.raises(FailurePolicyError) as captured:
        assess_failure(assessment_input(**changes))
    assert captured.value.code == "RECOVERY_DISPOSITION_CONFLICT"


def test_attempt_number_cannot_exceed_operation_budget() -> None:
    with pytest.raises(FailurePolicyError) as captured:
        assess_failure(assessment_input(attempt_number=4))
    assert captured.value.code == "RETRY_BUDGET_EXHAUSTED"


def test_outbound_unknown_outcome_requires_reconciliation_on_same_attempt() -> None:
    started_at = NOW - timedelta(minutes=2)
    result = assess_failure(
        assessment_input(
            operation_kind="OutboundAction",
            failure_code="PROVIDER_TIMEOUT",
            customer_side_effect="Unknown",
            attempt_started_at=started_at,
        )
    )
    assert result.recovery_disposition is RecoveryDisposition.RECONCILE_BEFORE_RETRY
    assert result.reconciliation_status is ReconciliationStatus.REQUIRED
    assert result.reconciliation_deadline == started_at + timedelta(minutes=15)
    assert result.next_eligible_at is None


def test_outbound_unknown_outcome_cannot_be_assessed_without_start_time() -> None:
    with pytest.raises(FailurePolicyError) as captured:
        assess_failure(
            assessment_input(
                operation_kind="OutboundAction",
                failure_code="PROVIDER_TIMEOUT",
                customer_side_effect="Unknown",
            )
        )
    assert captured.value.code == "RECOVERY_DISPOSITION_CONFLICT"


@pytest.mark.parametrize(
    ("failure_code", "expected_disposition"),
    [
        (FailureCode.OUTBOUND_DESTINATION_REJECTED, RecoveryDisposition.REVISE_PROPOSAL),
        (FailureCode.OUTBOUND_PAYLOAD_REJECTED, RecoveryDisposition.REVISE_PROPOSAL),
    ],
)
def test_outbound_material_defects_require_revision(failure_code, expected_disposition) -> None:
    result = assess_failure(
        assessment_input(
            operation_kind="OutboundAction",
            failure_code=failure_code,
            customer_side_effect="KnownNotApplied",
        )
    )
    assert result.recovery_disposition is expected_disposition
    assert result.next_eligible_at is None


def test_exact_equality_qualifies_for_retry_and_stale_assessment() -> None:
    assert is_retry_eligible(NOW, NOW)
    assert not is_retry_eligible(NOW - timedelta(microseconds=1), NOW)

    created_at = NOW
    assert pending_stale_at(created_at) == NOW + timedelta(minutes=2)
    assert not is_pending_stale(created_at, NOW + timedelta(minutes=2, microseconds=-1))
    assert is_pending_stale(created_at, NOW + timedelta(minutes=2))

    started_at = NOW
    assert ai_running_stale_at(started_at) == NOW + timedelta(minutes=5)
    assert not is_ai_running_stale(started_at, NOW + timedelta(minutes=5, microseconds=-1))
    assert is_ai_running_stale(started_at, NOW + timedelta(minutes=5))

    assert outbound_reconciliation_deadline(started_at) == NOW + timedelta(minutes=15)
    assert not is_outbound_reconciliation_due(
        started_at, NOW + timedelta(minutes=15, microseconds=-1)
    )
    assert is_outbound_reconciliation_due(started_at, NOW + timedelta(minutes=15))


def test_naive_time_is_rejected_at_policy_comparison_boundary() -> None:
    naive = datetime(2026, 7, 13, 12)
    with pytest.raises(ValueError, match="timezone-aware"):
        is_retry_eligible(naive, NOW)
    with pytest.raises(ValueError, match="timezone-aware"):
        later_time(NOW, naive)
    with pytest.raises(ValidationError):
        assessment_input(assessed_at=naive)


def test_policy_identity_equality_includes_digest_and_all_version_fields() -> None:
    identity = DEMO_FAILURE_RECOVERY_POLICY.identity
    assert policy_identities_equal(identity, identity.model_copy())
    require_policy_identity(identity, identity.model_copy())
    changed = identity.model_copy(update={"content_digest": "0" * 64})
    assert not policy_identities_equal(identity, changed)
    with pytest.raises(FailurePolicyError) as captured:
        require_policy_identity(identity, changed)
    assert captured.value.code == "FAILURE_POLICY_VERSION_CONFLICT"
