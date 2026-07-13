"""Atomic AI result callbacks with consumed-credential replay binding."""

import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from ai_operations_automation.attempt_callbacks.authorization import (
    AuthorizedCallbackCommand,
    CallbackCommandAuthorizer,
)
from ai_operations_automation.attempt_callbacks.models import (
    AiRetryableFailureCallbackRequest,
    AiSuccessCallbackRequest,
    AiTerminalFailureCallbackRequest,
)
from ai_operations_automation.command_idempotency.canonicalization import canonical_command_hash
from ai_operations_automation.command_idempotency.models import (
    CallbackAuthorizationMetadata,
    CompletedCommandReplay,
    NewCommandReservation,
)
from ai_operations_automation.db.models.ai_execution import AiInterpretation
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
from ai_operations_automation.failure_recovery.repository import (
    select_active_failure_policy,
)
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService

SUCCESS_ROUTE = "/api/v1/integration-attempts/{attempt_id}/callbacks/succeeded"
RETRYABLE_FAILURE_ROUTE = "/api/v1/integration-attempts/{attempt_id}/callbacks/retryable-failure"
TERMINAL_FAILURE_ROUTE = "/api/v1/integration-attempts/{attempt_id}/callbacks/terminal-failure"

RETRYABLE_FAILURE_EVIDENCE = {
    "WORKFLOW_FAILED_BEFORE_PROVIDER_INVOCATION": (
        FailureStage.BEFORE_DISPATCH,
        ProviderInvocation.NOT_INVOKED,
    ),
    "PROVIDER_CONNECTION_FAILED": (
        FailureStage.DISPATCH,
        ProviderInvocation.INVOCATION_UNKNOWN,
    ),
    "PROVIDER_TIMEOUT": (
        FailureStage.PROVIDER_PROCESSING,
        ProviderInvocation.INVOKED,
    ),
    "PROVIDER_RATE_LIMITED": (
        FailureStage.PROVIDER_PROCESSING,
        ProviderInvocation.INVOKED,
    ),
    "PROVIDER_TEMPORARILY_UNAVAILABLE": (
        FailureStage.PROVIDER_PROCESSING,
        ProviderInvocation.INVOKED,
    ),
    "PROVIDER_RESPONSE_SCHEMA_INVALID": (
        FailureStage.RESPONSE_VALIDATION,
        ProviderInvocation.INVOKED,
    ),
}
TERMINAL_FAILURE_EVIDENCE = {
    "PROVIDER_AUTHENTICATION_FAILED": (
        FailureStage.DISPATCH,
        ProviderInvocation.NOT_INVOKED,
    ),
    "PROVIDER_AUTHORIZATION_FAILED": (
        FailureStage.PROVIDER_PROCESSING,
        ProviderInvocation.INVOKED,
    ),
    "PROVIDER_CONFIGURATION_INVALID": (
        FailureStage.BEFORE_DISPATCH,
        ProviderInvocation.NOT_INVOKED,
    ),
    "PROVIDER_REQUEST_REJECTED": (
        FailureStage.PROVIDER_PROCESSING,
        ProviderInvocation.INVOKED,
    ),
}


@dataclass(frozen=True, slots=True)
class AiCallbackOutcome:
    logical_http_status: int
    command_id: uuid.UUID
    safe_snapshot: dict[str, Any]
    is_replay: bool


