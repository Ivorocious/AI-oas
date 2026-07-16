"""Atomic simulated outbound callbacks and bounded reconciliation start."""

import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from ai_operations_automation.attempt_callbacks.authorization import (
    AuthorizedCallbackCommand,
    CallbackCommandAuthorizer,
)
from ai_operations_automation.attempt_callbacks.models import (
    OutboundRetryableFailureCallbackRequest,
    OutboundSuccessCallbackRequest,
    OutboundTerminalFailureCallbackRequest,
)
from ai_operations_automation.command_idempotency.canonicalization import canonical_command_hash
from ai_operations_automation.command_idempotency.models import (
    CallbackAuthorizationMetadata,
    CompletedCommandReplay,
    NewCommandReservation,
)
from ai_operations_automation.db.models.proposal import ApprovalDecision, ProposedAction
from ai_operations_automation.event_writing import (
    AuditSpec,
    OutboxSpec,
    write_audit_and_optional_outbox,
)
from ai_operations_automation.failure_recovery import (
    CustomerSideEffect,
    FailureAssessmentInput,
    FailureCode,
    FailurePolicyError,
    FailureStage,
    OperationKind,
    ProviderInvocation,
    RecoveryDisposition,
    assess_failure,
)
from ai_operations_automation.failure_recovery.repository import select_active_failure_policy
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService
from ai_operations_automation.outbound_identity import outbound_binding_matches

SUCCESS_ROUTE = "/api/v1/integration-attempts/{attempt_id}/callbacks/succeeded"
RETRYABLE_ROUTE = "/api/v1/integration-attempts/{attempt_id}/callbacks/retryable-failure"
TERMINAL_ROUTE = "/api/v1/integration-attempts/{attempt_id}/callbacks/terminal-failure"

OutboundCommand = (
    OutboundSuccessCallbackRequest
    | OutboundRetryableFailureCallbackRequest
    | OutboundTerminalFailureCallbackRequest
)


@dataclass(frozen=True, slots=True)
class OutboundCallbackOutcome:
    logical_http_status: int
    command_id: uuid.UUID
    safe_snapshot: dict[str, Any]
    is_replay: bool


