"""Atomic claim/start transition for one assigned AI integration attempt."""

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
from ai_operations_automation.db.models.evidence import AuditEvent, OutboxMessage
from ai_operations_automation.db.models.intake import ServiceRequest
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService
from ai_operations_automation.start_ai.hashing import ai_input_hash

ROUTE_TEMPLATE = "/api/v1/integration-attempts/{attempt_id}/commands/start"


@dataclass(frozen=True, slots=True)
class AttemptStartOutcome:
    logical_http_status: int
    command_id: uuid.UUID
    safe_snapshot: dict[str, Any]
    is_replay: bool


class AttemptStartService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def execute(
        self,
        *,
        attempt_id: uuid.UUID,
        expected_attempt_version: int,
        raw_idempotency_key: str,
        canonical_body_hash: str,
        correlation_id: uuid.UUID,
        machine: AuthenticatedWorkflowService,
    ) -> AttemptStartOutcome:
        outcome: AttemptStartOutcome | None = None
        try:
            with self.session_factory() as session:
                with session.begin():
                    idempotency = CommandIdempotencyService(session)
                    resolution = idempotency.reserve(
                        CommandIdempotencyScope(
                            actor_class="MachineService",
                            actor_id=machine.machine_identity_id,
                            command_intent="StartIntegrationAttempt",
                            route_template=ROUTE_TEMPLATE,
                            target_type="IntegrationAttempt",
                            target_id=attempt_id,
                        ),
                        raw_idempotency_key,
                        canonical_body_hash,
                        correlation_id,
                    )
                    if isinstance(resolution, CompletedCommandReplay):
                        outcome = self._replay_outcome(resolution)
                    else:
                        outcome = self._execute_new(
                            session=session,
                            idempotency=idempotency,
                            reservation=resolution,
                            attempt_id=attempt_id,
                            expected_attempt_version=expected_attempt_version,
                            correlation_id=correlation_id,
                            machine=machine,
                        )
            if outcome is None:
                raise RuntimeError("attempt-start transaction produced no outcome")
            return outcome
        except IntakeError:
            raise
        except OperationalError as exc:
            raise IntakeError(
                503,
                "DEPENDENCY_UNAVAILABLE",
                "A required dependency is unavailable.",
                True,
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
        machine: AuthenticatedWorkflowService,
    ) -> AttemptStartOutcome:
        attempt = session.scalar(
            select(IntegrationAttempt).where(IntegrationAttempt.id == attempt_id).with_for_update()
        )
        if (
            attempt is None
            or attempt.assigned_workflow_service != machine.stable_service_id
            or attempt.workflow_environment != machine.environment
        ):
            return self._complete_guard(
                idempotency,
                reservation,
                404,
                "ATTEMPT_NOT_FOUND",
                "The requested attempt was not found.",
            )
        if attempt.version != expected_attempt_version:
            return self._complete_guard(
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
        if operation is None:
            raise RuntimeError("attempt has no logical operation")
        service_request = session.scalar(
            select(ServiceRequest)
            .where(ServiceRequest.id == attempt.service_request_id)
            .with_for_update()
        )
        if service_request is None:
            raise RuntimeError("attempt has no owning service request")
        siblings = session.scalars(
            select(IntegrationAttempt)
            .where(IntegrationAttempt.logical_operation_id == operation.id)
            .order_by(IntegrationAttempt.attempt_number)
            .with_for_update()
        ).all()
        credential_rows = session.execute(
            select(
                AttemptCallbackCredential.id,
                AttemptCallbackCredential.integration_attempt_id,
                AttemptCallbackCredential.operation_kind,
                AttemptCallbackCredential.workflow_service_identity,
                AttemptCallbackCredential.workflow_environment,
                AttemptCallbackCredential.credential_version,
                AttemptCallbackCredential.state,
                AttemptCallbackCredential.expires_at,
                AttemptCallbackCredential.consumed_at,
                AttemptCallbackCredential.replaced_at,
                AttemptCallbackCredential.revoked_at,
                AttemptCallbackCredential.replacement_credential_id,
            )
            .where(AttemptCallbackCredential.integration_attempt_id == attempt.id)
            .with_for_update()
        ).all()
        database_now = session.scalar(select(func.now()))
        if database_now is None or database_now.tzinfo is None or database_now.utcoffset() is None:
            raise RuntimeError("database time must be timezone-aware")

        if attempt.state != "Pending":
            return self._complete_guard(
                idempotency,
                reservation,
                409,
                "INVALID_STATE_TRANSITION",
                "The command is not valid for the current attempt state.",
            )
        if (
            attempt.operation_kind != "AIInterpretation"
            or attempt.started_at is not None
            or attempt.completed_at is not None
            or attempt.result_hash is not None
            or attempt.sanitized_error_code is not None
            or operation.service_request_id != service_request.id
            or operation.service_request_id != attempt.service_request_id
        ):
            raise RuntimeError("attempt ownership or lifecycle structure is inconsistent")
        target_matches = [item for item in siblings if item.id == attempt.id]
        active_others = [
            item
            for item in siblings
            if item.id != attempt.id and item.state in ("Pending", "Running")
        ]
        succeeded = [item for item in siblings if item.state == "Succeeded"]
        if len(target_matches) != 1 or active_others:
            raise RuntimeError("logical operation has contradictory active attempts")
        if operation.succeeded_attempt_id is not None:
            valid_success = any(
                item.id == operation.succeeded_attempt_id and item.state == "Succeeded"
                for item in siblings
            )
            if not valid_success:
                raise RuntimeError("logical operation success reference is inconsistent")
            return self._complete_guard(
                idempotency,
                reservation,
                409,
                "LOGICAL_OPERATION_ALREADY_SUCCEEDED",
                "The logical operation has already succeeded.",
            )
        if succeeded:
            raise RuntimeError("successful sibling lacks a valid operation success reference")
        if (
            service_request.status != "TriagePending"
            or ai_input_hash(service_request) != operation.input_hash
        ):
            return self._complete_guard(
                idempotency,
                reservation,
                409,
                "INVALID_STATE_TRANSITION",
                "The attempt owner is no longer eligible for this command.",
            )

        if len(credential_rows) != 1:
            raise RuntimeError("attempt callback credential metadata is inconsistent")
        credential = credential_rows[0]
        if (
            credential.integration_attempt_id != attempt.id
            or credential.operation_kind != "AIInterpretation"
            or credential.workflow_service_identity != attempt.assigned_workflow_service
            or credential.workflow_environment != attempt.workflow_environment
            or credential.workflow_service_identity != machine.stable_service_id
            or credential.workflow_environment != machine.environment
            or credential.credential_version <= 0
            or credential.state != "Active"
            or credential.expires_at != attempt.callback_authorization_deadline
            or credential.consumed_at is not None
            or credential.replaced_at is not None
            or credential.revoked_at is not None
            or credential.replacement_credential_id is not None
        ):
            raise RuntimeError("attempt callback credential scope is inconsistent")
        if credential.expires_at <= database_now:
            return self._complete_guard(
                idempotency,
                reservation,
                409,
                "INVALID_STATE_TRANSITION",
                "The attempt callback authorization has expired.",
            )

        previous_state = attempt.state
        attempt.state = "Running"
        attempt.version = expected_attempt_version + 1
        attempt.started_at = database_now
        session.flush()

        audit = AuditEvent(
            id=uuid.uuid4(),
            schema_version="1.0",
            event_name="integration_attempt.started",
            aggregate_type="IntegrationAttempt",
            aggregate_id=attempt.id,
            aggregate_version=attempt.version,
            actor_type="WorkflowService",
            actor_reference_id=machine.machine_identity_id,
            outcome="Running",
            correlation_id=correlation_id,
            causation_id=reservation.command_id,
            command_id=reservation.command_id,
            reason_codes=[],
            safe_metadata={
                "service_request_id": str(service_request.id),
                "logical_operation_id": str(operation.id),
                "integration_attempt_id": str(attempt.id),
                "attempt_number": attempt.attempt_number,
                "operation_kind": attempt.operation_kind,
                "adapter_name": attempt.adapter_name,
                "adapter_version": attempt.adapter_version,
                "previous_state": previous_state,
                "new_state": attempt.state,
                "assigned_workflow_service": attempt.assigned_workflow_service,
                "workflow_environment": attempt.workflow_environment,
            },
        )
        session.add(audit)
        session.flush()
        session.add(
            OutboxMessage(
                id=uuid.uuid4(),
                event_type="integration_attempt.started",
                schema_version="1.0",
                aggregate_type="IntegrationAttempt",
                aggregate_id=attempt.id,
                aggregate_version=attempt.version,
                audit_event_id=audit.id,
                correlation_id=correlation_id,
                causation_id=reservation.command_id,
                payload={
                    "integration_attempt_id": str(attempt.id),
                    "attempt_number": attempt.attempt_number,
                    "operation_kind": attempt.operation_kind,
                    "service_request_id": str(service_request.id),
                    "logical_operation_id": str(operation.id),
                    "adapter_name": attempt.adapter_name,
                    "adapter_version": attempt.adapter_version,
                    "state": attempt.state,
                    "assigned_workflow_service": attempt.assigned_workflow_service,
                    "workflow_environment": attempt.workflow_environment,
                },
                publication_state="Pending",
            )
        )
        session.flush()
        snapshot = {
            "result": {
                "service_request_id": str(service_request.id),
                "logical_operation_id": str(operation.id),
                "integration_attempt_id": str(attempt.id),
                "attempt_number": attempt.attempt_number,
                "operation_kind": attempt.operation_kind,
                "attempt_state": attempt.state,
                "started_at": database_now.isoformat(),
                "adapter_name": attempt.adapter_name,
                "adapter_version": attempt.adapter_version,
            },
            "versions": {"integration_attempt": attempt.version},
        }
        completed = idempotency.complete(reservation, 200, snapshot)
        session.flush()
        return AttemptStartOutcome(
            logical_http_status=200,
            command_id=completed.command_id,
            safe_snapshot=deepcopy(completed.safe_response_snapshot),
            is_replay=False,
        )

    @staticmethod
    def _complete_guard(
        idempotency: CommandIdempotencyService,
        reservation: NewCommandReservation,
        status: int,
        code: str,
        message: str,
        *,
        current_versions: dict[str, int] | None = None,
    ) -> AttemptStartOutcome:
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
        return AttemptStartOutcome(
            logical_http_status=status,
            command_id=completed.command_id,
            safe_snapshot=deepcopy(completed.safe_response_snapshot),
            is_replay=False,
        )

    @staticmethod
    def _replay_outcome(replay: CompletedCommandReplay) -> AttemptStartOutcome:
        return AttemptStartOutcome(
            logical_http_status=replay.logical_http_status,
            command_id=replay.command_id,
            safe_snapshot=deepcopy(replay.safe_response_snapshot),
            is_replay=True,
        )
