"""Manager/administrator terminal disposition for retryable work."""

import hashlib
import uuid
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from ai_operations_automation.auth.models import AuthenticatedHuman
from ai_operations_automation.command_idempotency.models import (
    CommandIdempotencyScope,
    CompletedCommandReplay,
    NewCommandReservation,
)
from ai_operations_automation.command_idempotency.service import CommandIdempotencyService
from ai_operations_automation.db.models.ai_execution import IntegrationAttempt, LogicalOperation
from ai_operations_automation.db.models.intake import ServiceRequest
from ai_operations_automation.db.models.proposal import ApprovalDecision, ProposedAction
from ai_operations_automation.event_writing import (
    AuditSpec,
    OutboxSpec,
    write_audit_and_optional_outbox,
)
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.terminal_failure.models import MarkTerminalFailureRequest

ROUTE_TEMPLATE = "/api/v1/service-requests/{request_id}/commands/mark-terminal-failure"


@dataclass(frozen=True, slots=True)
class MarkTerminalFailureOutcome:
    logical_http_status: int
    command_id: uuid.UUID
    safe_snapshot: dict[str, Any]
    is_replay: bool


class MarkTerminalFailureService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def execute(
        self,
        *,
        request_id: uuid.UUID,
        command: MarkTerminalFailureRequest,
        raw_idempotency_key: str,
        canonical_body_hash: str,
        correlation_id: uuid.UUID,
        actor: AuthenticatedHuman,
    ) -> MarkTerminalFailureOutcome:
        try:
            with self.session_factory() as session:
                with session.begin():
                    idempotency = CommandIdempotencyService(session)
                    resolution = idempotency.reserve(
                        CommandIdempotencyScope(
                            actor_class="HumanActor",
                            actor_id=actor.actor_id,
                            command_intent="MarkTerminalFailure",
                            route_template=ROUTE_TEMPLATE,
                            target_type="ServiceRequest",
                            target_id=request_id,
                        ),
                        raw_idempotency_key,
                        canonical_body_hash,
                        correlation_id,
                    )
                    if isinstance(resolution, CompletedCommandReplay):
                        return self._replay(resolution)
                    return self._execute_new(
                        session=session,
                        idempotency=idempotency,
                        reservation=resolution,
                        request_id=request_id,
                        command=command,
                        correlation_id=correlation_id,
                        actor=actor,
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
        request_id: uuid.UUID,
        command: MarkTerminalFailureRequest,
        correlation_id: uuid.UUID,
        actor: AuthenticatedHuman,
    ) -> MarkTerminalFailureOutcome:
        service_request = session.scalar(
            select(ServiceRequest).where(ServiceRequest.id == request_id).with_for_update()
        )
        if service_request is None:
            return self._guard(
                idempotency,
                reservation,
                404,
                "RESOURCE_NOT_FOUND",
                "The requested resource was not found.",
            )
        if service_request.version != command.expected_versions.service_request:
            return self._guard(
                idempotency,
                reservation,
                409,
                "CONCURRENCY_CONFLICT",
                "The resource version does not match the current version.",
                current_versions={"service_request": service_request.version},
            )
        failed = session.scalar(
            select(IntegrationAttempt)
            .where(IntegrationAttempt.id == command.command.failed_attempt_id)
            .with_for_update()
        )
        if failed is None or failed.service_request_id != service_request.id:
            return self._guard(
                idempotency,
                reservation,
                404,
                "ATTEMPT_NOT_FOUND",
                "The requested attempt was not found.",
            )
        operation = session.scalar(
            select(LogicalOperation)
            .where(LogicalOperation.id == failed.logical_operation_id)
            .with_for_update()
        )
        proposal = None
        approval = None
        if failed.operation_kind == "OutboundAction":
            proposal = session.scalar(
                select(ProposedAction)
                .where(ProposedAction.id == failed.proposed_action_id)
                .with_for_update()
            )
            approval = session.scalar(
                select(ApprovalDecision)
                .where(ApprovalDecision.id == failed.approval_decision_id)
                .with_for_update()
            )
        if (
            service_request.status != "RetryableFailure"
            or service_request.recovery_attempt_id != failed.id
            or failed.state != "RetryableFailure"
            or failed.recovery_disposition not in ("RetrySameOperation", "ReviseProposal")
            or failed.sanitized_error_code is None
        ):
            return self._guard(
                idempotency,
                reservation,
                409,
                "INVALID_STATE_TRANSITION",
                "The retryable work cannot be terminalized from its current state.",
            )
        if failed.operation_kind == "OutboundAction" and not (
            operation is not None
            and proposal is not None
            and approval is not None
            and command.expected_versions.proposed_action == proposal.version
            and service_request.current_proposed_action_id == proposal.id
            and proposal.state == "RetryableExecutionFailure"
            and proposal.logical_operation_id == operation.id
            and failed.proposed_action_id == proposal.id
            and failed.proposal_number == proposal.proposal_number
            and failed.proposal_payload_digest == proposal.payload_digest
            and failed.approval_decision_id == approval.id == proposal.current_approval_id
            and operation.succeeded_attempt_id is None
        ):
            return self._guard(
                idempotency,
                reservation,
                409,
                "INVALID_STATE_TRANSITION",
                "The retryable outbound work cannot be terminalized from its current state.",
            )
        database_now = session.scalar(select(func.now()))
        if database_now is None or database_now.tzinfo is None or database_now.utcoffset() is None:
            raise RuntimeError("database time must be timezone-aware")
        disposition_code = (
            "MANAGER_TERMINAL_DISPOSITION"
            if actor.role == "ManagerApprover"
            else "ADMINISTRATOR_TERMINAL_DISPOSITION"
        )
        rationale_hash = hashlib.sha256(command.command.rationale.encode("utf-8")).hexdigest()
        service_request.version += 1
        service_request.status = "TerminalFailure"
        service_request.current_queue = None
        service_request.recovery_target = None
        service_request.terminal_at = database_now
        if proposal is not None:
            proposal.state = "TerminalExecutionFailure"
            proposal.terminal_at = database_now
            proposal.version += 1
        session.flush()
        safe_evidence = {
            "service_request_id": str(service_request.id),
            "failed_attempt_id": str(failed.id),
            "service_request_status": "TerminalFailure",
            "service_request_queue": None,
            "failure_code": failed.sanitized_error_code,
            "terminal_disposition_code": disposition_code,
            "terminal_at": database_now.isoformat(),
            "rationale_hash": rationale_hash,
        }
        if proposal is not None:
            safe_evidence.update(
                {
                    "proposed_action_id": str(proposal.id),
                    "proposal_state": proposal.state,
                }
            )
        write_audit_and_optional_outbox(
            session,
            AuditSpec(
                event_name="service_request.terminal_failure",
                aggregate_type="ServiceRequest",
                aggregate_id=service_request.id,
                aggregate_version=service_request.version,
                actor_type="HumanActor",
                actor_reference_id=actor.actor_id,
                outcome="TerminalFailure",
                correlation_id=correlation_id,
                causation_id=reservation.command_id,
                command_id=reservation.command_id,
                reason_codes=(disposition_code, failed.sanitized_error_code),
                safe_metadata=safe_evidence,
            ),
            OutboxSpec(event_type="service_request.terminal_failure", payload=safe_evidence),
        )
        versions = {"service_request": service_request.version}
        if proposal is not None:
            versions["proposed_action"] = proposal.version
        completed = idempotency.complete(
            reservation,
            200,
            {
                "result": {
                    key: value for key, value in safe_evidence.items() if key != "rationale_hash"
                },
                "versions": versions,
            },
        )
        return MarkTerminalFailureOutcome(
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
    ) -> MarkTerminalFailureOutcome:
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
        return MarkTerminalFailureOutcome(
            logical_http_status=status,
            command_id=completed.command_id,
            safe_snapshot=deepcopy(completed.safe_response_snapshot),
            is_replay=False,
        )

    @staticmethod
    def _replay(replay: CompletedCommandReplay) -> MarkTerminalFailureOutcome:
        return MarkTerminalFailureOutcome(
            logical_http_status=replay.logical_http_status,
            command_id=replay.command_id,
            safe_snapshot=deepcopy(replay.safe_response_snapshot),
            is_replay=True,
        )
