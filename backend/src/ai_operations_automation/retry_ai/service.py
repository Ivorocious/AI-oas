"""Atomic bounded retry of one failed AI logical operation."""

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
from ai_operations_automation.db.models.intake import ServiceRequest
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
from ai_operations_automation.failure_recovery.repository import (
    select_active_failure_policy,
)
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService
from ai_operations_automation.retry_ai.models import RetryAiRequest
from ai_operations_automation.start_ai.credentials import callback_credential_hash
from ai_operations_automation.start_ai.hashing import ai_input_hash

ROUTE_TEMPLATE = "/api/v1/service-requests/{request_id}/commands/retry-ai"
OPAQUE_CREDENTIAL = re.compile(r"^[A-Za-z0-9_-]{43,256}$")
RetryAuthority = AuthenticatedHuman | AuthenticatedWorkflowService


@dataclass(frozen=True, slots=True)
class RetryAiOutcome:
    logical_http_status: int
    command_id: uuid.UUID
    safe_snapshot: dict[str, Any]
    is_replay: bool
    callback_plaintext: str | None = None
    secret_was_issued: bool = False


class RetryAiService:
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
        command: RetryAiRequest,
        raw_idempotency_key: str,
        canonical_body_hash: str,
        correlation_id: uuid.UUID,
        authority: RetryAuthority,
    ) -> RetryAiOutcome:
        plaintext: str | None = None
        try:
            with self.session_factory() as session:
                with session.begin():
                    idempotency = CommandIdempotencyService(session)
                    resolution = idempotency.reserve(
                        self._scope(authority, request_id),
                        raw_idempotency_key,
                        canonical_body_hash,
                        correlation_id,
                    )
                    if isinstance(resolution, CompletedCommandReplay):
                        return self._replay(resolution)
                    outcome, plaintext = self._execute_new(
                        session=session,
                        idempotency=idempotency,
                        reservation=resolution,
                        request_id=request_id,
                        command=command,
                        correlation_id=correlation_id,
                        authority=authority,
                    )
            if plaintext is None:
                return outcome
            return RetryAiOutcome(
                logical_http_status=outcome.logical_http_status,
                command_id=outcome.command_id,
                safe_snapshot=outcome.safe_snapshot,
                is_replay=False,
                callback_plaintext=plaintext,
                secret_was_issued=True,
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
        command: RetryAiRequest,
        correlation_id: uuid.UUID,
        authority: RetryAuthority,
    ) -> tuple[RetryAiOutcome, str | None]:
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
            ), None
        if service_request.version != command.expected_versions.service_request:
            return self._guard(
                idempotency,
                reservation,
                409,
                "CONCURRENCY_CONFLICT",
                "The resource version does not match the current version.",
                current_versions={"service_request": service_request.version},
            ), None
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
            ), None
        operation = session.scalar(
            select(LogicalOperation)
            .where(LogicalOperation.id == failed.logical_operation_id)
            .with_for_update()
        )
        if operation is None:
            raise RuntimeError("failed attempt has no logical operation")
        siblings = session.scalars(
            select(IntegrationAttempt)
            .where(IntegrationAttempt.logical_operation_id == operation.id)
            .order_by(IntegrationAttempt.attempt_number)
            .with_for_update()
        ).all()
        database_now = session.scalar(select(func.now()))
        if database_now is None or database_now.tzinfo is None or database_now.utcoffset() is None:
            raise RuntimeError("database time must be timezone-aware")
        policy = select_active_failure_policy(session, database_now)
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
                idempotency,
                reservation,
                409,
                "FAILURE_POLICY_VERSION_CONFLICT",
                "The expected recovery policy is no longer current.",
            ), None

        if (
            service_request.status != "RetryableFailure"
            or service_request.recovery_target != "TriagePending"
            or service_request.recovery_attempt_id != failed.id
            or failed.state != "RetryableFailure"
            or failed.operation_kind != "AIInterpretation"
            or operation.operation_kind != "AIInterpretation"
            or failed.recovery_disposition != "RetrySameOperation"
            or failed.remaining_attempts is None
            or failed.remaining_attempts <= 0
            or failed.next_eligible_at is None
            or failed.failure_policy_id != expected.policy_id
            or failed.failure_policy_semantic_version != expected.semantic_version
            or failed.failure_policy_revision != expected.revision
            or failed.failure_policy_digest != expected.content_digest
        ):
            return self._guard(
                idempotency,
                reservation,
                409,
                "RECOVERY_DISPOSITION_CONFLICT",
                "The failed work is not eligible for an AI retry.",
            ), None
        if isinstance(authority, AuthenticatedWorkflowService) and (
            authority.stable_service_id != failed.assigned_workflow_service
            or authority.environment != failed.workflow_environment
        ):
            return self._guard(
                idempotency,
                reservation,
                403,
                "FORBIDDEN",
                "The requested operation is not permitted.",
            ), None
        if not is_retry_eligible(database_now, failed.next_eligible_at):
            return self._guard(
                idempotency,
                reservation,
                409,
                "RETRY_NOT_YET_ELIGIBLE",
                "The retry eligibility time has not been reached.",
            ), None
        if ai_input_hash(service_request) != operation.input_hash:
            return self._guard(
                idempotency,
                reservation,
                409,
                "RECOVERY_DISPOSITION_CONFLICT",
                "The request input no longer matches the failed operation.",
            ), None
        if (
            operation.succeeded_attempt_id is not None
            or any(item.state in ("Pending", "Running", "Succeeded") for item in siblings)
            or not siblings
            or siblings[-1].id != failed.id
            or siblings[-1].attempt_number != failed.attempt_number
            or failed.maximum_attempts is None
            or failed.attempt_number >= failed.maximum_attempts
        ):
            return self._guard(
                idempotency,
                reservation,
                409,
                "RETRY_NOT_ALLOWED",
                "A new attempt cannot be created for this operation.",
            ), None

        try:
            generated = self.credential_generator()
        except Exception as exc:
            raise IntakeError(
                503, "DEPENDENCY_UNAVAILABLE", "A required dependency is unavailable.", True
            ) from exc
        if not isinstance(generated, str) or OPAQUE_CREDENTIAL.fullmatch(generated) is None:
            raise IntakeError(
                503, "DEPENDENCY_UNAVAILABLE", "A required dependency is unavailable.", True
            )
        new_attempt_id = uuid.uuid4()
        credential_id = uuid.uuid4()
        next_number = failed.attempt_number + 1
        deadline = database_now + timedelta(seconds=self.settings.ai_callback_authorization_seconds)
        attempt = IntegrationAttempt(
            id=new_attempt_id,
            logical_operation_id=operation.id,
            service_request_id=service_request.id,
            operation_kind="AIInterpretation",
            attempt_number=next_number,
            state="Pending",
            version=1,
            adapter_name=operation.adapter_name,
            adapter_version=operation.adapter_version,
            assigned_workflow_service=failed.assigned_workflow_service,
            workflow_environment=failed.workflow_environment,
            callback_authorization_deadline=deadline,
        )
        credential = AttemptCallbackCredential(
            id=credential_id,
            integration_attempt_id=new_attempt_id,
            operation_kind="AIInterpretation",
            workflow_service_identity=failed.assigned_workflow_service,
            workflow_environment=failed.workflow_environment,
            credential_version=1,
            credential_hash=callback_credential_hash(generated),
            state="Active",
            expires_at=deadline,
        )
        service_request.version += 1
        service_request.status = "TriagePending"
        service_request.current_queue = None
        service_request.recovery_target = None
        service_request.recovery_attempt_id = None
        service_request.failure_summary_code = None
        service_request.terminal_at = None
        operation.version += 1
        operation.safe_outcome_summary = {
            "retry_attempt_id": str(new_attempt_id),
            "attempt_number": next_number,
        }
        session.add(attempt)
        session.flush()
        session.add(credential)
        session.flush()

        actor_type, actor_id = self._audit_actor(authority)
        safe_evidence = {
            "service_request_id": str(service_request.id),
            "logical_operation_id": str(operation.id),
            "failed_attempt_id": str(failed.id),
            "integration_attempt_id": str(new_attempt_id),
            "attempt_number": next_number,
            "attempt_state": "Pending",
            "service_request_status": "TriagePending",
            "failure_policy_id": str(policy.id),
        }
        write_audit_and_optional_outbox(
            session,
            AuditSpec(
                event_name="integration_attempt.retry_created",
                aggregate_type="IntegrationAttempt",
                aggregate_id=new_attempt_id,
                aggregate_version=1,
                actor_type=actor_type,
                actor_reference_id=actor_id,
                outcome="Pending",
                correlation_id=correlation_id,
                causation_id=reservation.command_id,
                command_id=reservation.command_id,
                reason_codes=(failed.sanitized_error_code or "AI_RETRY_ELIGIBLE",),
                safe_metadata=safe_evidence,
            ),
            OutboxSpec(event_type="integration_attempt.retry_created", payload=safe_evidence),
        )
        result = {
            **safe_evidence,
            "callback_credential_id": str(credential_id),
            "callback_credential_version": 1,
            "callback_credential_expires_at": deadline.isoformat(),
        }
        snapshot = {
            "result": result,
            "versions": {
                "service_request": service_request.version,
                "logical_operation": operation.version,
                "integration_attempt": 1,
            },
        }
        secret_delivery = None
        deliver_plaintext = isinstance(authority, AuthenticatedWorkflowService)
        if deliver_plaintext:
            secret_delivery = SecretDeliveryMetadata(
                callback_credential_id=credential_id,
                callback_credential_version=1,
                callback_credential_expires_at=deadline,
            )
        completed = idempotency.complete(
            reservation,
            202,
            snapshot,
            secret_delivery=secret_delivery,
        )
        return RetryAiOutcome(
            logical_http_status=202,
            command_id=completed.command_id,
            safe_snapshot=deepcopy(completed.safe_response_snapshot),
            is_replay=False,
        ), generated if deliver_plaintext else None

    @staticmethod
    def _scope(authority: RetryAuthority, request_id: uuid.UUID) -> CommandIdempotencyScope:
        if isinstance(authority, AuthenticatedHuman):
            actor_class = "HumanActor"
            actor_id = authority.actor_id
        else:
            actor_class = "MachineService"
            actor_id = authority.machine_identity_id
        return CommandIdempotencyScope(
            actor_class=actor_class,
            actor_id=actor_id,
            command_intent="RetryAiInterpretation",
            route_template=ROUTE_TEMPLATE,
            target_type="ServiceRequest",
            target_id=request_id,
        )

    @staticmethod
    def _audit_actor(authority: RetryAuthority) -> tuple[str, uuid.UUID]:
        if isinstance(authority, AuthenticatedHuman):
            return "HumanActor", authority.actor_id
        return "WorkflowService", authority.machine_identity_id

    @staticmethod
    def _guard(
        idempotency: CommandIdempotencyService,
        reservation: NewCommandReservation,
        status: int,
        code: str,
        message: str,
        *,
        current_versions: dict[str, int] | None = None,
    ) -> RetryAiOutcome:
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
        return RetryAiOutcome(
            logical_http_status=status,
            command_id=completed.command_id,
            safe_snapshot=deepcopy(completed.safe_response_snapshot),
            is_replay=False,
        )

    @staticmethod
    def _replay(replay: CompletedCommandReplay) -> RetryAiOutcome:
        return RetryAiOutcome(
            logical_http_status=replay.logical_http_status,
            command_id=replay.command_id,
            safe_snapshot=deepcopy(replay.safe_response_snapshot),
            is_replay=True,
            secret_was_issued=replay.credential_delivery == "AlreadyIssued",
        )
