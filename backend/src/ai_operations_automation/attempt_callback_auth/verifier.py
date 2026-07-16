"""Caller-owned transactional proof for one Running integration attempt."""

import hashlib
import hmac
import uuid
from collections.abc import Iterable
from typing import Literal, NoReturn

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, SessionTransaction
from sqlalchemy.orm.session import SessionTransactionOrigin

from ai_operations_automation.attempt_callback_auth.headers import callback_forbidden
from ai_operations_automation.attempt_callback_auth.models import (
    VerifiedAttemptCallbackContext,
)
from ai_operations_automation.db.models.ai_execution import (
    AttemptCallbackCredential,
    IntegrationAttempt,
    LogicalOperation,
)
from ai_operations_automation.db.models.intake import ServiceRequest
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService


def _internal_error(message: str) -> NoReturn:
    raise RuntimeError(message)


def matching_callback_credentials(
    credentials: Iterable[AttemptCallbackCredential], supplied_credential: str
) -> list[AttemptCallbackCredential]:
    """Compare every loaded candidate in constant time without a hash predicate."""
    supplied_digest = hashlib.sha256(supplied_credential.encode("ascii")).hexdigest()
    matches: list[AttemptCallbackCredential] = []
    for credential in credentials:
        if hmac.compare_digest(credential.credential_hash, supplied_digest):
            matches.append(credential)
    return matches


