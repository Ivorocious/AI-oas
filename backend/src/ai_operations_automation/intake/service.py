"""Atomic accepted-intake, replay, conflict, and rejection transactions."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ai_operations_automation.db.models.evidence import AuditEvent, OutboxMessage
from ai_operations_automation.db.models.intake import (
    AcceptedIntakeKey,
    Contact,
    InboundDelivery,
    ServiceRequest,
)
from ai_operations_automation.intake.canonicalization import canonical_payload_hash
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.intake.models import (
    IntakeRequest,
    IntakeResponse,
    IntakeResult,
    IntakeVersions,
)

INTAKE_SCOPE = "public-service-request-intake"


@dataclass(frozen=True, slots=True)
class IntakeServiceResult:
    status_code: int
    response: IntakeResponse
    location: str | None = None


class IntakeService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def process_valid(
        self,
        payload: IntakeRequest,
        key_digest: str,
        correlation_id: uuid.UUID,
    ) -> IntakeServiceResult:
        payload_hash = canonical_payload_hash(payload)
        conflict_delivery_id: uuid.UUID | None = None
        with self.session_factory() as session, session.begin():
            reservation = self._reservation(session, key_digest)
            if reservation is not None:
                if reservation.canonical_payload_hash == payload_hash:
                    return self._create_replay(
                        session, reservation, correlation_id, payload.schema_version
                    )
                conflict_delivery_id = self._create_conflict(
                    session, reservation, correlation_id, payload.schema_version
                )
        if conflict_delivery_id is not None:
            raise self._conflict_error(conflict_delivery_id)

        try:
            return self._create_new(payload, key_digest, payload_hash, correlation_id)
        except IntegrityError:
            conflict_delivery_id = None
            with self.session_factory() as session, session.begin():
                winner = self._reservation(session, key_digest)
                if winner is None:
                    raise
                if winner.canonical_payload_hash == payload_hash:
                    return self._create_replay(
                        session, winner, correlation_id, payload.schema_version
                    )
                conflict_delivery_id = self._create_conflict(
                    session, winner, correlation_id, payload.schema_version
                )
            if conflict_delivery_id is not None:
                raise self._conflict_error(conflict_delivery_id)
            raise RuntimeError("unreachable intake race resolution")

    def process_rejected(
        self,
        *,
        key_digest: str,
        correlation_id: uuid.UUID,
        schema_version: str,
        error_code: str,
        issues: list[dict[str, str]],
        raw_body_fingerprint: str | None = None,
    ) -> uuid.UUID:
        conflict_delivery_id: uuid.UUID | None = None
        rejected_delivery_id: uuid.UUID | None = None
        with self.session_factory() as session, session.begin():
            reservation = self._reservation(session, key_digest)
            if reservation is not None:
                conflict_delivery_id = self._create_conflict(
                    session,
                    reservation,
                    correlation_id,
                    schema_version,
                    raw_body_fingerprint=raw_body_fingerprint,
                )
            else:
                delivery_id = uuid.uuid4()
                rejected_delivery_id = delivery_id
                delivery = InboundDelivery(
                    id=delivery_id,
                    scope=INTAKE_SCOPE,
                    idempotency_key_digest=key_digest,
                    processing_status="Rejected",
                    schema_version=schema_version,
                    version=1,
                    correlation_id=correlation_id,
                    raw_body_fingerprint=raw_body_fingerprint,
                    intake_outcome="Invalid",
                    sanitized_issues=issues or None,
                    sanitized_error_code=error_code,
                    completed_at=datetime.now(UTC),
                )
                session.add(delivery)
                self._add_event(
                    session,
                    event_name="inbound_delivery.rejected",
                    aggregate_type="InboundDelivery",
                    aggregate_id=delivery_id,
                    aggregate_version=1,
                    delivery_id=delivery_id,
                    correlation_id=correlation_id,
                    outcome="Invalid",
                    reason_codes=[error_code],
                    payload={"delivery_id": str(delivery_id), "intake_outcome": "Invalid"},
                )

        if conflict_delivery_id is not None:
            raise self._conflict_error(conflict_delivery_id)
        if rejected_delivery_id is None:
            raise RuntimeError("rejected delivery did not commit")
        return rejected_delivery_id

    @staticmethod
    def _reservation(session: Session, key_digest: str) -> AcceptedIntakeKey | None:
        return session.scalar(
            select(AcceptedIntakeKey).where(
                AcceptedIntakeKey.scope == INTAKE_SCOPE,
                AcceptedIntakeKey.idempotency_key_digest == key_digest,
            )
        )

    def _create_new(
        self,
        payload: IntakeRequest,
        key_digest: str,
        payload_hash: str,
        correlation_id: uuid.UUID,
    ) -> IntakeServiceResult:
        delivery_id = uuid.uuid4()
        request_id = uuid.uuid4()
        contact_id = uuid.uuid4()
        reservation_id = uuid.uuid4()
        snapshot = {
            "original_delivery_id": str(delivery_id),
            "service_request_id": str(request_id),
            "service_request_status": "TriagePending",
            "inbound_delivery_version": 1,
            "service_request_version": 1,
        }
        with self.session_factory() as session, session.begin():
            reservation = AcceptedIntakeKey(
                id=reservation_id,
                scope=INTAKE_SCOPE,
                idempotency_key_digest=key_digest,
                canonical_payload_hash=payload_hash,
                original_delivery_id=delivery_id,
                request_id=request_id,
                original_http_status=201,
                safe_response_snapshot=snapshot,
            )
            session.add(reservation)
            session.flush()

            contact = Contact(
                id=contact_id,
                display_label=payload.contact.display_name,
                normalized_email=str(payload.contact.email) if payload.contact.email else None,
                normalized_phone=payload.contact.phone,
                preferred_channel=payload.contact.preferred_channel,
                version=1,
            )
            session.add(contact)
            session.flush()
            delivery = InboundDelivery(
                id=delivery_id,
                scope=INTAKE_SCOPE,
                idempotency_key_digest=key_digest,
                processing_status="Accepted",
                schema_version=payload.schema_version,
                version=1,
                correlation_id=correlation_id,
                canonical_payload_hash=payload_hash,
                intake_outcome="New",
                completed_at=datetime.now(UTC),
            )
            session.add(delivery)
            session.flush()
            service_request = ServiceRequest(
                id=request_id,
                originating_delivery_id=delivery_id,
                contact_id=contact_id,
                normalized_request_description=payload.service_request.description,
                status="TriagePending",
                version=1,
                location_context=payload.service_request.location_context,
                timing_preference=payload.service_request.timing_preference,
            )
            session.add(service_request)
            session.flush()
            delivery.created_request_id = request_id
            delivery.logical_result_request_id = request_id
            delivery.accepted_intake_key_id = reservation_id
            self._add_event(
                session,
                event_name="inbound_delivery.accepted",
                aggregate_type="InboundDelivery",
                aggregate_id=delivery_id,
                aggregate_version=1,
                delivery_id=delivery_id,
                correlation_id=correlation_id,
                outcome="New",
                reason_codes=[],
                payload={
                    "delivery_id": str(delivery_id),
                    "intake_outcome": "New",
                    "service_request_id": str(request_id),
                    "service_request_status": "TriagePending",
                },
            )
            self._add_event(
                session,
                event_name="service_request.created",
                aggregate_type="ServiceRequest",
                aggregate_id=request_id,
                aggregate_version=1,
                delivery_id=delivery_id,
                correlation_id=correlation_id,
                outcome="Created",
                reason_codes=[],
                payload={
                    "delivery_id": str(delivery_id),
                    "service_request_id": str(request_id),
                    "service_request_status": "TriagePending",
                },
            )

        response = self._response(
            correlation_id=correlation_id,
            delivery_id=delivery_id,
            request_id=request_id,
            outcome="New",
        )
        return IntakeServiceResult(
            201,
            response,
            location=f"/api/v1/service-requests/{request_id}",
        )

    def _create_replay(
        self,
        session: Session,
        reservation: AcceptedIntakeKey,
        correlation_id: uuid.UUID,
        schema_version: str,
    ) -> IntakeServiceResult:
        delivery_id = uuid.uuid4()
        snapshot = reservation.safe_response_snapshot
        request_id = uuid.UUID(snapshot["service_request_id"])
        original_delivery_id = uuid.UUID(snapshot["original_delivery_id"])
        session.add(
            InboundDelivery(
                id=delivery_id,
                scope=INTAKE_SCOPE,
                idempotency_key_digest=reservation.idempotency_key_digest,
                processing_status="Accepted",
                schema_version=schema_version,
                version=1,
                correlation_id=correlation_id,
                canonical_payload_hash=reservation.canonical_payload_hash,
                intake_outcome="IdempotentReplay",
                original_delivery_id=original_delivery_id,
                logical_result_request_id=request_id,
                accepted_intake_key_id=reservation.id,
                completed_at=datetime.now(UTC),
            )
        )
        self._add_event(
            session,
            event_name="inbound_delivery.accepted",
            aggregate_type="InboundDelivery",
            aggregate_id=delivery_id,
            aggregate_version=1,
            delivery_id=delivery_id,
            correlation_id=correlation_id,
            outcome="IdempotentReplay",
            reason_codes=[],
            payload={
                "delivery_id": str(delivery_id),
                "original_delivery_id": str(original_delivery_id),
                "intake_outcome": "IdempotentReplay",
                "service_request_id": str(request_id),
            },
        )
        return IntakeServiceResult(
            200,
            self._response(
                correlation_id=correlation_id,
                delivery_id=delivery_id,
                request_id=request_id,
                outcome="IdempotentReplay",
                original_delivery_id=original_delivery_id,
                request_version=int(snapshot["service_request_version"]),
            ),
        )

    def _create_conflict(
        self,
        session: Session,
        reservation: AcceptedIntakeKey,
        correlation_id: uuid.UUID,
        schema_version: str,
        *,
        raw_body_fingerprint: str | None = None,
    ) -> uuid.UUID:
        delivery_id = uuid.uuid4()
        session.add(
            InboundDelivery(
                id=delivery_id,
                scope=INTAKE_SCOPE,
                idempotency_key_digest=reservation.idempotency_key_digest,
                processing_status="Rejected",
                schema_version=schema_version,
                version=1,
                correlation_id=correlation_id,
                raw_body_fingerprint=raw_body_fingerprint,
                intake_outcome="IdempotencyConflict",
                original_delivery_id=reservation.original_delivery_id,
                accepted_intake_key_id=reservation.id,
                sanitized_error_code="IDEMPOTENCY_CONFLICT",
                completed_at=datetime.now(UTC),
            )
        )
        self._add_event(
            session,
            event_name="inbound_delivery.rejected",
            aggregate_type="InboundDelivery",
            aggregate_id=delivery_id,
            aggregate_version=1,
            delivery_id=delivery_id,
            correlation_id=correlation_id,
            outcome="IdempotencyConflict",
            reason_codes=["IDEMPOTENCY_CONFLICT"],
            payload={"delivery_id": str(delivery_id), "intake_outcome": "IdempotencyConflict"},
        )
        return delivery_id

    @staticmethod
    def _conflict_error(delivery_id: uuid.UUID) -> IntakeError:
        return IntakeError(
            409,
            "IDEMPOTENCY_CONFLICT",
            "The idempotency key was already accepted for a different request.",
            delivery_id=delivery_id,
        )

    @staticmethod
    def _response(
        *,
        correlation_id: uuid.UUID,
        delivery_id: uuid.UUID,
        request_id: uuid.UUID,
        outcome: str,
        original_delivery_id: uuid.UUID | None = None,
        request_version: int = 1,
    ) -> IntakeResponse:
        return IntakeResponse(
            correlation_id=correlation_id,
            result=IntakeResult(
                delivery_id=delivery_id,
                service_request_id=request_id,
                intake_outcome=outcome,
                service_request_status="TriagePending",
                original_delivery_id=original_delivery_id,
            ),
            versions=IntakeVersions(inbound_delivery=1, service_request=request_version),
        )

    @staticmethod
    def _add_event(
        session: Session,
        *,
        event_name: str,
        aggregate_type: str,
        aggregate_id: uuid.UUID,
        aggregate_version: int,
        delivery_id: uuid.UUID,
        correlation_id: uuid.UUID,
        outcome: str,
        reason_codes: list[str],
        payload: dict[str, Any],
    ) -> None:
        audit_id = uuid.uuid4()
        session.add(
            AuditEvent(
                id=audit_id,
                schema_version="1.0",
                event_name=event_name,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                aggregate_version=aggregate_version,
                actor_type="Customer",
                actor_reference_id=delivery_id,
                outcome=outcome,
                correlation_id=correlation_id,
                reason_codes=reason_codes,
                safe_metadata={},
            )
        )
        session.add(
            OutboxMessage(
                id=uuid.uuid4(),
                event_type=event_name,
                schema_version="1.0",
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                aggregate_version=aggregate_version,
                audit_event_id=audit_id,
                correlation_id=correlation_id,
                payload=payload,
                publication_state="Pending",
            )
        )
