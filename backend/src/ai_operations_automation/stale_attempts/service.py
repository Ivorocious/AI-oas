"""Directly testable, non-HTTP stale AI-attempt assessment command."""

import hashlib
import uuid
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from ai_operations_automation.command_idempotency.models import (
    CommandIdempotencyScope,
    CompletedCommandReplay,
    NewCommandReservation,
)
from ai_operations_automation.command_idempotency.service import CommandIdempotencyService
from ai_operations_automation.db.models.ai_execution import (
    AttemptCallbackCredential,
    IntegrationAttempt,
    LogicalOperation,
)
from ai_operations_automation.db.models.intake import ServiceRequest
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
    FailureStage,
    OperationKind,
    ProviderInvocation,
    RecoveryDisposition,
    assess_failure,
    is_ai_running_stale,
    is_outbound_reconciliation_due,
    is_pending_stale,
    outbound_reconciliation_deadline,
)
from ai_operations_automation.failure_recovery.repository import (
    select_active_failure_policy,
)
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.outbound_identity import outbound_binding_matches

BACKEND_SERVICE_ACTOR_ID = uuid.UUID("ad2f513b-aa42-5bb8-81bd-caeff2fb5078")
ROUTE_TEMPLATE = "/internal/assess-stale-attempt"


@dataclass(frozen=True, slots=True)
class AssessStaleAttemptOutcome:
    logical_http_status: int
    command_id: uuid.UUID
    safe_snapshot: dict[str, Any]
    is_replay: bool


