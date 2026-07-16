"""Atomic known-not-applied retry of one simulated outbound operation."""

import re
import uuid
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from ai_operations_automation.auth.models import AuthenticatedHuman
from ai_operations_automation.command_idempotency.models import (
    CommandIdempotencyScope,
    CompletedCommandReplay,
    SecretDeliveryMetadata,
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
    FailurePolicyError,
    FailurePolicyIdentity,
    is_retry_eligible,
    require_policy_identity,
)
from ai_operations_automation.failure_recovery.repository import select_active_failure_policy
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService
from ai_operations_automation.outbound_identity import (
    OUTBOUND_KEY_SCOPE,
    outbound_binding_matches,
    outbound_key_reference,
)
from ai_operations_automation.retry_outbound.models import RetryOutboundRequest
from ai_operations_automation.start_ai.credentials import callback_credential_hash
from ai_operations_automation.start_outbound.service import CALLBACK_AUTHORIZATION_SECONDS

ROUTE_TEMPLATE = "/api/v1/proposed-actions/{action_id}/commands/retry-outbound"
OPAQUE_CREDENTIAL = re.compile(r"^[A-Za-z0-9_-]{43,256}$")
RetryAuthority = AuthenticatedHuman | AuthenticatedWorkflowService


@dataclass(frozen=True, slots=True)
class RetryOutboundOutcome:
    logical_http_status: int
    command_id: uuid.UUID
    safe_snapshot: dict[str, Any]
    is_replay: bool
    callback_plaintext: str | None = None
    secret_was_issued: bool = False