class AttemptCallbackCredentialVerifier:
    """Verify callback authority without owning or mutating the transaction."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def verify(
        self,
        *,
        attempt_id: uuid.UUID,
        machine: AuthenticatedWorkflowService,
        supplied_credential: str,
        expected_operation_kind: Literal["AIInterpretation", "OutboundAction"],
    ) -> VerifiedAttemptCallbackContext:
        transaction = self.session.get_transaction()
        if (
            transaction is None
            or transaction.origin is not SessionTransactionOrigin.BEGIN
            or not transaction.is_active
        ):
            raise IntakeError(500, "INTERNAL_ERROR", "The request could not be completed safely.")
        try:
            return self._verify_in_transaction(
                transaction=transaction,
                attempt_id=attempt_id,
                machine=machine,
                supplied_credential=supplied_credential,
                expected_operation_kind=expected_operation_kind,
            )
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

    def _verify_in_transaction(
        self,
        *,
        transaction: SessionTransaction,
        attempt_id: uuid.UUID,
        machine: AuthenticatedWorkflowService,
        supplied_credential: str,
        expected_operation_kind: Literal["AIInterpretation", "OutboundAction"],
    ) -> VerifiedAttemptCallbackContext:
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
            _internal_error("attempt has no owning service request")
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
        if attempt.state != "Running":
            raise callback_forbidden()
        if (
            attempt.version <= 0
            or attempt.started_at is None
            or attempt.completed_at is not None
            or attempt.result_hash is not None
            or attempt.sanitized_error_code is not None
        ):
            _internal_error("running attempt lifecycle fields are inconsistent")

        active = [row for row in credentials if row.state == "Active"]
        if not active:
            raise callback_forbidden()
        if len(active) != 1:
            _internal_error("attempt has multiple active callback credentials")
        versions = [row.credential_version for row in credentials]
        if any(version <= 0 for version in versions) or len(versions) != len(set(versions)):
            _internal_error("callback credential versions are inconsistent")
        current = active[0]
        if current.credential_version != max(versions):
            _internal_error("active callback credential is not current")
        if (
            current.integration_attempt_id != attempt.id
            or current.operation_kind != expected_operation_kind
            or current.workflow_service_identity != attempt.assigned_workflow_service
            or current.workflow_environment != attempt.workflow_environment
            or current.workflow_service_identity != machine.stable_service_id
            or current.workflow_environment != machine.environment
            or current.expires_at != attempt.callback_authorization_deadline
            or current.consumed_at is not None
            or current.replaced_at is not None
            or current.revoked_at is not None
            or current.replacement_credential_id is not None
        ):
            _internal_error("active callback credential scope is inconsistent")
        if current.issued_at > database_now or current.expires_at <= database_now:
            raise callback_forbidden()

        credentials_by_id = {row.id: row for row in credentials}
        for historical in credentials:
            if historical.id == current.id:
                continue
            replacement = credentials_by_id.get(historical.replacement_credential_id)
            if historical.state == "Replaced":
                valid_history = (
                    historical.consumed_at is None
                    and historical.replaced_at is not None
                    and historical.revoked_at is None
                    and historical.replacement_credential_id is not None
                    and replacement is not None
                    and replacement.credential_version == historical.credential_version + 1
                )
            elif historical.state == "Revoked":
                valid_history = (
                    historical.consumed_at is None
                    and historical.replaced_at is None
                    and historical.revoked_at is not None
                    and historical.replacement_credential_id is None
                )
            elif historical.state == "Consumed":
                valid_history = (
                    historical.consumed_at is not None
                    and historical.replaced_at is None
                    and historical.revoked_at is None
                    and historical.replacement_credential_id is None
                )
            else:
                valid_history = False
            if (
                not valid_history
                or historical.operation_kind != expected_operation_kind
                or historical.workflow_service_identity != attempt.assigned_workflow_service
                or historical.workflow_environment != attempt.workflow_environment
                or historical.expires_at != attempt.callback_authorization_deadline
                or historical.issued_at > database_now
            ):
                _internal_error("callback credential history is inconsistent")

        matches = matching_callback_credentials(credentials, supplied_credential)
        if len(matches) != 1 or matches[0].id != current.id:
            raise callback_forbidden()

        return VerifiedAttemptCallbackContext(
            machine_identity_id=machine.machine_identity_id,
            stable_service_id=machine.stable_service_id,
            workflow_environment=machine.environment,
            integration_attempt_id=attempt.id,
            integration_attempt_version=attempt.version,
            logical_operation_id=operation.id,
            service_request_id=service_request.id,
            operation_kind=expected_operation_kind,
            callback_credential_id=current.id,
            callback_credential_version=current.credential_version,
            callback_credential_expires_at=current.expires_at,
            _session=self.session,
            _transaction=transaction,
        )

    @staticmethod
    def _validate_graph(
        *,
        attempt: IntegrationAttempt,
        operation: LogicalOperation,
        service_request: ServiceRequest,
        siblings: list[IntegrationAttempt],
        expected_operation_kind: Literal["AIInterpretation", "OutboundAction"],
    ) -> None:
        if (
            attempt.operation_kind != expected_operation_kind
            or operation.operation_kind != expected_operation_kind
            or attempt.operation_kind != operation.operation_kind
        ):
            _internal_error("attempt does not match frozen operation intent")
        if expected_operation_kind == "AIInterpretation" and (
            attempt.adapter_name != operation.adapter_name
            or attempt.adapter_version != operation.adapter_version
        ):
            _internal_error("AI attempt does not match frozen operation intent")
        if expected_operation_kind == "OutboundAction" and (
            attempt.proposal_series_id != operation.proposal_series_id
            or attempt.stable_outbound_key_scope != operation.outbound_key_scope
            or attempt.stable_outbound_key_digest != operation.outbound_key_digest
        ):
            _internal_error("outbound attempt does not match frozen operation intent")
        if (
            operation.service_request_id != service_request.id
            or operation.service_request_id != attempt.service_request_id
        ):
            _internal_error("attempt ownership graph is inconsistent")
        targets = [row for row in siblings if row.id == attempt.id]
        contradictory = [
            row
            for row in siblings
            if row.id != attempt.id and row.state in ("Pending", "Running", "Succeeded")
        ]
        if len(targets) != 1 or contradictory or operation.succeeded_attempt_id is not None:
            _internal_error("logical operation attempt graph is inconsistent")
