"""Caller-owned transaction reservation, replay, conflict, and completion."""

import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, SessionTransaction, SessionTransactionOrigin

from ai_operations_automation.command_idempotency.keys import command_key_digest
from ai_operations_automation.command_idempotency.models import (
    CommandIdempotencyScope,
    CompletedCommandReplay,
    NewCommandReservation,
    SecretDeliveryMetadata,
)
from ai_operations_automation.command_idempotency.snapshots import validate_safe_snapshot
from ai_operations_automation.db.models.command_idempotency import CommandIdempotencyRecord
from ai_operations_automation.intake.errors import IntakeError

HASH = re.compile(r"^[0-9a-f]{64}$")


class CommandIdempotencyService:
    """Resolve one command key without controlling the caller's outer transaction."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def reserve(
        self,
        scope: CommandIdempotencyScope,
        raw_key: str,
        canonical_body_hash: str,
        correlation_id: uuid.UUID,
    ) -> NewCommandReservation | CompletedCommandReplay:
        outer_transaction = self._require_outer_transaction()
        if HASH.fullmatch(canonical_body_hash) is None:
            raise ValueError("canonical body hash must be lowercase SHA-256 hexadecimal")
        key_digest = command_key_digest(raw_key)
        try:
            existing = self._find(scope, key_digest)
        except SQLAlchemyError as exc:
            raise self._dependency_error() from exc
        if existing is not None:
            return self._classify(existing, canonical_body_hash)

        record = CommandIdempotencyRecord(
            id=uuid.uuid4(),
            actor_class=scope.actor_class,
            actor_id=scope.actor_id,
            command_intent=scope.command_intent,
            route_template=scope.route_template,
            target_type=scope.target_type,
            target_id=scope.target_id,
            idempotency_key_digest=key_digest,
            canonical_body_hash=canonical_body_hash,
            status="Processing",
            command_id=uuid.uuid4(),
            correlation_id=correlation_id,
        )
        try:
            with self.session.begin_nested():
                self.session.add(record)
                self.session.flush()
        except IntegrityError:
            try:
                winner = self._find(scope, key_digest, lock=True)
            except SQLAlchemyError as exc:
                raise self._dependency_error() from exc
            if winner is None:
                raise self._dependency_error()
            return self._classify(winner, canonical_body_hash)
        except SQLAlchemyError as exc:
            raise self._dependency_error() from exc
        return NewCommandReservation(
            record_id=record.id,
            command_id=record.command_id,
            correlation_id=record.correlation_id,
            _session=self.session,
            _outer_transaction=outer_transaction,
        )

    def complete(
        self,
        reservation: NewCommandReservation,
        logical_http_status: int,
        safe_response_snapshot: dict,
        secret_delivery: SecretDeliveryMetadata | None = None,
    ) -> CompletedCommandReplay:
        outer_transaction = self._require_outer_transaction()
        if (
            reservation._session is not self.session
            or reservation._outer_transaction is not outer_transaction
            or not reservation._outer_transaction.is_active
        ):
            raise ValueError("reservation is not owned by this active outer transaction")
        if not 200 <= logical_http_status <= 599:
            raise ValueError("logical HTTP status must be between 200 and 599")
        snapshot = validate_safe_snapshot(safe_response_snapshot)
        try:
            record = self.session.get(CommandIdempotencyRecord, reservation.record_id)
        except SQLAlchemyError as exc:
            raise self._dependency_error() from exc
        if record is None or record.status != "Processing":
            raise ValueError("command reservation is not eligible for completion")
        try:
            with self.session.no_autoflush:
                completed_at = self.session.scalar(select(func.now()))
        except SQLAlchemyError as exc:
            raise self._dependency_error() from exc
        if (
            completed_at is None
            or getattr(completed_at, "tzinfo", None) is None
            or completed_at.utcoffset() is None
        ):
            raise self._dependency_error()
        record.status = "Completed"
        record.logical_http_status = logical_http_status
        record.safe_response_snapshot = snapshot
        record.completed_at = completed_at
        if secret_delivery is not None:
            record.callback_credential_id = secret_delivery.callback_credential_id
            record.callback_credential_version = secret_delivery.callback_credential_version
            record.callback_credential_expires_at = secret_delivery.callback_credential_expires_at
            record.secret_delivery_receipt = "PlaintextIssued"
        try:
            self.session.flush()
        except SQLAlchemyError as exc:
            raise self._dependency_error() from exc
        return self._result(record, is_replay=False)

    def _require_outer_transaction(self) -> SessionTransaction:
        transaction = self.session.get_transaction()
        if (
            transaction is None
            or not transaction.is_active
            or transaction.origin is not SessionTransactionOrigin.BEGIN
        ):
            raise RuntimeError("an active explicit caller-owned outer transaction is required")
        return transaction

    def _find(
        self, scope: CommandIdempotencyScope, key_digest: str, *, lock: bool = False
    ) -> CommandIdempotencyRecord | None:
        statement = select(CommandIdempotencyRecord).where(
            CommandIdempotencyRecord.actor_class == scope.actor_class,
            CommandIdempotencyRecord.actor_id == scope.actor_id,
            CommandIdempotencyRecord.command_intent == scope.command_intent,
            CommandIdempotencyRecord.route_template == scope.route_template,
            CommandIdempotencyRecord.target_type == scope.target_type,
            CommandIdempotencyRecord.target_id == scope.target_id,
            CommandIdempotencyRecord.idempotency_key_digest == key_digest,
        )
        if lock:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def _classify(
        self, record: CommandIdempotencyRecord, canonical_body_hash: str
    ) -> CompletedCommandReplay:
        if record.canonical_body_hash != canonical_body_hash:
            raise IntakeError(
                409,
                "COMMAND_IDEMPOTENCY_CONFLICT",
                "The idempotency key was already used for a different command.",
            )
        if record.status != "Completed":
            raise IntakeError(500, "INTERNAL_ERROR", "The request could not be completed safely.")
        return self._result(record, is_replay=True)

    @staticmethod
    def _result(record: CommandIdempotencyRecord, *, is_replay: bool) -> CompletedCommandReplay:
        return CompletedCommandReplay(
            record_id=record.id,
            command_id=record.command_id,
            original_correlation_id=record.correlation_id,
            logical_http_status=record.logical_http_status,
            safe_response_snapshot=record.safe_response_snapshot,
            completed_at=record.completed_at,
            callback_credential_id=record.callback_credential_id,
            callback_credential_version=record.callback_credential_version,
            callback_credential_expires_at=record.callback_credential_expires_at,
            credential_delivery=(
                "AlreadyIssued"
                if is_replay and record.secret_delivery_receipt == "PlaintextIssued"
                else None
            ),
        )

    @staticmethod
    def _dependency_error() -> IntakeError:
        return IntakeError(
            503,
            "DEPENDENCY_UNAVAILABLE",
            "A required dependency is unavailable.",
            True,
        )
