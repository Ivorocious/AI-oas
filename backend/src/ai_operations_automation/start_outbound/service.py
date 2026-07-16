"""Atomic start of one approved simulated outbound operation."""

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

from ai_operations_automation.command_idempotency.models import (
    CommandIdempotencyScope,
    CompletedCommandReplay,
    NewCommandReservation,
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
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService
from ai_operations_automation.outbound_identity import (
    OUTBOUND_KEY_SCOPE,
    outbound_binding_matches,
    outbound_key_digest,
    outbound_key_reference,
)
from ai_operations_automation.start_ai.credentials import callback_credential_hash
from ai_operations_automation.start_outbound.models import StartOutboundRequest

ROUTE_TEMPLATE = "/api/v1/proposed-actions/{action_id}/commands/start-outbound"
MOCK_ADAPTER_NAME = "MockOutboundAdapter"
MOCK_ADAPTER_VERSION = "1.0"
CALLBACK_AUTHORIZATION_SECONDS = 30 * 60
OPAQUE_CREDENTIAL = re.compile(r"^[A-Za-z0-9_-]{43,256}$")


@dataclass(frozen=True, slots=True)
class StartOutboundOutcome:
    logical_http_status: int
    command_id: uuid.UUID
    safe_snapshot: dict[str, Any]
    is_replay: bool
    callback_plaintext: str | None = None


class StartOutboundService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        credential_generator: Callable[[], str],
    ) -> None:
        self.session_factory = session_factory
        self.credential_generator = credential_generator

    def execute(
        self,
        *,
        action_id: uuid.UUID,
        command: StartOutboundRequest,
        raw_idempotency_key: str,
        canonical_body_hash: str,
        correlation_id: uuid.UUID,
        machine: AuthenticatedWorkflowService,
    ) -> StartOutboundOutcome:
        plaintext: str | None = None
        try:
            with self.session_factory() as session:
                with session.begin():
                    idem = CommandIdempotencyService(session)
                    resolution = idem.reserve(
                        CommandIdempotencyScope(
                            actor_class="MachineService",
                            actor_id=machine.machine_identity_id,
                            command_intent="StartOutboundAction",
                            route_template=ROUTE_TEMPLATE,
                            target_type="ProposedAction",
                            target_id=action_id,
                        ),
                        raw_idempotency_key,
                        canonical_body_hash,
                        correlation_id,
                    )
                    if isinstance(resolution, CompletedCommandReplay):
                        return self._replay(resolution)
                    outcome, plaintext = self._execute_new(
                        session, idem, resolution, action_id, command, correlation_id, machine
                    )
            if plaintext is None:
                return outcome
            return StartOutboundOutcome(
                outcome.logical_http_status,
                outcome.command_id,
                outcome.safe_snapshot,
                False,
                plaintext,
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
        idem: CommandIdempotencyService,
        reservation: NewCommandReservation,
        action_id: uuid.UUID,
        command: StartOutboundRequest,
        correlation_id: uuid.UUID,
        machine: AuthenticatedWorkflowService,
    ) -> tuple[StartOutboundOutcome, str | None]:
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
        if operation is None or request is None:
            raise RuntimeError("outbound ownership graph is incomplete")
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
        approval = session.scalar(
            select(ApprovalDecision)
            .where(ApprovalDecision.id == proposal.current_approval_id)
            .with_for_update()
        )
        siblings = session.scalars(
            select(IntegrationAttempt)
            .where(IntegrationAttempt.logical_operation_id == operation.id)
            .order_by(IntegrationAttempt.attempt_number)
            .with_for_update()
        ).all()
        graph_valid = (
            request.current_proposed_action_id == proposal.id
            and request.id == proposal.service_request_id == operation.service_request_id
            and proposal.logical_operation_id == operation.id
            and proposal.proposal_series_id == operation.proposal_series_id
            and operation.operation_kind == "OutboundAction"
            and proposal.state == "Approved"
            and request.status == "ActionPendingExecution"
            and approval is not None
            and approval.id == proposal.current_approval_id
            and approval.proposed_action_id == proposal.id
            and approval.proposal_number == proposal.proposal_number
            and approval.payload_digest == proposal.payload_digest
            and approval.decision == "Approved"
        )
        if not graph_valid:
            return self._guard(
                idem,
                reservation,
                409,
                "INVALID_STATE_TRANSITION",
                "The approved proposal is not eligible for outbound execution.",
            ), None
        if operation.succeeded_attempt_id is not None or any(
            row.state in ("Pending", "Running", "Succeeded") for row in siblings
        ):
            return self._guard(
                idem,
                reservation,
                409,
                "OUTBOUND_OPERATION_BLOCKED",
                "The outbound operation already has active or successful evidence.",
            ), None
        next_number = max((row.attempt_number for row in siblings), default=0) + 1
        if next_number > 3:
            return self._guard(
                idem,
                reservation,
                409,
                "RETRY_BUDGET_EXHAUSTED",
                "The outbound operation attempt budget is exhausted.",
            ), None
        if operation.outbound_key_scope is None and operation.outbound_key_digest is None:
            operation.outbound_key_scope = OUTBOUND_KEY_SCOPE
            operation.outbound_key_digest = outbound_key_digest(operation.id)
            operation.version += 1
        elif not outbound_binding_matches(
            operation.id, operation.outbound_key_scope, operation.outbound_key_digest
        ):
            raise RuntimeError("stable outbound identity is inconsistent")
        session.flush()
        database_now = session.scalar(select(func.now()))
        if database_now is None or database_now.tzinfo is None:
            raise RuntimeError("database time must be timezone-aware")
        deadline = database_now + timedelta(seconds=CALLBACK_AUTHORIZATION_SECONDS)
        plaintext = self.credential_generator()
        if not isinstance(plaintext, str) or OPAQUE_CREDENTIAL.fullmatch(plaintext) is None:
            raise IntakeError(
                503, "DEPENDENCY_UNAVAILABLE", "A required dependency is unavailable.", True
            )
        attempt_id, credential_id = uuid.uuid4(), uuid.uuid4()
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
            adapter_name=MOCK_ADAPTER_NAME,
            adapter_version=MOCK_ADAPTER_VERSION,
            assigned_workflow_service=machine.stable_service_id,
            workflow_environment=machine.environment,
            callback_authorization_deadline=deadline,
        )
        credential = AttemptCallbackCredential(
            id=credential_id,
            integration_attempt_id=attempt_id,
            operation_kind="OutboundAction",
            workflow_service_identity=machine.stable_service_id,
            workflow_environment=machine.environment,
            credential_version=1,
            credential_hash=callback_credential_hash(plaintext),
            state="Active",
            expires_at=deadline,
        )
        proposal.state = "PendingExecution"
        proposal.version += 1
        session.add(attempt)
        session.flush()
        session.add(credential)
        session.flush()
        safe = {
            "service_request_id": str(request.id),
            "proposed_action_id": str(proposal.id),
            "proposal_series_id": str(proposal.proposal_series_id),
            "proposal_number": proposal.proposal_number,
            "proposal_payload_digest": proposal.payload_digest,
            "approval_decision_id": str(approval.id),
            "logical_operation_id": str(operation.id),
            "integration_attempt_id": str(attempt.id),
            "attempt_number": attempt.attempt_number,
            "attempt_state": "Pending",
            "proposal_state": proposal.state,
            "service_request_status": request.status,
            "adapter_name": MOCK_ADAPTER_NAME,
            "adapter_version": MOCK_ADAPTER_VERSION,
            "stable_outbound_key_scope": OUTBOUND_KEY_SCOPE,
            "stable_outbound_key_reference": outbound_key_reference(operation.id),
        }
        for event_name, aggregate_type, aggregate_id, aggregate_version in (
            ("integration_attempt.created", "IntegrationAttempt", attempt.id, attempt.version),
            ("proposed_action.execution_started", "ProposedAction", proposal.id, proposal.version),
        ):
            write_audit_and_optional_outbox(
                session,
                AuditSpec(
                    event_name=event_name,
                    aggregate_type=aggregate_type,
                    aggregate_id=aggregate_id,
                    aggregate_version=aggregate_version,
                    actor_type="WorkflowService",
                    actor_reference_id=machine.machine_identity_id,
                    outcome="Pending",
                    correlation_id=correlation_id,
                    causation_id=reservation.command_id,
                    command_id=reservation.command_id,
                    safe_metadata=safe,
                ),
                OutboxSpec(event_type=event_name, payload=safe),
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
        completed = idem.complete(
            reservation,
            202,
            snapshot,
            SecretDeliveryMetadata(
                callback_credential_id=credential_id,
                callback_credential_version=1,
                callback_credential_expires_at=deadline,
            ),
        )
        return StartOutboundOutcome(
            202, completed.command_id, deepcopy(completed.safe_response_snapshot), False
        ), plaintext

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
        return StartOutboundOutcome(
            status, completed.command_id, deepcopy(completed.safe_response_snapshot), False
        )

    @staticmethod
    def _replay(replay: CompletedCommandReplay) -> StartOutboundOutcome:
        return StartOutboundOutcome(
            replay.logical_http_status,
            replay.command_id,
            deepcopy(replay.safe_response_snapshot),
            True,
        )
