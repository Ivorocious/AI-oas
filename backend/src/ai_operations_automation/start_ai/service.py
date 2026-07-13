"""Atomic Start AI interpretation command transaction."""

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
from ai_operations_automation.config import Settings
from ai_operations_automation.db.models.ai_execution import (
    AttemptCallbackCredential,
    IntegrationAttempt,
    LogicalOperation,
)
from ai_operations_automation.db.models.evidence import AuditEvent, OutboxMessage
from ai_operations_automation.db.models.intake import ServiceRequest
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService
from ai_operations_automation.start_ai.credentials import callback_credential_hash
from ai_operations_automation.start_ai.hashing import ai_configuration_hash, ai_input_hash

ROUTE_TEMPLATE = "/api/v1/service-requests/{request_id}/commands/start-ai-interpretation"
OPAQUE_CREDENTIAL = re.compile(r"^[A-Za-z0-9_-]{43,256}$")


@dataclass(frozen=True, slots=True)
class StartAiCommandOutcome:
    logical_http_status: int
    command_id: uuid.UUID
    safe_snapshot: dict[str, Any]
    is_replay: bool
    callback_plaintext: str | None = None


class StartAiInterpretationService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        credential_generator: Callable[[], str],
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.credential_generator = credential_generator

    def execute(
        self,
        *,
        request_id: uuid.UUID,
        expected_request_version: int,
        raw_idempotency_key: str,
        canonical_body_hash: str,
        correlation_id: uuid.UUID,
        machine: AuthenticatedWorkflowService,
    ) -> StartAiCommandOutcome:
        plaintext: str | None = None
        outcome: StartAiCommandOutcome | None = None
        try:
            with self.session_factory() as session:
                with session.begin():
                    idempotency = CommandIdempotencyService(session)
                    resolution = idempotency.reserve(
                        CommandIdempotencyScope(
                            actor_class="MachineService",
                            actor_id=machine.machine_identity_id,
                            command_intent="StartAiInterpretation",
                            route_template=ROUTE_TEMPLATE,
                            target_type="ServiceRequest",
                            target_id=request_id,
                        ),
                        raw_idempotency_key,
                        canonical_body_hash,
                        correlation_id,
                    )
                    if isinstance(resolution, CompletedCommandReplay):
                        outcome = self._replay_outcome(resolution)
                    else:
                        outcome, plaintext = self._execute_new(
                            session=session,
                            idempotency=idempotency,
                            reservation=resolution,
                            request_id=request_id,
                            expected_request_version=expected_request_version,
                            correlation_id=correlation_id,
                            machine=machine,
                        )
            if outcome is None:
                raise RuntimeError("command transaction produced no outcome")
            if plaintext is not None:
                return StartAiCommandOutcome(
                    logical_http_status=outcome.logical_http_status,
                    command_id=outcome.command_id,
                    safe_snapshot=outcome.safe_snapshot,
                    is_replay=False,
                    callback_plaintext=plaintext,
                )
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
        request_id: uuid.UUID,
        expected_request_version: int,
        correlation_id: uuid.UUID,
        machine: AuthenticatedWorkflowService,
    ) -> tuple[StartAiCommandOutcome, str | None]:
        service_request = session.scalar(
            select(ServiceRequest).where(ServiceRequest.id == request_id).with_for_update()
        )
        if service_request is None:
            return self._complete_guard(
                idempotency,
                reservation,
                404,
                "RESOURCE_NOT_FOUND",
                "The requested resource was not found.",
            ), None
        if service_request.version != expected_request_version:
            return self._complete_guard(
                idempotency,
                reservation,
                409,
                "CONCURRENCY_CONFLICT",
                "The resource version does not match the current version.",
                current_versions={"service_request": service_request.version},
            ), None
        if service_request.status != "TriagePending":
            return self._complete_guard(
                idempotency,
                reservation,
                409,
                "INVALID_STATE_TRANSITION",
                "The command is not valid for the current resource state.",
            ), None

        input_digest = ai_input_hash(service_request)
        configuration_digest = ai_configuration_hash(self.settings)
        operation = session.scalar(
            select(LogicalOperation)
            .where(
                LogicalOperation.service_request_id == request_id,
                LogicalOperation.input_hash == input_digest,
                LogicalOperation.configuration_hash == configuration_digest,
            )
            .with_for_update()
        )
        if operation is not None:
            guard = self._existing_operation_guard(session, operation)
            if guard is None:
                raise RuntimeError("inconsistent existing logical operation")
            code, message = guard
            return self._complete_guard(idempotency, reservation, 409, code, message), None

        database_now = session.scalar(select(func.now()))
        if database_now is None or database_now.tzinfo is None or database_now.utcoffset() is None:
            raise RuntimeError("database time must be timezone-aware")
        callback_deadline = database_now + timedelta(
            seconds=self.settings.ai_callback_authorization_seconds
        )
        try:
            plaintext = self.credential_generator()
        except Exception as exc:
            raise IntakeError(
                503,
                "DEPENDENCY_UNAVAILABLE",
                "A required dependency is unavailable.",
                True,
            ) from exc
        if not isinstance(plaintext, str) or OPAQUE_CREDENTIAL.fullmatch(plaintext) is None:
            raise IntakeError(
                503,
                "DEPENDENCY_UNAVAILABLE",
                "A required dependency is unavailable.",
                True,
            )

        operation_id = uuid.uuid4()
        attempt_id = uuid.uuid4()
        credential_id = uuid.uuid4()
        request_version = service_request.version + 1
        operation = LogicalOperation(
            id=operation_id,
            service_request_id=request_id,
            operation_kind="AIInterpretation",
            input_hash=input_digest,
            configuration_hash=configuration_digest,
            prompt_version=self.settings.ai_interpretation_prompt_version,
            result_schema_version=self.settings.ai_interpretation_result_schema_version,
            provider_name=self.settings.ai_provider_name,
            model_name=self.settings.ai_model_name,
            adapter_name=self.settings.ai_adapter_name,
            adapter_version=self.settings.ai_adapter_version,
            version=1,
        )
        attempt = IntegrationAttempt(
            id=attempt_id,
            logical_operation_id=operation_id,
            service_request_id=request_id,
            operation_kind="AIInterpretation",
            attempt_number=1,
            state="Pending",
            version=1,
            adapter_name=self.settings.ai_adapter_name,
            adapter_version=self.settings.ai_adapter_version,
            assigned_workflow_service=machine.stable_service_id,
            workflow_environment=machine.environment,
            callback_authorization_deadline=callback_deadline,
        )
        credential = AttemptCallbackCredential(
            id=credential_id,
            integration_attempt_id=attempt_id,
            operation_kind="AIInterpretation",
            workflow_service_identity=machine.stable_service_id,
            workflow_environment=machine.environment,
            credential_version=1,
            credential_hash=callback_credential_hash(plaintext),
            state="Active",
            expires_at=callback_deadline,
        )
        service_request.version = request_version
        session.add(operation)
        session.flush()
        session.add(attempt)
        session.flush()
        session.add(credential)
        session.flush()

        request_audit = AuditEvent(
            id=uuid.uuid4(),
            schema_version="1.0",
            event_name="service_request.ai_interpretation_started",
            aggregate_type="ServiceRequest",
            aggregate_id=request_id,
            aggregate_version=request_version,
            actor_type="WorkflowService",
            actor_reference_id=machine.machine_identity_id,
            outcome="Started",
            correlation_id=correlation_id,
            causation_id=reservation.command_id,
            command_id=reservation.command_id,
            reason_codes=[],
            safe_metadata={
                "logical_operation_id": str(operation_id),
                "integration_attempt_id": str(attempt_id),
                "attempt_number": 1,
                "prompt_version": self.settings.ai_interpretation_prompt_version,
                "result_schema_version": self.settings.ai_interpretation_result_schema_version,
                "adapter_name": self.settings.ai_adapter_name,
                "adapter_version": self.settings.ai_adapter_version,
            },
        )
        attempt_audit = AuditEvent(
            id=uuid.uuid4(),
            schema_version="1.0",
            event_name="integration_attempt.created",
            aggregate_type="IntegrationAttempt",
            aggregate_id=attempt_id,
            aggregate_version=1,
            actor_type="WorkflowService",
            actor_reference_id=machine.machine_identity_id,
            outcome="Pending",
            correlation_id=correlation_id,
            causation_id=reservation.command_id,
            command_id=reservation.command_id,
            reason_codes=[],
            safe_metadata={
                "service_request_id": str(request_id),
                "logical_operation_id": str(operation_id),
                "integration_attempt_id": str(attempt_id),
                "attempt_number": 1,
                "operation_kind": "AIInterpretation",
                "state": "Pending",
                "service_request_version": request_version,
                "logical_operation_version": 1,
                "integration_attempt_version": 1,
            },
        )
        session.add_all((request_audit, attempt_audit))
        session.flush()
        session.add(
            OutboxMessage(
                id=uuid.uuid4(),
                event_type="integration_attempt.created",
                schema_version="1.0",
                aggregate_type="IntegrationAttempt",
                aggregate_id=attempt_id,
                aggregate_version=1,
                audit_event_id=attempt_audit.id,
                correlation_id=correlation_id,
                causation_id=reservation.command_id,
                payload={
                    "integration_attempt_id": str(attempt_id),
                    "attempt_number": 1,
                    "operation_kind": "AIInterpretation",
                    "service_request_id": str(request_id),
                    "logical_operation_id": str(operation_id),
                    "adapter_name": self.settings.ai_adapter_name,
                    "adapter_version": self.settings.ai_adapter_version,
                    "state": "Pending",
                    "assigned_workflow_service": machine.stable_service_id,
                    "workflow_environment": machine.environment,
                },
                publication_state="Pending",
            )
        )
        session.flush()

        snapshot = {
            "result": {
                "service_request_id": str(request_id),
                "logical_operation_id": str(operation_id),
                "integration_attempt_id": str(attempt_id),
                "attempt_number": 1,
                "attempt_state": "Pending",
                "callback_credential_id": str(credential_id),
                "callback_credential_version": 1,
                "callback_credential_expires_at": callback_deadline.isoformat(),
            },
            "versions": {
                "service_request": request_version,
                "logical_operation": 1,
                "integration_attempt": 1,
            },
        }
        completed = idempotency.complete(
            reservation,
            202,
            snapshot,
            SecretDeliveryMetadata(
                callback_credential_id=credential_id,
                callback_credential_version=1,
                callback_credential_expires_at=callback_deadline,
            ),
        )
        session.flush()
        return StartAiCommandOutcome(
            logical_http_status=202,
            command_id=completed.command_id,
            safe_snapshot=deepcopy(completed.safe_response_snapshot),
            is_replay=False,
        ), plaintext

    @staticmethod
    def _existing_operation_guard(
        session: Session, operation: LogicalOperation
    ) -> tuple[str, str] | None:
        attempts = session.scalars(
            select(IntegrationAttempt)
            .where(IntegrationAttempt.logical_operation_id == operation.id)
            .order_by(IntegrationAttempt.attempt_number)
            .with_for_update()
        ).all()
        if not attempts:
            return None
        succeeded = [attempt for attempt in attempts if attempt.state == "Succeeded"]
        active = [attempt for attempt in attempts if attempt.state in ("Pending", "Running")]
        if operation.succeeded_attempt_id is not None and not any(
            attempt.id == operation.succeeded_attempt_id and attempt.state == "Succeeded"
            for attempt in attempts
        ):
            return None
        if succeeded and active:
            return None
        if active:
            return (
                "ACTIVE_ATTEMPT_EXISTS",
                "An active attempt already exists for this operation.",
            )
        if operation.succeeded_attempt_id is not None or succeeded:
            return (
                "LOGICAL_OPERATION_ALREADY_SUCCEEDED",
                "The logical operation has already succeeded.",
            )
        if all(attempt.state in ("RetryableFailure", "TerminalFailure") for attempt in attempts):
            return ("RETRY_NOT_ALLOWED", "A separate retry command is required.")
        return None

    @staticmethod
    def _complete_guard(
        idempotency: CommandIdempotencyService,
        reservation: NewCommandReservation,
        status: int,
        code: str,
        message: str,
        *,
        current_versions: dict[str, int] | None = None,
    ) -> StartAiCommandOutcome:
        error = {
            "schema_version": "1.0",
            "code": code,
            "message": message,
            "retryable": False,
            "current_versions": current_versions or {},
            "details": [],
        }
        completed = idempotency.complete(reservation, status, {"error": error})
        return StartAiCommandOutcome(
            logical_http_status=status,
            command_id=completed.command_id,
            safe_snapshot=deepcopy(completed.safe_response_snapshot),
            is_replay=False,
        )

    @staticmethod
    def _replay_outcome(replay: CompletedCommandReplay) -> StartAiCommandOutcome:
        return StartAiCommandOutcome(
            logical_http_status=replay.logical_http_status,
            command_id=replay.command_id,
            safe_snapshot=deepcopy(replay.safe_response_snapshot),
            is_replay=True,
        )