class OutboundAttemptCallbackService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def succeed(self, **kwargs) -> OutboundCallbackOutcome:
        return self._execute(
            command_intent="CompleteOutboundActionSucceeded",
            route_template=SUCCESS_ROUTE,
            mode="success",
            **kwargs,
        )

    def retryable_failure(self, **kwargs) -> OutboundCallbackOutcome:
        return self._execute(
            command_intent="CompleteOutboundActionRetryableFailure",
            route_template=RETRYABLE_ROUTE,
            mode="retryable",
            **kwargs,
        )

    def terminal_failure(self, **kwargs) -> OutboundCallbackOutcome:
        return self._execute(
            command_intent="CompleteOutboundActionTerminalFailure",
            route_template=TERMINAL_ROUTE,
            mode="terminal",
            **kwargs,
        )

    def _execute(
        self,
        *,
        attempt_id: uuid.UUID,
        command: OutboundCommand,
        raw_idempotency_key: str,
        canonical_body_hash: str,
        correlation_id: uuid.UUID,
        machine: AuthenticatedWorkflowService,
        supplied_credential: str,
        command_intent: str,
        route_template: str,
        mode: str,
    ) -> OutboundCallbackOutcome:
        try:
            with self.session_factory() as session:
                with session.begin():
                    authorized = CallbackCommandAuthorizer(session).authorize(
                        attempt_id=attempt_id,
                        machine=machine,
                        supplied_credential=supplied_credential,
                        raw_idempotency_key=raw_idempotency_key,
                        canonical_body_hash=canonical_body_hash,
                        correlation_id=correlation_id,
                        command_intent=command_intent,
                        route_template=route_template,
                        expected_operation_kind="OutboundAction",
                    )
                    if isinstance(authorized.resolution, CompletedCommandReplay):
                        return self._replay(authorized.resolution)
                    return self._execute_new(
                        session,
                        authorized,
                        authorized.resolution,
                        command,
                        correlation_id,
                        machine,
                        mode,
                    )
        except IntakeError:
            raise
        except OperationalError as exc:
            raise IntakeError(
                503, "DEPENDENCY_UNAVAILABLE", "A required dependency is unavailable.", True
            ) from exc
        except SQLAlchemyError as exc:
            raise IntakeError(
                500, "INTERNAL_ERROR", "The request could not be completed safely."
            ) from exc
        except Exception as exc:
            raise IntakeError(
                500, "INTERNAL_ERROR", "The request could not be completed safely."
            ) from exc

    def _execute_new(
        self,
        session: Session,
        authorized: AuthorizedCallbackCommand,
        reservation: NewCommandReservation,
        command: OutboundCommand,
        correlation_id: uuid.UUID,
        machine: AuthenticatedWorkflowService,
        mode: str,
    ) -> OutboundCallbackOutcome:
        attempt, operation, request = (
            authorized.attempt,
            authorized.operation,
            authorized.service_request,
        )
        proposal = session.scalar(
            select(ProposedAction)
            .where(ProposedAction.id == attempt.proposed_action_id)
            .with_for_update()
        )
        approval = session.scalar(
            select(ApprovalDecision)
            .where(ApprovalDecision.id == attempt.approval_decision_id)
            .with_for_update()
        )
        if attempt.version != command.expected_versions.integration_attempt:
            return self._guard(
                authorized,
                reservation,
                409,
                "CONCURRENCY_CONFLICT",
                "The resource version does not match the current version.",
                current_versions={"integration_attempt": attempt.version},
            )
        if attempt.state != "Running":
            return self._guard(
                authorized,
                reservation,
                409,
                "INTEGRATION_RESULT_CONFLICT",
                "A final result already exists or the attempt is not running.",
            )
        if command.evidence.adapter_version != attempt.adapter_version:
            return self._guard(
                authorized,
                reservation,
                409,
                "FROZEN_OPERATION_MISMATCH",
                "The callback evidence does not match the frozen attempt intent.",
            )
        if not self._exact_graph(attempt, operation, request, proposal, approval):
            return self._guard(
                authorized,
                reservation,
                409,
                "OUTBOUND_BINDING_CONFLICT",
                "The exact outbound execution binding is no longer valid.",
            )
        if mode == "success":
            return self._succeed(
                session, authorized, reservation, command, proposal, correlation_id, machine
            )
        return self._fail(
            session,
            authorized,
            reservation,
            command,
            proposal,
            correlation_id,
            machine,
            terminal=mode == "terminal",
        )

    @staticmethod
    def _exact_graph(attempt, operation, request, proposal, approval) -> bool:
        return bool(
            proposal is not None
            and approval is not None
            and request.current_proposed_action_id == proposal.id
            and request.status == "ActionPendingExecution"
            and proposal.state == "PendingExecution"
            and proposal.service_request_id == request.id == operation.service_request_id
            and proposal.logical_operation_id == operation.id == attempt.logical_operation_id
            and proposal.proposal_series_id
            == operation.proposal_series_id
            == attempt.proposal_series_id
            and attempt.proposed_action_id == proposal.id
            and attempt.proposal_number == proposal.proposal_number
            and attempt.proposal_payload_digest == proposal.payload_digest
            and proposal.current_approval_id == approval.id == attempt.approval_decision_id
            and approval.proposed_action_id == proposal.id
            and approval.proposal_number == proposal.proposal_number
            and approval.payload_digest == proposal.payload_digest
            and approval.decision == "Approved"
            and attempt.stable_outbound_key_scope == operation.outbound_key_scope
            and attempt.stable_outbound_key_digest == operation.outbound_key_digest
            and outbound_binding_matches(
                operation.id, operation.outbound_key_scope, operation.outbound_key_digest
            )
        )

    def _succeed(
        self,
        session,
        authorized,
        reservation,
        command,
        proposal,
        correlation_id,
        machine,
    ) -> OutboundCallbackOutcome:
        attempt, operation, request, credential = (
            authorized.attempt,
            authorized.operation,
            authorized.service_request,
            authorized.credential,
        )
        evidence = command.evidence
        completed_at = authorized.database_now
        result_hash = canonical_command_hash(evidence)
        prior_uncertainty = (
            {
                "failure_code": attempt.sanitized_error_code,
                "evidence_reference": attempt.sanitized_evidence_reference,
                "evidence_hash": attempt.sanitized_evidence_hash,
                "reconciliation_deadline": attempt.reconciliation_deadline.isoformat(),
            }
            if attempt.reconciliation_status == "Required"
            and attempt.reconciliation_deadline is not None
            else None
        )
        attempt.state = "Succeeded"
        attempt.version += 1
        attempt.completed_at = completed_at
        attempt.safe_provider_correlation = evidence.safe_provider_correlation
        attempt.result_hash = result_hash
        for field in (
            "sanitized_error_code",
            "failure_policy_id",
            "failure_policy_semantic_version",
            "failure_policy_revision",
            "failure_policy_digest",
            "failure_stage",
            "provider_invocation",
            "customer_side_effect",
            "recovery_disposition",
            "maximum_attempts",
            "remaining_attempts",
            "next_eligible_at",
            "provider_retry_after_at",
            "reconciliation_status",
            "reconciliation_deadline",
            "sanitized_evidence_reference",
            "sanitized_evidence_hash",
            "terminal_reason",
            "assessed_at",
        ):
            setattr(attempt, field, None)
        credential.state, credential.consumed_at = "Consumed", completed_at
        operation.version += 1
        operation.succeeded_attempt_id = attempt.id
        operation.safe_outcome_summary = {
            "simulated_outcome": "Applied",
            "integration_attempt_id": str(attempt.id),
            "result_hash": result_hash,
            **({"prior_uncertainty": prior_uncertainty} if prior_uncertainty else {}),
        }
        previous_queue = request.current_queue
        proposal.state = "Executed"
        proposal.version += 1
        request.status = "Completed"
        request.current_queue = None
        request.recovery_target = None
        request.recovery_attempt_id = None
        request.failure_summary_code = None
        request.version += 1
        session.flush()
        safe = self._safe_result(attempt, operation, request, proposal)
        safe["simulated_outcome"] = "Applied"
        safe["completed_at"] = completed_at.isoformat()
        lifecycle_events = [
            (
                "integration_attempt.succeeded",
                "IntegrationAttempt",
                attempt.id,
                attempt.version,
            ),
            ("proposed_action.executed", "ProposedAction", proposal.id, proposal.version),
            ("service_request.completed", "ServiceRequest", request.id, request.version),
        ]
        if previous_queue != request.current_queue:
            safe["previous_service_request_queue"] = previous_queue
            lifecycle_events.append(
                ("service_request.queue_changed", "ServiceRequest", request.id, request.version)
            )
        self._events(
            session,
            reservation,
            correlation_id,
            machine,
            safe,
            lifecycle_events,
        )
        return self._complete(authorized, reservation, safe, proposal)

    def _fail(
        self,
        session,
        authorized,
        reservation,
        command,
        proposal,
        correlation_id,
        machine,
        *,
        terminal: bool,
    ) -> OutboundCallbackOutcome:
        attempt, operation, request, credential = (
            authorized.attempt,
            authorized.operation,
            authorized.service_request,
            authorized.credential,
        )
        evidence = command.evidence
        if (
            evidence.customer_side_effect == "Unknown"
            and getattr(evidence, "retry_after_seconds", None) is not None
        ):
            return self._guard(
                authorized,
                reservation,
                409,
                "RECOVERY_DISPOSITION_CONFLICT",
                "Unknown outbound outcomes cannot carry retry eligibility.",
            )
        retry_after = getattr(evidence, "retry_after_seconds", None)
        retry_after_at = (
            authorized.database_now + timedelta(seconds=retry_after)
            if retry_after is not None
            else None
        )
        policy = select_active_failure_policy(session, authorized.database_now)
        try:
            assessment = assess_failure(
                FailureAssessmentInput(
                    operation_kind=OperationKind.OUTBOUND_ACTION,
                    failure_code=FailureCode(evidence.failure_code),
                    failure_stage=FailureStage(evidence.failure_stage),
                    provider_invocation=ProviderInvocation(evidence.provider_invocation),
                    customer_side_effect=CustomerSideEffect(evidence.customer_side_effect),
                    attempt_number=attempt.attempt_number,
                    assessed_at=authorized.database_now,
                    attempt_started_at=attempt.started_at,
                    provider_retry_after_at=retry_after_at,
                ),
                policy,
            )
        except FailurePolicyError as exc:
            return self._guard(
                authorized,
                reservation,
                409,
                exc.code,
                "The failure evidence conflicts with the active recovery policy.",
            )
        if (
            assessment.recovery_disposition is RecoveryDisposition.REVISE_PROPOSAL
            and assessment.remaining_attempts == 0
        ):
            assessment = assessment.model_copy(
                update={
                    "recovery_disposition": RecoveryDisposition.TERMINAL,
                    "terminal_reason": FailureCode.RETRY_BUDGET_EXHAUSTED,
                }
            )
        if terminal and assessment.recovery_disposition is not RecoveryDisposition.TERMINAL:
            return self._guard(
                authorized,
                reservation,
                409,
                "RECOVERY_DISPOSITION_CONFLICT",
                "The supplied evidence is not a terminal outbound outcome.",
            )
        if not terminal and assessment.recovery_disposition is RecoveryDisposition.TERMINAL:
            pass  # final-attempt exhaustion is derived by the backend
        is_uncertain = assessment.recovery_disposition is RecoveryDisposition.RECONCILE_BEFORE_RETRY
        is_retryable = assessment.recovery_disposition in (
            RecoveryDisposition.RETRY_SAME_OPERATION,
            RecoveryDisposition.REVISE_PROPOSAL,
        )
        attempt.version += 1
        attempt.safe_provider_correlation = evidence.safe_provider_correlation
        attempt.sanitized_error_code = evidence.failure_code
        attempt.failure_policy_id = assessment.policy.policy_id
        attempt.failure_policy_semantic_version = assessment.policy.semantic_version
        attempt.failure_policy_revision = assessment.policy.revision
        attempt.failure_policy_digest = assessment.policy.content_digest
        attempt.failure_stage = assessment.failure_stage.value
        attempt.provider_invocation = assessment.provider_invocation.value
        attempt.customer_side_effect = assessment.customer_side_effect.value
        attempt.recovery_disposition = assessment.recovery_disposition.value
        attempt.maximum_attempts = assessment.maximum_attempts
        attempt.remaining_attempts = assessment.remaining_attempts
        attempt.next_eligible_at = assessment.next_eligible_at
        attempt.provider_retry_after_at = assessment.provider_retry_after_at
        attempt.reconciliation_status = assessment.reconciliation_status.value
        attempt.reconciliation_deadline = assessment.reconciliation_deadline
        attempt.sanitized_evidence_reference = evidence.safe_evidence_reference
        attempt.sanitized_evidence_hash = evidence.safe_evidence_hash
        attempt.terminal_reason = (
            assessment.terminal_reason.value if assessment.terminal_reason is not None else None
        )
        attempt.assessed_at = assessment.assessed_at
        operation.version += 1
        if is_uncertain:
            operation.safe_outcome_summary = {
                "integration_attempt_id": str(attempt.id),
                "simulated_outcome": "Uncertain",
                "reconciliation_deadline": assessment.reconciliation_deadline.isoformat(),
            }
            session.flush()
            safe = self._safe_result(attempt, operation, request, proposal)
            safe.update(
                {
                    "failure_code": evidence.failure_code,
                    "recovery_disposition": assessment.recovery_disposition.value,
                    "customer_side_effect": assessment.customer_side_effect.value,
                    "maximum_attempts": assessment.maximum_attempts,
                    "remaining_attempts": assessment.remaining_attempts,
                    "reconciliation_deadline": assessment.reconciliation_deadline.isoformat(),
                }
            )
            self._events(
                session,
                reservation,
                correlation_id,
                machine,
                safe,
                (
                    (
                        "integration_attempt.reconciliation_started",
                        "IntegrationAttempt",
                        attempt.id,
                        attempt.version,
                    ),
                ),
                outbox=False,
            )
            return self._complete(authorized, reservation, safe, proposal)

        resulting_state = "RetryableFailure" if is_retryable else "TerminalFailure"
        attempt.state = resulting_state
        attempt.completed_at = authorized.database_now
        credential.state, credential.consumed_at = "Consumed", authorized.database_now
        operation.safe_outcome_summary = {
            "integration_attempt_id": str(attempt.id),
            "attempt_state": resulting_state,
            "failure_code": evidence.failure_code,
        }
        previous_queue = request.current_queue
        proposal.state = "RetryableExecutionFailure" if is_retryable else "TerminalExecutionFailure"
        proposal.version += 1
        if not is_retryable:
            proposal.terminal_at = authorized.database_now
        request.status = "RetryableFailure" if is_retryable else "TerminalFailure"
        request.current_queue = "FailedRetryRequired" if is_retryable else None
        request.recovery_target = "ActionPendingExecution" if is_retryable else None
        request.recovery_attempt_id = attempt.id
        request.failure_summary_code = evidence.failure_code
        request.terminal_at = None if is_retryable else authorized.database_now
        request.version += 1
        session.flush()
        safe = self._safe_result(attempt, operation, request, proposal)
        safe.update(
            {
                "failure_code": evidence.failure_code,
                "recovery_disposition": assessment.recovery_disposition.value,
                "customer_side_effect": assessment.customer_side_effect.value,
                "maximum_attempts": assessment.maximum_attempts,
                "remaining_attempts": assessment.remaining_attempts,
                "next_eligible_at": assessment.next_eligible_at.isoformat()
                if assessment.next_eligible_at
                else None,
                "completed_at": authorized.database_now.isoformat(),
            }
        )
        suffix = "retryable_failure" if is_retryable else "terminal_failure"
        proposal_suffix = (
            "retryable_execution_failure" if is_retryable else "terminal_execution_failure"
        )
        lifecycle_events = [
            (
                f"integration_attempt.{suffix}",
                "IntegrationAttempt",
                attempt.id,
                attempt.version,
            ),
            (
                f"proposed_action.{proposal_suffix}",
                "ProposedAction",
                proposal.id,
                proposal.version,
            ),
            (f"service_request.{suffix}", "ServiceRequest", request.id, request.version),
        ]
        if previous_queue != request.current_queue:
            safe["previous_service_request_queue"] = previous_queue
            lifecycle_events.append(
                ("service_request.queue_changed", "ServiceRequest", request.id, request.version)
            )
        self._events(
            session,
            reservation,
            correlation_id,
            machine,
            safe,
            lifecycle_events,
        )
        return self._complete(authorized, reservation, safe, proposal)

    @staticmethod
    def _safe_result(attempt, operation, request, proposal) -> dict[str, Any]:
        return {
            "service_request_id": str(request.id),
            "proposed_action_id": str(proposal.id),
            "proposal_series_id": str(proposal.proposal_series_id),
            "proposal_number": proposal.proposal_number,
            "proposal_payload_digest": proposal.payload_digest,
            "approval_decision_id": str(attempt.approval_decision_id),
            "logical_operation_id": str(operation.id),
            "integration_attempt_id": str(attempt.id),
            "attempt_number": attempt.attempt_number,
            "attempt_state": attempt.state,
            "proposal_state": proposal.state,
            "service_request_status": request.status,
            "service_request_queue": request.current_queue,
        }

    @staticmethod
    def _events(
        session,
        reservation,
        correlation_id,
        machine,
        safe,
        events,
        *,
        outbox=True,
    ) -> None:
        for name, aggregate_type, aggregate_id, version in events:
            write_audit_and_optional_outbox(
                session,
                AuditSpec(
                    event_name=name,
                    aggregate_type=aggregate_type,
                    aggregate_id=aggregate_id,
                    aggregate_version=version,
                    actor_type="WorkflowService",
                    actor_reference_id=machine.machine_identity_id,
                    outcome=safe["attempt_state"],
                    correlation_id=correlation_id,
                    causation_id=reservation.command_id,
                    command_id=reservation.command_id,
                    reason_codes=(safe["failure_code"],) if safe.get("failure_code") else (),
                    safe_metadata=safe,
                ),
                OutboxSpec(event_type=name, payload=safe) if outbox else None,
            )

    @staticmethod
    def _complete(authorized, reservation, safe, proposal) -> OutboundCallbackOutcome:
        completed = authorized.idempotency.complete(
            reservation,
            200,
            {
                "result": safe,
                "versions": {
                    "service_request": authorized.service_request.version,
                    "proposed_action": proposal.version,
                    "logical_operation": authorized.operation.version,
                    "integration_attempt": authorized.attempt.version,
                },
            },
            callback_authorization=CallbackAuthorizationMetadata(
                callback_credential_id=authorized.credential.id,
                callback_credential_version=authorized.credential.credential_version,
            ),
        )
        return OutboundCallbackOutcome(
            200, completed.command_id, deepcopy(completed.safe_response_snapshot), False
        )

    @staticmethod
    def _guard(
        authorized,
        reservation,
        status,
        code,
        message,
        *,
        current_versions=None,
    ) -> OutboundCallbackOutcome:
        completed = authorized.idempotency.complete(
            reservation,
            status,
            {
                "error": {
                    "schema_version": "1.0",
                    "code": code,
                    "message": message,
                    "retryable": False,
                    "current_versions": current_versions or {},
                    "details": [],
                }
            },
            callback_authorization=CallbackAuthorizationMetadata(
                callback_credential_id=authorized.credential.id,
                callback_credential_version=authorized.credential.credential_version,
            ),
        )
        return OutboundCallbackOutcome(
            status, completed.command_id, deepcopy(completed.safe_response_snapshot), False
        )

    @staticmethod
    def _replay(replay: CompletedCommandReplay) -> OutboundCallbackOutcome:
        return OutboundCallbackOutcome(
            replay.logical_http_status,
            replay.command_id,
            deepcopy(replay.safe_response_snapshot),
            True,
        )
