"""Atomic hash-only callback-credential replacement."""

import re
import uuid
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from ai_operations_automation.command_idempotency.models import (
    CallbackAuthorizationMetadata,
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
from ai_operations_automation.event_writing import AuditSpec, write_audit_and_optional_outbox
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService
from ai_operations_automation.start_ai.credentials import callback_credential_hash

ROUTE_TEMPLATE = "/api/v1/integration-attempts/{attempt_id}/commands/replace-callback-credential"
OPAQUE_CREDENTIAL = re.compile(r"^[A-Za-z0-9_-]{43,256}$")


@dataclass(frozen=True, slots=True)
class ReplaceCredentialOutcome:
    logical_http_status: int
    command_id: uuid.UUID
    safe_snapshot: dict[str, Any]
    is_replay: bool
    callback_plaintext: str | None = None


class ReplaceCallbackCredentialService:
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
        attempt_id: uuid.UUID,
        expected_attempt_version: int,
        expected_credential_version: int,
        raw_idempotency_key: str,
        canonical_body_hash: str,
        correlation_id: uuid.UUID,
        machine: AuthenticatedWorkflowService,
    ) -> ReplaceCredentialOutcome:
        plaintext: str | None = None
        try:
            with self.session_factory() as session:
                with session.begin():
                    idempotency = CommandIdempotencyService(session)
                    resolution = idempotency.reserve(
                        CommandIdempotencyScope(
                            actor_class="MachineService",
                            actor_id=machine.machine_identity_id,
                            command_intent="ReplaceCallbackCredential",
                            route_template=ROUTE_TEMPLATE,
                            target_type="IntegrationAttempt",
                            target_id=attempt_id,
                        ),
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
                        attempt_id=attempt_id,
                        expected_attempt_version=expected_attempt_version,
                        expected_credential_version=expected_credential_version,
                        correlation_id=correlation_id,
                        machine=machine,
                    )
            if plaintext is None:
                return outcome
            return ReplaceCredentialOutcome(
                logical_http_status=outcome.logical_http_status,
                command_id=outcome.command_id,
                safe_snapshot=outcome.safe_snapshot,
                is_replay=False,
                callback_plaintext=plaintext,
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
        expected_credential_version: int,
        correlation_id: uuid.UUID,
        machine: AuthenticatedWorkflowService,
    ) -> tuple[ReplaceCredentialOutcome, str | None]:
        attempt = session.scalar(
            select(IntegrationAttempt).where(IntegrationAttempt.id == attempt_id).with_for_update()
        )
        if (
            attempt is None
            or attempt.assigned_workflow_service != machine.stable_service_id
            or attempt.workflow_environment != machine.environment
        ):
            return self._guard(
                idempotency,
                reservation,
                404,
                "ATTEMPT_NOT_FOUND",
                "The requested attempt was not found.",
            ), None
        if attempt.version != expected_attempt_version:
            return self._guard(
                idempotency,
                reservation,
                409,
                "CONCURRENCY_CONFLICT",
                "The resource version does not match the current version.",
                current_versions={"integration_attempt": attempt.version},
            ), None
        operation = session.scalar(
            select(LogicalOperation)
            .where(LogicalOperation.id == attempt.logical_operation_id)
            .with_for_update()
        )
        if (
            operation is None
            or operation.operation_kind != attempt.operation_kind
            or operation.service_request_id != attempt.service_request_id
        ):
            raise RuntimeError("attempt operation graph is inconsistent")
        if attempt.state not in ("Pending", "Running"):
            return self._guard(
                idempotency,
                reservation,
                409,
                "CALLBACK_CREDENTIAL_REPLACEMENT_NOT_ALLOWED",
                "The callback credential cannot be replaced for this attempt.",
            ), None
        database_now = session.scalar(select(func.now()))
        if database_now is None or database_now.tzinfo is None or database_now.utcoffset() is None:
            raise RuntimeError("database time must be timezone-aware")
        if attempt.callback_authorization_deadline <= database_now:
            return self._guard(
                idempotency,
                reservation,
                409,
                "CALLBACK_CREDENTIAL_REPLACEMENT_NOT_ALLOWED",
                "The callback credential cannot be replaced for this attempt.",
            ), None
        credentials = session.scalars(
            select(AttemptCallbackCredential)
            .where(AttemptCallbackCredential.integration_attempt_id == attempt.id)
            .order_by(AttemptCallbackCredential.credential_version)
            .with_for_update()
        ).all()
        active = [row for row in credentials if row.state == "Active"]
        if len(active) != 1:
            raise RuntimeError("attempt callback credential graph is inconsistent")
        current = active[0]
        versions = [row.credential_version for row in credentials]
        if (
            current.credential_version != max(versions)
            or current.credential_version != expected_credential_version
        ):
            return self._guard(
                idempotency,
                reservation,
                409,
                "CALLBACK_CREDENTIAL_VERSION_CONFLICT",
                "The callback credential version changed.",
                current_versions={
                    "integration_attempt": attempt.version,
                    "callback_credential": current.credential_version,
                },
            ), None
        if (
            current.operation_kind != attempt.operation_kind
            or current.workflow_service_identity != machine.stable_service_id
            or current.workflow_environment != machine.environment
            or current.expires_at != attempt.callback_authorization_deadline
            or current.consumed_at is not None
            or current.replaced_at is not None
            or current.revoked_at is not None
            or current.replacement_credential_id is not None
        ):
            raise RuntimeError("active callback credential scope is inconsistent")

        plaintext = self.credential_generator()
        if not isinstance(plaintext, str) or OPAQUE_CREDENTIAL.fullmatch(plaintext) is None:
            raise IntakeError(
                503, "DEPENDENCY_UNAVAILABLE", "A required dependency is unavailable.", True
            )
        next_id = uuid.uuid4()
        next_version = current.credential_version + 1
        current.state = "Replaced"
        current.replaced_at = database_now
        current.replacement_credential_id = next_id
        replacement = AttemptCallbackCredential(
            id=next_id,
            integration_attempt_id=attempt.id,
            operation_kind=attempt.operation_kind,
            workflow_service_identity=attempt.assigned_workflow_service,
            workflow_environment=attempt.workflow_environment,
            credential_version=next_version,
            credential_hash=callback_credential_hash(plaintext),
            state="Active",
            expires_at=attempt.callback_authorization_deadline,
        )
        session.add(replacement)
        session.flush()
        write_audit_and_optional_outbox(
            session,
            AuditSpec(
                event_name="integration_attempt.callback_credential_replaced",
                aggregate_type="IntegrationAttempt",
                aggregate_id=attempt.id,
                aggregate_version=attempt.version,
                actor_type="WorkflowService",
                actor_reference_id=machine.machine_identity_id,
                outcome="Replaced",
                correlation_id=correlation_id,
                causation_id=reservation.command_id,
                command_id=reservation.command_id,
                reason_codes=("CALLBACK_CREDENTIAL_REPLACED",),
                safe_metadata={
                    "integration_attempt_id": str(attempt.id),
                    "operation_kind": attempt.operation_kind,
                    "old_credential_id": str(current.id),
                    "old_credential_version": current.credential_version,
                    "new_credential_id": str(next_id),
                    "new_credential_version": next_version,
                },
            ),
        )
        snapshot = {
            "result": {
                "integration_attempt_id": str(attempt.id),
                "attempt_state": attempt.state,
                "callback_credential_id": str(next_id),
                "callback_credential_version": next_version,
                "callback_credential_expires_at": (
                    attempt.callback_authorization_deadline.isoformat()
                ),
            },
            "versions": {
                "integration_attempt": attempt.version,
                "callback_credential": next_version,
            },
        }
        completed = idempotency.complete(
            reservation,
            200,
            snapshot,
            SecretDeliveryMetadata(
                callback_credential_id=next_id,
                callback_credential_version=next_version,
                callback_credential_expires_at=attempt.callback_authorization_deadline,
            ),
            CallbackAuthorizationMetadata(
                callback_credential_id=current.id,
                callback_credential_version=current.credential_version,
            ),
        )
        return ReplaceCredentialOutcome(
            logical_http_status=200,
            command_id=completed.command_id,
            safe_snapshot=deepcopy(completed.safe_response_snapshot),
            is_replay=False,
        ), plaintext

    @staticmethod
    def _guard(
        idempotency: CommandIdempotencyService,
        reservation: NewCommandReservation,
        status: int,
        code: str,
        message: str,
        *,
        current_versions: dict[str, int] | None = None,
    ) -> ReplaceCredentialOutcome:
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
        return ReplaceCredentialOutcome(
            logical_http_status=status,
            command_id=completed.command_id,
            safe_snapshot=deepcopy(completed.safe_response_snapshot),
            is_replay=False,
        )

    @staticmethod
    def _replay(replay: CompletedCommandReplay) -> ReplaceCredentialOutcome:
        return ReplaceCredentialOutcome(
            logical_http_status=replay.logical_http_status,
            command_id=replay.command_id,
            safe_snapshot=deepcopy(replay.safe_response_snapshot),
            is_replay=True,
        )