class RetryOutboundService:
    def __init__(
        self, session_factory: sessionmaker[Session], credential_generator: Callable[[], str]
    ) -> None:
        self.session_factory = session_factory
        self.credential_generator = credential_generator

    def execute(
        self,
        *,
        action_id,
        command,
        raw_idempotency_key,
        canonical_body_hash,
        correlation_id,
        authority,
    ) -> RetryOutboundOutcome:
        plaintext = None
        try:
            with self.session_factory() as session:
                with session.begin():
                    idem = CommandIdempotencyService(session)
                    resolution = idem.reserve(
                        self._scope(authority, action_id),
                        raw_idempotency_key,
                        canonical_body_hash,
                        correlation_id,
                    )
                    if isinstance(resolution, CompletedCommandReplay):
                        return self._replay(resolution)
                    outcome, plaintext = self._execute_new(
                        session, idem, resolution, action_id, command, correlation_id, authority
                    )
            if plaintext is None:
                return outcome
            return RetryOutboundOutcome(
                outcome.logical_http_status,
                outcome.command_id,
                outcome.safe_snapshot,
                False,
                plaintext,
                True,
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
        session,
        idem,
        reservation,
        action_id,
        command: RetryOutboundRequest,
        correlation_id,
        authority,
    ):
        proposal = session.scalar(
            select(ProposedAction).where(ProposedAction.id == action_id).with_for_update()
        )
        if proposal is None:
            return self._guard(
                idem,
                reservation,
                404,
                "RESOURCE_NOT_FOUND",
                "The requested resource was not found.",
            ), None
        operation = session.scalar(
            select(LogicalOperation)
            .where(LogicalOperation.id == proposal.logical_operation_id)
            .with_for_update()
        )
        request = session.scalar(
            select(ServiceRequest)
            .where(ServiceRequest.id == proposal.service_request_id)
            .with_for_update()
        )
        failed = session.scalar(
            select(IntegrationAttempt)
            .where(IntegrationAttempt.id == command.command.failed_attempt_id)
            .with_for_update()
        )
        approval = session.scalar(
            select(ApprovalDecision)
            .where(ApprovalDecision.id == proposal.current_approval_id)
            .with_for_update()
        )
        if operation is None or request is None:
            raise RuntimeError("outbound retry ownership graph is incomplete")
        if (
            request.version != command.expected_versions.service_request
            or proposal.version != command.expected_versions.proposed_action
        ):
            return self._guard(
                idem,
                reservation,
                409,
                "CONCURRENCY_CONFLICT",
                "The resource version does not match the current version.",
                current_versions={
                    "service_request": request.version,
                    "proposed_action": proposal.version,
                },
            ), None
        if failed is None or failed.logical_operation_id != operation.id:
            return self._guard(
                idem, reservation, 404, "ATTEMPT_NOT_FOUND", "The requested attempt was not found."
            ), None
        siblings = session.scalars(
            select(IntegrationAttempt)
            .where(IntegrationAttempt.logical_operation_id == operation.id)
            .order_by(IntegrationAttempt.attempt_number)
            .with_for_update()
        ).all()
        now = session.scalar(select(func.now()))
        policy = select_active_failure_policy(session, now)
        expected = command.command.expected_failure_policy
        try:
            require_policy_identity(
                FailurePolicyIdentity(
                    policy_id=expected.policy_id,
                    policy_key=policy.policy_key,
                    semantic_version=expected.semantic_version,
                    revision=expected.revision,
                    content_digest=expected.content_digest,
                ),
                policy.identity,
            )
        except FailurePolicyError:
            return self._guard(
                idem,
                reservation,
                409,
                "FAILURE_POLICY_VERSION_CONFLICT",
                "The expected recovery policy is no longer current.",
            ), None
        exact = (
            request.current_proposed_action_id == proposal.id
            and request.status == "RetryableFailure"
            and request.recovery_target == "ActionPendingExecution"
            and request.recovery_attempt_id == failed.id
            and proposal.state == "RetryableExecutionFailure"
            and proposal.logical_operation_id == operation.id
            and proposal.proposal_series_id == operation.proposal_series_id
            and failed.service_request_id == request.id
            and failed.proposed_action_id == proposal.id
            and failed.proposal_series_id == proposal.proposal_series_id
            and failed.proposal_number == proposal.proposal_number
            and failed.proposal_payload_digest == proposal.payload_digest
            and failed.approval_decision_id == proposal.current_approval_id
            and failed.state == "RetryableFailure"
            and failed.recovery_disposition == "RetrySameOperation"
            and failed.customer_side_effect == "KnownNotApplied"
            and failed.next_eligible_at is not None
            and failed.remaining_attempts is not None
            and failed.remaining_attempts > 0
            and approval is not None
            and approval.decision == "Approved"
            and approval.proposed_action_id == proposal.id
            and approval.proposal_number == proposal.proposal_number
            and approval.payload_digest == proposal.payload_digest
            and failed.failure_policy_id == expected.policy_id
            and failed.failure_policy_semantic_version == expected.semantic_version
            and failed.failure_policy_revision == expected.revision
            and failed.failure_policy_digest == expected.content_digest
            and outbound_binding_matches(
                operation.id, operation.outbound_key_scope, operation.outbound_key_digest
            )
            and failed.stable_outbound_key_scope == operation.outbound_key_scope
            and failed.stable_outbound_key_digest == operation.outbound_key_digest
        )
        if not exact:
            return self._guard(
                idem,
                reservation,
                409,
                "RECOVERY_DISPOSITION_CONFLICT",
                "The failed outbound work is not eligible for retry.",
            ), None
        if isinstance(authority, AuthenticatedWorkflowService) and (
            authority.stable_service_id != failed.assigned_workflow_service
            or authority.environment != failed.workflow_environment
        ):
            return self._guard(
                idem, reservation, 403, "FORBIDDEN", "The requested operation is not permitted."
            ), None
        if not is_retry_eligible(now, failed.next_eligible_at):
            return self._guard(
                idem,
                reservation,
                409,
                "RETRY_NOT_YET_ELIGIBLE",
                "The retry eligibility time has not been reached.",
            ), None
        if (
            operation.succeeded_attempt_id is not None
            or any(row.state in ("Pending", "Running", "Succeeded") for row in siblings)
            or siblings[-1].id != failed.id
            or failed.attempt_number >= 3
        ):
            return self._guard(
                idem,
                reservation,
                409,
                "RETRY_NOT_ALLOWED",
                "A new attempt cannot be created for this operation.",
            ), None
        generated = self.credential_generator()
        if not isinstance(generated, str) or OPAQUE_CREDENTIAL.fullmatch(generated) is None:
            raise IntakeError(
                503, "DEPENDENCY_UNAVAILABLE", "A required dependency is unavailable.", True
            )
        attempt_id, credential_id = uuid.uuid4(), uuid.uuid4()
        deadline = now + timedelta(seconds=CALLBACK_AUTHORIZATION_SECONDS)
        next_number = failed.attempt_number + 1
        attempt = IntegrationAttempt(
            id=attempt_id,
            logical_operation_id=operation.id,
            service_request_id=request.id,
            operation_kind="OutboundAction",
            proposal_series_id=proposal.proposal_series_id,
            proposed_action_id=proposal.id,
            proposal_number=proposal.proposal_number,
            proposal_payload_digest=proposal.payload_digest,
            approval_decision_id=approval.id,
            stable_outbound_key_scope=operation.outbound_key_scope,
            stable_outbound_key_digest=operation.outbound_key_digest,
            attempt_number=next_number,
            state="Pending",
            version=1,
            adapter_name=failed.adapter_name,
            adapter_version=failed.adapter_version,
            assigned_workflow_service=failed.assigned_workflow_service,
            workflow_environment=failed.workflow_environment,
            callback_authorization_deadline=deadline,
        )
        credential = AttemptCallbackCredential(
            id=credential_id,
            integration_attempt_id=attempt_id,
            operation_kind="OutboundAction",
            workflow_service_identity=failed.assigned_workflow_service,
            workflow_environment=failed.workflow_environment,
            credential_version=1,
            credential_hash=callback_credential_hash(generated),
            state="Active",
            expires_at=deadline,
        )
        previous_queue = request.current_queue
        proposal.state = "PendingExecution"
        proposal.version += 1
        request.status = "ActionPendingExecution"
        request.current_queue = (
            "StandardRequests"
            if request.priority in ("Low", "Normal")
            else "PriorityRequests"
            if request.priority == "High"
            else "HumanReview"
        )
        request.recovery_target = request.recovery_attempt_id = request.failure_summary_code = None
        request.version += 1
        operation.version += 1
        operation.safe_outcome_summary = {
            "retry_attempt_id": str(attempt_id),
            "attempt_number": next_number,
        }
        session.add(attempt)
        session.flush()
        session.add(credential)
        session.flush()
        actor_type, actor_id = self._actor(authority)
        safe = {
            "service_request_id": str(request.id),
            "proposed_action_id": str(proposal.id),
            "proposal_series_id": str(proposal.proposal_series_id),
            "proposal_number": proposal.proposal_number,
            "proposal_payload_digest": proposal.payload_digest,
            "approval_decision_id": str(approval.id),
            "logical_operation_id": str(operation.id),
            "failed_attempt_id": str(failed.id),
            "integration_attempt_id": str(attempt.id),
            "attempt_number": next_number,
            "attempt_state": "Pending",
            "proposal_state": proposal.state,
            "service_request_status": request.status,
            "service_request_queue": request.current_queue,
            "previous_service_request_queue": previous_queue,
            "stable_outbound_key_scope": OUTBOUND_KEY_SCOPE,
            "stable_outbound_key_reference": outbound_key_reference(operation.id),
        }
        lifecycle_events = [
            (
                "integration_attempt.retry_requested",
                "IntegrationAttempt",
                failed.id,
                failed.version,
            ),
            ("integration_attempt.created", "IntegrationAttempt", attempt.id, 1),
            ("proposed_action.execution_retried", "ProposedAction", proposal.id, proposal.version),
        ]
        if previous_queue != request.current_queue:
            lifecycle_events.append(
                ("service_request.queue_changed", "ServiceRequest", request.id, request.version)
            )
        for event, aggregate_type, aggregate_id, version in lifecycle_events:
            write_audit_and_optional_outbox(
                session,
                AuditSpec(
                    event_name=event,
                    aggregate_type=aggregate_type,
                    aggregate_id=aggregate_id,
                    aggregate_version=version,
                    actor_type=actor_type,
                    actor_reference_id=actor_id,
                    outcome="Pending",
                    correlation_id=correlation_id,
                    causation_id=reservation.command_id,
                    command_id=reservation.command_id,
                    reason_codes=(failed.sanitized_error_code,),
                    safe_metadata=safe,
                ),
                OutboxSpec(event_type=event, payload=safe),
            )
        snapshot = {
            "result": {
                **safe,
                "callback_credential_id": str(credential_id),
                "callback_credential_version": 1,
                "callback_credential_expires_at": deadline.isoformat(),
            },
            "versions": {
                "service_request": request.version,
                "proposed_action": proposal.version,
                "logical_operation": operation.version,
                "integration_attempt": 1,
            },
        }
        deliver = isinstance(authority, AuthenticatedWorkflowService)
        completed = idem.complete(
            reservation,
            202,
            snapshot,
            SecretDeliveryMetadata(
                callback_credential_id=credential_id,
                callback_credential_version=1,
                callback_credential_expires_at=deadline,
            )
            if deliver
            else None,
        )
        return RetryOutboundOutcome(
            202, completed.command_id, deepcopy(completed.safe_response_snapshot), False
        ), generated if deliver else None

    @staticmethod
    def _scope(authority, action_id):
        return CommandIdempotencyScope(
            actor_class="HumanActor"
            if isinstance(authority, AuthenticatedHuman)
            else "MachineService",
            actor_id=authority.actor_id
            if isinstance(authority, AuthenticatedHuman)
            else authority.machine_identity_id,
            command_intent="RetryOutboundAction",
            route_template=ROUTE_TEMPLATE,
            target_type="ProposedAction",
            target_id=action_id,
        )

    @staticmethod
    def _actor(authority):
        return (
            ("HumanActor", authority.actor_id)
            if isinstance(authority, AuthenticatedHuman)
            else ("WorkflowService", authority.machine_identity_id)
        )

    @staticmethod
    def _guard(idem, reservation, status, code, message, *, current_versions=None):
        completed = idem.complete(
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
        return RetryOutboundOutcome(
            status, completed.command_id, deepcopy(completed.safe_response_snapshot), False
        )

    @staticmethod
    def _replay(replay):
        return RetryOutboundOutcome(
            replay.logical_http_status,
            replay.command_id,
            deepcopy(replay.safe_response_snapshot),
            True,
            secret_was_issued=replay.credential_delivery == "AlreadyIssued",
        )