class AssessStaleAttemptService:
    """Assess one exact stale attempt as trusted in-process BackendService code."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def execute(
        self,
        *,
        attempt_id: uuid.UUID,
        expected_attempt_version: int,
        durable_command_key: str,
        correlation_id: uuid.UUID,
    ) -> AssessStaleAttemptOutcome:
        body_hash = hashlib.sha256(
            f"AssessStaleAttempt:{attempt_id}:{expected_attempt_version}".encode()
        ).hexdigest()
        try:
            with self.session_factory() as session:
                with session.begin():
                    idempotency = CommandIdempotencyService(session)
                    resolution = idempotency.reserve(
                        CommandIdempotencyScope(
                            actor_class="BackendService",
                            actor_id=BACKEND_SERVICE_ACTOR_ID,
                            command_intent="AssessStaleAttempt",
                            route_template=ROUTE_TEMPLATE,
                            target_type="IntegrationAttempt",
                            target_id=attempt_id,
                        ),
                        durable_command_key,
                        body_hash,
                        correlation_id,
                    )
                    if isinstance(resolution, CompletedCommandReplay):
                        return self._replay(resolution)
                    return self._execute_new(
                        session=session,
                        idempotency=idempotency,
                        reservation=resolution,
                        attempt_id=attempt_id,
                        expected_attempt_version=expected_attempt_version,
                        correlation_id=correlation_id,
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
        *,
        session: Session,
        idempotency: CommandIdempotencyService,
        reservation: NewCommandReservation,
        attempt_id: uuid.UUID,
        expected_attempt_version: int,
        correlation_id: uuid.UUID,
    ) -> AssessStaleAttemptOutcome:
        attempt = session.scalar(
            select(IntegrationAttempt).where(IntegrationAttempt.id == attempt_id).with_for_update()
        )
        if attempt is None:
            return self._guard(
                idempotency,
                reservation,
                404,
                "ATTEMPT_NOT_FOUND",
                "The requested attempt was not found.",
            )
        if attempt.version != expected_attempt_version:
            return self._guard(
                idempotency,
                reservation,
                409,
                "CONCURRENCY_CONFLICT",
                "The resource version does not match the current version.",
                current_versions={"integration_attempt": attempt.version},
            )
        operation = session.scalar(
            select(LogicalOperation)
            .where(LogicalOperation.id == attempt.logical_operation_id)
            .with_for_update()
        )
        service_request = session.scalar(
            select(ServiceRequest)
            .where(ServiceRequest.id == attempt.service_request_id)
            .with_for_update()
        )
        siblings = session.scalars(
            select(IntegrationAttempt)
            .where(IntegrationAttempt.logical_operation_id == attempt.logical_operation_id)
            .order_by(IntegrationAttempt.attempt_number)
            .with_for_update()
        ).all()
        credentials = session.scalars(
            select(AttemptCallbackCredential)
            .where(AttemptCallbackCredential.integration_attempt_id == attempt.id)
            .with_for_update()
        ).all()
        if operation is None or service_request is None:
            raise RuntimeError("stale attempt ownership graph is incomplete")
        proposal = None
        approval = None
        if attempt.operation_kind == "OutboundAction":
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
        database_now = session.scalar(select(func.now()))
        if database_now is None or database_now.tzinfo is None or database_now.utcoffset() is None:
            raise RuntimeError("database time must be timezone-aware")
        if (
            attempt.operation_kind not in ("AIInterpretation", "OutboundAction")
            or operation.operation_kind != attempt.operation_kind
            or operation.service_request_id != service_request.id
            or attempt.service_request_id != service_request.id
            or operation.succeeded_attempt_id is not None
            or any(
                row.id != attempt.id and row.state in ("Pending", "Running", "Succeeded")
                for row in siblings
            )
        ):
            return self._guard(
                idempotency,
                reservation,
                409,
                "INVALID_STATE_TRANSITION",
                "The attempt cannot be assessed from its current lifecycle context.",
            )
        if attempt.operation_kind == "OutboundAction" and not (
            proposal is not None
            and approval is not None
            and service_request.current_proposed_action_id == proposal.id
            and service_request.status == "ActionPendingExecution"
            and proposal.state == "PendingExecution"
            and proposal.service_request_id == service_request.id
            and proposal.logical_operation_id == operation.id
            and proposal.proposal_series_id
            == operation.proposal_series_id
            == attempt.proposal_series_id
            and attempt.proposed_action_id == proposal.id
            and attempt.proposal_number == proposal.proposal_number
            and attempt.proposal_payload_digest == proposal.payload_digest
            and attempt.approval_decision_id == approval.id == proposal.current_approval_id
            and approval.proposed_action_id == proposal.id
            and approval.proposal_number == proposal.proposal_number
            and approval.payload_digest == proposal.payload_digest
            and approval.decision == "Approved"
            and attempt.stable_outbound_key_scope == operation.outbound_key_scope
            and attempt.stable_outbound_key_digest == operation.outbound_key_digest
            and outbound_binding_matches(
                operation.id, operation.outbound_key_scope, operation.outbound_key_digest
            )
        ):
            return self._guard(
                idempotency,
                reservation,
                409,
                "OUTBOUND_BINDING_CONFLICT",
                "The exact outbound execution binding is no longer valid.",
            )
        unresolved = False
        if attempt.state == "Pending":
            eligible = is_pending_stale(attempt.created_at, database_now)
            failure_code = FailureCode.ATTEMPT_PENDING_STALE
            stage = FailureStage.BEFORE_DISPATCH
            invocation = ProviderInvocation.NOT_INVOKED
        elif (
            attempt.operation_kind == "AIInterpretation"
            and attempt.state == "Running"
            and attempt.started_at is not None
        ):
            eligible = is_ai_running_stale(attempt.started_at, database_now)
            failure_code = FailureCode.AI_ATTEMPT_RUNNING_STALE
            stage = FailureStage.PROVIDER_PROCESSING
            invocation = ProviderInvocation.INVOCATION_UNKNOWN
        elif (
            attempt.operation_kind == "OutboundAction"
            and attempt.state == "Running"
            and attempt.started_at is not None
            and attempt.reconciliation_status == "Required"
            and attempt.customer_side_effect == "Unknown"
            and attempt.reconciliation_deadline
            == outbound_reconciliation_deadline(attempt.started_at)
        ):
            eligible = is_outbound_reconciliation_due(attempt.started_at, database_now)
            failure_code = FailureCode.OUTBOUND_OUTCOME_UNRESOLVED
            stage = FailureStage.RECONCILIATION
            invocation = ProviderInvocation.INVOCATION_UNKNOWN
            unresolved = True
        else:
            return self._guard(
                idempotency,
                reservation,
                409,
                "INVALID_STATE_TRANSITION",
                "The attempt cannot be assessed from its current state.",
            )
        if not eligible:
            return self._guard(
                idempotency,
                reservation,
                409,
                "STALE_ASSESSMENT_NOT_YET_ELIGIBLE",
                "The stale-attempt threshold has not been reached.",
            )

        policy = select_active_failure_policy(session, database_now)
        assessment = assess_failure(
            FailureAssessmentInput(
                operation_kind=OperationKind(attempt.operation_kind),
                failure_code=failure_code,
                failure_stage=stage,
                provider_invocation=invocation,
                customer_side_effect=(
                    CustomerSideEffect.NOT_APPLICABLE
                    if attempt.operation_kind == "AIInterpretation"
                    else CustomerSideEffect.UNKNOWN
                    if unresolved
                    else CustomerSideEffect.KNOWN_NOT_APPLIED
                ),
                attempt_number=attempt.attempt_number,
                assessed_at=database_now,
                attempt_started_at=attempt.started_at,
            ),
            policy,
        )
        retryable = assessment.recovery_disposition is RecoveryDisposition.RETRY_SAME_OPERATION
        resulting_state = "RetryableFailure" if retryable else "TerminalFailure"
        evidence_hash = hashlib.sha256(
            (
                f"{attempt.id}:{failure_code.value}:{attempt.created_at.isoformat()}:"
                f"{attempt.started_at.isoformat() if attempt.started_at else ''}:"
                f"{database_now.isoformat()}"
            ).encode()
        ).hexdigest()
        attempt.state = resulting_state
        attempt.version += 1
        attempt.completed_at = database_now
        attempt.sanitized_error_code = failure_code.value
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
        attempt.provider_retry_after_at = None
        attempt.reconciliation_status = assessment.reconciliation_status.value
        attempt.reconciliation_deadline = None
        if not unresolved:
            attempt.sanitized_evidence_reference = f"internal-command:{reservation.command_id}"
            attempt.sanitized_evidence_hash = evidence_hash
        attempt.terminal_reason = (
            assessment.terminal_reason.value if assessment.terminal_reason is not None else None
        )
        attempt.assessed_at = database_now
        active_credentials = [row for row in credentials if row.state == "Active"]
        if len(active_credentials) != 1:
            raise RuntimeError("stale attempt must have exactly one active callback credential")
        active_credentials[0].state = "Revoked"
        active_credentials[0].revoked_at = database_now
        operation.version += 1
        operation.safe_outcome_summary = {
            "integration_attempt_id": str(attempt.id),
            "attempt_state": resulting_state,
            "failure_code": failure_code.value,
        }
        service_request.version += 1
        service_request.status = resulting_state
        service_request.current_queue = "FailedRetryRequired" if retryable else None
        service_request.recovery_target = (
            "TriagePending"
            if retryable and attempt.operation_kind == "AIInterpretation"
            else "ActionPendingExecution"
            if retryable
            else None
        )
        service_request.recovery_attempt_id = attempt.id
        service_request.failure_summary_code = failure_code.value
        service_request.terminal_at = None if retryable else database_now
        if proposal is not None:
            proposal.state = (
                "RetryableExecutionFailure" if retryable else "TerminalExecutionFailure"
            )
            proposal.version += 1
            if not retryable:
                proposal.terminal_at = database_now
        session.flush()
        safe_evidence = {
            "service_request_id": str(service_request.id),
            "logical_operation_id": str(operation.id),
            "integration_attempt_id": str(attempt.id),
            "attempt_state": resulting_state,
            "service_request_status": resulting_state,
            "service_request_queue": service_request.current_queue,
            "failure_code": failure_code.value,
            "recovery_disposition": assessment.recovery_disposition.value,
            "attempt_number": attempt.attempt_number,
            "remaining_attempts": assessment.remaining_attempts,
            "next_eligible_at": (
                assessment.next_eligible_at.isoformat()
                if assessment.next_eligible_at is not None
                else None
            ),
            "assessed_at": database_now.isoformat(),
        }
        if proposal is not None:
            safe_evidence.update(
                {
                    "proposed_action_id": str(proposal.id),
                    "proposal_state": proposal.state,
                    "proposal_number": proposal.proposal_number,
                    "proposal_payload_digest": proposal.payload_digest,
                    "approval_decision_id": str(approval.id),
                }
            )
        write_audit_and_optional_outbox(
            session,
            AuditSpec(
                event_name="integration_attempt.stale_assessed",
                aggregate_type="IntegrationAttempt",
                aggregate_id=attempt.id,
                aggregate_version=attempt.version,
                actor_type="BackendService",
                actor_reference_id=BACKEND_SERVICE_ACTOR_ID,
                outcome=resulting_state,
                correlation_id=correlation_id,
                causation_id=reservation.command_id,
                command_id=reservation.command_id,
                reason_codes=(failure_code.value,),
                safe_metadata=safe_evidence,
            ),
            OutboxSpec(event_type="integration_attempt.stale_assessed", payload=safe_evidence),
        )
        completed = idempotency.complete(
            reservation,
            200,
            {
                "result": safe_evidence,
                "versions": {
                    "service_request": service_request.version,
                    "logical_operation": operation.version,
                    "integration_attempt": attempt.version,
                    **({"proposed_action": proposal.version} if proposal is not None else {}),
                },
            },
        )
        return AssessStaleAttemptOutcome(
            logical_http_status=200,
            command_id=completed.command_id,
            safe_snapshot=deepcopy(completed.safe_response_snapshot),
            is_replay=False,
        )

    @staticmethod
    def _guard(
        idempotency: CommandIdempotencyService,
        reservation: NewCommandReservation,
        status: int,
        code: str,
        message: str,
        *,
        current_versions: dict[str, int] | None = None,
    ) -> AssessStaleAttemptOutcome:
        completed = idempotency.complete(
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
        )
        return AssessStaleAttemptOutcome(
            logical_http_status=status,
            command_id=completed.command_id,
            safe_snapshot=deepcopy(completed.safe_response_snapshot),
            is_replay=False,
        )

    @staticmethod
    def _replay(replay: CompletedCommandReplay) -> AssessStaleAttemptOutcome:
        return AssessStaleAttemptOutcome(
            logical_http_status=replay.logical_http_status,
            command_id=replay.command_id,
            safe_snapshot=deepcopy(replay.safe_response_snapshot),
            is_replay=True,
        )