class AiAttemptCallbackService:
    """Finalize one assigned AI attempt without invoking any provider."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def succeed(
        self,
        *,
        attempt_id: uuid.UUID,
        command: AiSuccessCallbackRequest,
        raw_idempotency_key: str,
        canonical_body_hash: str,
        correlation_id: uuid.UUID,
        machine: AuthenticatedWorkflowService,
        supplied_credential: str,
    ) -> AiCallbackOutcome:
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
                        command_intent="CompleteAiInterpretationSucceeded",
                        route_template=SUCCESS_ROUTE,
                        expected_operation_kind="AIInterpretation",
                    )
                    if isinstance(authorized.resolution, CompletedCommandReplay):
                        return self._replay(authorized.resolution)
                    return self._succeed_new(
                        session=session,
                        authorized=authorized,
                        reservation=authorized.resolution,
                        command=command,
                        correlation_id=correlation_id,
                        machine=machine,
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

    def retryable_failure(
        self,
        *,
        attempt_id: uuid.UUID,
        command: AiRetryableFailureCallbackRequest,
        raw_idempotency_key: str,
        canonical_body_hash: str,
        correlation_id: uuid.UUID,
        machine: AuthenticatedWorkflowService,
        supplied_credential: str,
    ) -> AiCallbackOutcome:
        return self._failure(
            attempt_id=attempt_id,
            command=command,
            raw_idempotency_key=raw_idempotency_key,
            canonical_body_hash=canonical_body_hash,
            correlation_id=correlation_id,
            machine=machine,
            supplied_credential=supplied_credential,
            command_intent="CompleteAiInterpretationRetryableFailure",
            route_template=RETRYABLE_FAILURE_ROUTE,
            evidence_dimensions=RETRYABLE_FAILURE_EVIDENCE,
        )

    def terminal_failure(
        self,
        *,
        attempt_id: uuid.UUID,
        command: AiTerminalFailureCallbackRequest,
        raw_idempotency_key: str,
        canonical_body_hash: str,
        correlation_id: uuid.UUID,
        machine: AuthenticatedWorkflowService,
        supplied_credential: str,
    ) -> AiCallbackOutcome:
        return self._failure(
            attempt_id=attempt_id,
            command=command,
            raw_idempotency_key=raw_idempotency_key,
            canonical_body_hash=canonical_body_hash,
            correlation_id=correlation_id,
            machine=machine,
            supplied_credential=supplied_credential,
            command_intent="CompleteAiInterpretationTerminalFailure",
            route_template=TERMINAL_FAILURE_ROUTE,
            evidence_dimensions=TERMINAL_FAILURE_EVIDENCE,
        )

    def _failure(
        self,
        *,
        attempt_id: uuid.UUID,
        command: AiRetryableFailureCallbackRequest | AiTerminalFailureCallbackRequest,
        raw_idempotency_key: str,
        canonical_body_hash: str,
        correlation_id: uuid.UUID,
        machine: AuthenticatedWorkflowService,
        supplied_credential: str,
        command_intent: str,
        route_template: str,
        evidence_dimensions: dict[str, tuple[FailureStage, ProviderInvocation]],
    ) -> AiCallbackOutcome:
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
                        expected_operation_kind="AIInterpretation",
                    )
                    if isinstance(authorized.resolution, CompletedCommandReplay):
                        return self._replay(authorized.resolution)
                    return self._failure_new(
                        session=session,
                        authorized=authorized,
                        reservation=authorized.resolution,
                        command=command,
                        correlation_id=correlation_id,
                        machine=machine,
                        evidence_dimensions=evidence_dimensions,
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
        except Exception as exc:
            raise IntakeError(
                500, "INTERNAL_ERROR", "The request could not be completed safely."
            ) from exc

    def _succeed_new(
        self,
        *,
        session: Session,
        authorized: AuthorizedCallbackCommand,
        reservation: NewCommandReservation,
        command: AiSuccessCallbackRequest,
        correlation_id: uuid.UUID,
        machine: AuthenticatedWorkflowService,
    ) -> AiCallbackOutcome:
        attempt = authorized.attempt
        operation = authorized.operation
        service_request = authorized.service_request
        credential = authorized.credential
        evidence = command.evidence

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
                "INVALID_STATE_TRANSITION",
                "The callback is not valid for the current attempt state.",
            )
        frozen_identity = (
            operation.result_schema_version,
            operation.prompt_version,
            operation.provider_name,
            operation.model_name,
            operation.adapter_name,
            operation.adapter_version,
        )
        supplied_identity = (
            evidence.result_schema_version,
            evidence.prompt_version,
            evidence.provider_name,
            evidence.model_name,
            evidence.adapter_name,
            evidence.adapter_version,
        )
        if supplied_identity != frozen_identity:
            return self._guard(
                authorized,
                reservation,
                409,
                "FROZEN_OPERATION_MISMATCH",
                "The callback evidence does not match the frozen operation intent.",
            )
        if operation.succeeded_attempt_id is not None:
            raise RuntimeError("running operation already has a success reference")

        completed_at = authorized.database_now
        result_hash = canonical_command_hash(command.evidence)
        next_interpretation_number = (
            int(
                session.scalar(
                    select(
                        func.coalesce(func.max(AiInterpretation.interpretation_number), 0)
                    ).where(AiInterpretation.service_request_id == service_request.id)
                )
                or 0
            )
            + 1
        )
        interpretation_id = uuid.uuid4()
        interpretation = AiInterpretation(
            id=interpretation_id,
            service_request_id=service_request.id,
            logical_operation_id=operation.id,
            producing_attempt_id=attempt.id,
            interpretation_number=next_interpretation_number,
            summary=evidence.interpretation.summary,
            suggested_category=evidence.interpretation.suggested_category,
            missing_information=list(evidence.interpretation.missing_information),
            confidence=evidence.interpretation.confidence,
            input_hash=operation.input_hash,
            configuration_hash=operation.configuration_hash,
            result_schema_version=operation.result_schema_version,
            prompt_version=operation.prompt_version,
            provider_name=operation.provider_name,
            model_name=operation.model_name,
            adapter_name=operation.adapter_name,
            adapter_version=operation.adapter_version,
            safe_provider_correlation=evidence.safe_provider_correlation,
            warnings=list(evidence.interpretation.warning_codes),
            latency_ms=evidence.latency_ms,
            usage_metadata=(
                evidence.token_usage.model_dump(mode="json")
                if evidence.token_usage is not None
                else None
            ),
        )
        attempt.state = "Succeeded"
        attempt.version += 1
        attempt.completed_at = completed_at
        attempt.safe_provider_correlation = evidence.safe_provider_correlation
        attempt.result_hash = result_hash
        credential.state = "Consumed"
        credential.consumed_at = completed_at
        operation.version += 1
        operation.succeeded_attempt_id = attempt.id
        operation.safe_outcome_summary = {
            "interpretation_id": str(interpretation_id),
            "result_hash": result_hash,
        }
        service_request.version += 1
        service_request.current_interpretation_id = interpretation_id
        session.add(interpretation)
        session.flush()

        safe_evidence = {
            "service_request_id": str(service_request.id),
            "logical_operation_id": str(operation.id),
            "integration_attempt_id": str(attempt.id),
            "interpretation_id": str(interpretation_id),
            "attempt_number": attempt.attempt_number,
            "attempt_state": attempt.state,
            "service_request_status": service_request.status,
        }
        write_audit_and_optional_outbox(
            session,
            AuditSpec(
                event_name="integration_attempt.succeeded",
                aggregate_type="IntegrationAttempt",
                aggregate_id=attempt.id,
                aggregate_version=attempt.version,
                actor_type="WorkflowService",
                actor_reference_id=machine.machine_identity_id,
                outcome="Succeeded",
                correlation_id=correlation_id,
                causation_id=reservation.command_id,
                command_id=reservation.command_id,
                reason_codes=(),
                safe_metadata=safe_evidence,
            ),
            OutboxSpec(event_type="integration_attempt.succeeded", payload=safe_evidence),
        )
        snapshot = {
            "result": {
                **safe_evidence,
                "completed_at": completed_at.isoformat(),
            },
            "versions": {
                "service_request": service_request.version,
                "logical_operation": operation.version,
                "integration_attempt": attempt.version,
            },
        }
        completed = authorized.idempotency.complete(
            reservation,
            200,
            snapshot,
            callback_authorization=CallbackAuthorizationMetadata(
                callback_credential_id=credential.id,
                callback_credential_version=credential.credential_version,
            ),
        )
        return AiCallbackOutcome(
            logical_http_status=200,
            command_id=completed.command_id,
            safe_snapshot=deepcopy(completed.safe_response_snapshot),
            is_replay=False,
        )

    def _failure_new(
        self,
        *,
        session: Session,
        authorized: AuthorizedCallbackCommand,
        reservation: NewCommandReservation,
        command: AiRetryableFailureCallbackRequest | AiTerminalFailureCallbackRequest,
        correlation_id: uuid.UUID,
        machine: AuthenticatedWorkflowService,
        evidence_dimensions: dict[str, tuple[FailureStage, ProviderInvocation]],
    ) -> AiCallbackOutcome:
        attempt = authorized.attempt
        operation = authorized.operation
        service_request = authorized.service_request
        credential = authorized.credential
        evidence = command.evidence
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
        if evidence.adapter_version != attempt.adapter_version:
            return self._guard(
                authorized,
                reservation,
                409,
                "FROZEN_OPERATION_MISMATCH",
                "The callback evidence does not match the frozen attempt intent.",
            )

        policy = select_active_failure_policy(session, authorized.database_now)
        stage, invocation = evidence_dimensions[evidence.failure_code]
        retry_after_seconds = getattr(evidence, "retry_after_seconds", None)
        provider_retry_after_at = (
            authorized.database_now + timedelta(seconds=retry_after_seconds)
            if retry_after_seconds is not None
            else None
        )
        try:
            assessment = assess_failure(
                FailureAssessmentInput(
                    operation_kind=OperationKind.AI_INTERPRETATION,
                    failure_code=FailureCode(evidence.failure_code),
                    failure_stage=stage,
                    provider_invocation=invocation,
                    customer_side_effect=CustomerSideEffect.NOT_APPLICABLE,
                    attempt_number=attempt.attempt_number,
                    assessed_at=authorized.database_now,
                    attempt_started_at=attempt.started_at,
                    provider_retry_after_at=provider_retry_after_at,
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

        is_retryable = assessment.recovery_disposition is RecoveryDisposition.RETRY_SAME_OPERATION
        resulting_state = "RetryableFailure" if is_retryable else "TerminalFailure"
        completed_at = authorized.database_now
        evidence_hash = canonical_command_hash(evidence)
        attempt.state = resulting_state
        attempt.version += 1
        attempt.completed_at = completed_at
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
        attempt.reconciliation_deadline = None
        attempt.sanitized_evidence_reference = f"callback-command:{reservation.command_id}"
        attempt.sanitized_evidence_hash = evidence_hash
        attempt.terminal_reason = (
            assessment.terminal_reason.value if assessment.terminal_reason is not None else None
        )
        attempt.assessed_at = assessment.assessed_at
        credential.state = "Consumed"
        credential.consumed_at = completed_at
        operation.version += 1
        operation.safe_outcome_summary = {
            "integration_attempt_id": str(attempt.id),
            "attempt_state": resulting_state,
            "failure_code": evidence.failure_code,
            "failure_policy_id": str(assessment.policy.policy_id),
        }
        service_request.version += 1
        service_request.status = resulting_state
        service_request.current_queue = "FailedRetryRequired" if is_retryable else None
        service_request.recovery_target = "TriagePending" if is_retryable else None
        service_request.recovery_attempt_id = attempt.id
        service_request.failure_summary_code = evidence.failure_code
        service_request.terminal_at = None if is_retryable else completed_at
        session.flush()

        safe_evidence = {
            "service_request_id": str(service_request.id),
            "logical_operation_id": str(operation.id),
            "integration_attempt_id": str(attempt.id),
            "attempt_state": resulting_state,
            "service_request_status": resulting_state,
            "service_request_queue": service_request.current_queue,
            "failure_code": evidence.failure_code,
            "recovery_disposition": assessment.recovery_disposition.value,
            "attempt_number": attempt.attempt_number,
            "maximum_attempts": assessment.maximum_attempts,
            "remaining_attempts": assessment.remaining_attempts,
            "next_eligible_at": (
                assessment.next_eligible_at.isoformat()
                if assessment.next_eligible_at is not None
                else None
            ),
            "completed_at": completed_at.isoformat(),
        }
        event_suffix = "retryable_failure" if is_retryable else "terminal_failure"
        write_audit_and_optional_outbox(
            session,
            AuditSpec(
                event_name=f"integration_attempt.{event_suffix}",
                aggregate_type="IntegrationAttempt",
                aggregate_id=attempt.id,
                aggregate_version=attempt.version,
                actor_type="WorkflowService",
                actor_reference_id=machine.machine_identity_id,
                outcome=resulting_state,
                correlation_id=correlation_id,
                causation_id=reservation.command_id,
                command_id=reservation.command_id,
                reason_codes=(evidence.failure_code,),
                safe_metadata=safe_evidence,
            ),
            OutboxSpec(
                event_type=f"integration_attempt.{event_suffix}",
                payload=safe_evidence,
            ),
        )
        completed = authorized.idempotency.complete(
            reservation,
            200,
            {
                "result": safe_evidence,
                "versions": {
                    "service_request": service_request.version,
                    "logical_operation": operation.version,
                    "integration_attempt": attempt.version,
                },
            },
            callback_authorization=CallbackAuthorizationMetadata(
                callback_credential_id=credential.id,
                callback_credential_version=credential.credential_version,
            ),
        )
        return AiCallbackOutcome(
            logical_http_status=200,
            command_id=completed.command_id,
            safe_snapshot=deepcopy(completed.safe_response_snapshot),
            is_replay=False,
        )

    @staticmethod
    def _guard(
        authorized: AuthorizedCallbackCommand,
        reservation: NewCommandReservation,
        status: int,
        code: str,
        message: str,
        *,
        current_versions: dict[str, int] | None = None,
    ) -> AiCallbackOutcome:
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
        return AiCallbackOutcome(
            logical_http_status=status,
            command_id=completed.command_id,
            safe_snapshot=deepcopy(completed.safe_response_snapshot),
            is_replay=False,
        )

    @staticmethod
    def _replay(replay: CompletedCommandReplay) -> AiCallbackOutcome:
        return AiCallbackOutcome(
            logical_http_status=replay.logical_http_status,
            command_id=replay.command_id,
            safe_snapshot=deepcopy(replay.safe_response_snapshot),
            is_replay=True,
        )
