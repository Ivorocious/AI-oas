"""Replay-aware callback-command authorization inside one domain transaction."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, NoReturn

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.session import SessionTransactionOrigin

from ai_operations_automation.attempt_callback_auth.headers import callback_forbidden
from ai_operations_automation.attempt_callback_auth.verifier import matching_callback_credentials
from ai_operations_automation.command_idempotency.keys import command_key_digest
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
from ai_operations_automation.db.models.command_idempotency import CommandIdempotencyRecord
from ai_operations_automation.db.models.intake import ServiceRequest
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService

OperationKind = Literal["AIInterpretation", "OutboundAction"]


def _internal_error(message: str) -> NoReturn:
    raise RuntimeError(message)


@dataclass(frozen=True, slots=True)
class AuthorizedCallbackCommand:
    attempt: IntegrationAttempt
    operation: LogicalOperation
    service_request: ServiceRequest
    credential: AttemptCallbackCredential
    database_now: datetime
    idempotency: CommandIdempotencyService
    resolution: NewCommandReservation | CompletedCommandReplay


class CallbackCommandAuthorizer:
    """Prove Active first execution or exact Consumed-credential replay."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def authorize(
        self,
        *,
        attempt_id: uuid.UUID,
        machine: AuthenticatedWorkflowService,
        supplied_credential: str,
        raw_idempotency_key: str,
        canonical_body_hash: str,
        correlation_id: uuid.UUID,
        command_intent: str,
        route_template: str,
        expected_operation_kind: OperationKind,
    ) -> AuthorizedCallbackCommand:
        transaction = self.session.get_transaction()
        if (
            transaction is None
            or transaction.origin is not SessionTransactionOrigin.BEGIN
            or not transaction.is_active
        ):
            raise IntakeError(500, "INTERNAL_ERROR", "The request could not be completed safely.")

        attempt = self.session.scalar(
            select(IntegrationAttempt).where(IntegrationAttempt.id == attempt_id).with_for_update()
        )
        if (
            attempt is None
            or attempt.assigned_workflow_service != machine.stable_service_id
            or attempt.workflow_environment != machine.environment
        ):
            raise IntakeError(404, "ATTEMPT_NOT_FOUND", "The requested attempt was not found.")
        operation = self.session.scalar(
            select(LogicalOperation)
            .where(LogicalOperation.id == attempt.logical_operation_id)
            .with_for_update()
        )
        if operation is None:
            _internal_error("attempt has no logical operation")
        service_request = self.session.scalar(
            select(ServiceRequest)
            .where(ServiceRequest.id == attempt.service_request_id)
            .with_for_update()
        )
        if service_request is None:
            _internal_error("attempt has no owning request")
        siblings = self.session.scalars(
            select(IntegrationAttempt)
            .where(IntegrationAttempt.logical_operation_id == operation.id)
            .order_by(IntegrationAttempt.attempt_number)
            .with_for_update()
        ).all()
        credentials = self.session.scalars(
            select(AttemptCallbackCredential)
            .where(AttemptCallbackCredential.integration_attempt_id == attempt.id)
            .order_by(AttemptCallbackCredential.credential_version)
            .with_for_update()
        ).all()
        database_now = self.session.scalar(select(func.now()))
        if database_now is None or database_now.tzinfo is None or database_now.utcoffset() is None:
            _internal_error("database time must be timezone-aware")

        self._validate_graph(
            attempt=attempt,
            operation=operation,
            service_request=service_request,
            siblings=siblings,
            expected_operation_kind=expected_operation_kind,
        )
        matched = self._prove_credential(
            attempt=attempt,
            credentials=credentials,
            supplied_credential=supplied_credential,
            machine=machine,
            database_now=database_now,
            expected_operation_kind=expected_operation_kind,
        )

        scope = CommandIdempotencyScope(
            actor_class="MachineService",
            actor_id=machine.machine_identity_id,
            command_intent=command_intent,
            route_template=route_template,
            target_type="IntegrationAttempt",
            target_id=attempt_id,
        )
        idempotency = CommandIdempotencyService(self.session)
        if matched.state == "Consumed":
            existing = self._find_existing(scope, raw_idempotency_key)
            if (
                existing is None
                or existing.status != "Completed"
                or existing.callback_authorization_credential_id != matched.id
                or existing.callback_authorization_credential_version != matched.credential_version
            ):
                raise callback_forbidden()
        resolution = idempotency.reserve(
            scope,
            raw_idempotency_key,
            canonical_body_hash,
            correlation_id,
        )
        if isinstance(resolution, CompletedCommandReplay):
            if (
                resolution.callback_authorization_credential_id != matched.id
                or resolution.callback_authorization_credential_version
                != matched.credential_version
            ):
                raise callback_forbidden()
        elif matched.state != "Active":
            raise callback_forbidden()

        return AuthorizedCallbackCommand(
            attempt=attempt,
            operation=operation,
            service_request=service_request,
            credential=matched,
            database_now=database_now,
            idempotency=idempotency,
            resolution=resolution,
        )

    def _find_existing(
        self, scope: CommandIdempotencyScope, raw_idempotency_key: str
    ) -> CommandIdempotencyRecord | None:
        digest = command_key_digest(raw_idempotency_key)
        return self.session.scalar(
            select(CommandIdempotencyRecord).where(
                CommandIdempotencyRecord.actor_class == scope.actor_class,
                CommandIdempotencyRecord.actor_id == scope.actor_id,
                CommandIdempotencyRecord.command_intent == scope.command_intent,
                CommandIdempotencyRecord.route_template == scope.route_template,
                CommandIdempotencyRecord.target_type == scope.target_type,
                CommandIdempotencyRecord.target_id == scope.target_id,
                CommandIdempotencyRecord.idempotency_key_digest == digest,
            )
        )

    @staticmethod
    def _validate_graph(
        *,
        attempt: IntegrationAttempt,
        operation: LogicalOperation,
        service_request: ServiceRequest,
        siblings: list[IntegrationAttempt],
        expected_operation_kind: OperationKind,
    ) -> None:
        if (
            attempt.operation_kind != expected_operation_kind
            or operation.operation_kind != expected_operation_kind
            or attempt.operation_kind != operation.operation_kind
            or attempt.adapter_name != operation.adapter_name
            or attempt.adapter_version != operation.adapter_version
        ):
            _internal_error("attempt does not match frozen operation intent")
        if (
            operation.service_request_id != service_request.id
            or attempt.service_request_id != service_request.id
        ):
            _internal_error("attempt ownership graph is inconsistent")
        if len([row for row in siblings if row.id == attempt.id]) != 1:
            _internal_error("attempt is absent from its operation")
        if attempt.state == "Running":
            contradictions = [
                row
                for row in siblings
                if row.id != attempt.id and row.state in ("Pending", "Running", "Succeeded")
            ]
            if contradictions or operation.succeeded_attempt_id is not None:
                _internal_error("running attempt graph is inconsistent")
        elif attempt.state == "Succeeded":
            if operation.succeeded_attempt_id != attempt.id:
                _internal_error("successful operation reference is inconsistent")

    @staticmethod
    def _prove_credential(
        *,
        attempt: IntegrationAttempt,
        credentials: list[AttemptCallbackCredential],
        supplied_credential: str,
        machine: AuthenticatedWorkflowService,
        database_now: datetime,
        expected_operation_kind: OperationKind,
    ) -> AttemptCallbackCredential:
        if not credentials:
            raise callback_forbidden()
        versions = [row.credential_version for row in credentials]
        if any(version <= 0 for version in versions) or len(set(versions)) != len(versions):
            _internal_error("callback credential versions are inconsistent")
        credentials_by_id = {row.id: row for row in credentials}
        for row in credentials:
            if (
                row.integration_attempt_id != attempt.id
                or row.operation_kind != expected_operation_kind
                or row.workflow_service_identity != attempt.assigned_workflow_service
                or row.workflow_environment != attempt.workflow_environment
                or row.workflow_service_identity != machine.stable_service_id
                or row.workflow_environment != machine.environment
                or row.expires_at != attempt.callback_authorization_deadline
                or row.issued_at > database_now
            ):
                _internal_error("callback credential scope is inconsistent")
            replacement = credentials_by_id.get(row.replacement_credential_id)
            state_valid = (
                (
                    row.state == "Active"
                    and row.consumed_at is None
                    and row.replaced_at is None
                    and row.revoked_at is None
                    and row.replacement_credential_id is None
                )
                or (
                    row.state == "Consumed"
                    and row.consumed_at is not None
                    and row.replaced_at is None
                    and row.revoked_at is None
                    and row.replacement_credential_id is None
                )
                or (
                    row.state == "Replaced"
                    and row.consumed_at is None
                    and row.replaced_at is not None
                    and row.revoked_at is None
                    and replacement is not None
                    and replacement.credential_version == row.credential_version + 1
                )
                or (
                    row.state == "Revoked"
                    and row.consumed_at is None
                    and row.replaced_at is None
                    and row.revoked_at is not None
                    and row.replacement_credential_id is None
                )
            )
            if not state_valid:
                _internal_error("callback credential history is inconsistent")

        matches = matching_callback_credentials(credentials, supplied_credential)
        if len(matches) != 1:
            raise callback_forbidden()
        matched = matches[0]
        if matched.expires_at <= database_now:
            raise callback_forbidden()
        highest_version = max(versions)
        if matched.state == "Active":
            active = [row for row in credentials if row.state == "Active"]
            if (
                attempt.state != "Running"
                or len(active) != 1
                or active[0].id != matched.id
                or matched.credential_version != highest_version
            ):
                raise callback_forbidden()
        elif matched.state == "Consumed":
            if (
                attempt.state not in ("Succeeded", "RetryableFailure", "TerminalFailure")
                or any(row.state == "Active" for row in credentials)
                or matched.credential_version != highest_version
            ):
                raise callback_forbidden()
        else:
            raise callback_forbidden()
        return matched
