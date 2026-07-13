"""Atomic human duplicate-candidate resolution command."""

import hashlib
import uuid
from copy import deepcopy

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
from ai_operations_automation.db.models.decision import DuplicateCandidate
from ai_operations_automation.db.models.intake import ServiceRequest
from ai_operations_automation.duplicate_resolution.contracts import DuplicateResolutionOutcome
from ai_operations_automation.duplicate_resolution.models import ResolveDuplicateRequest
from ai_operations_automation.event_writing import (
    AuditSpec,
    OutboxSpec,
    write_audit_and_optional_outbox,
)
from ai_operations_automation.intake.errors import IntakeError

ROUTE_TEMPLATE = (
    "/api/v1/service-requests/{request_id}/duplicate-candidates/{candidate_id}/commands/resolve"
)


class ResolveDuplicateService:
    """Resolve one current observation without merging or deleting records."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def execute(
        self,
        *,
        request_id: uuid.UUID,
        candidate_id: uuid.UUID,
        command: ResolveDuplicateRequest,
        raw_idempotency_key: str,
        canonical_body_hash: str,
        correlation_id: uuid.UUID,
        actor: AuthenticatedHuman,
    ) -> DuplicateResolutionOutcome:
        try:
            with self.session_factory() as session:
                with session.begin():
                    idempotency = CommandIdempotencyService(session)
                    resolution = idempotency.reserve(
                        CommandIdempotencyScope(
                            actor_class="HumanActor",
                            actor_id=actor.actor_id,
                            command_intent="ResolveDuplicateCandidate",
                            route_template=ROUTE_TEMPLATE,
                            target_type="DuplicateCandidate",
                            target_id=candidate_id,
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
                        candidate_id=candidate_id,
                        command=command,
                        correlation_id=correlation_id,
                        actor=actor,
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
                500,
                "INTERNAL_ERROR",
                "The request could not be completed safely.",
            ) from exc
        except Exception as exc:
            raise IntakeError(
                500,
                "INTERNAL_ERROR",
                "The request could not be completed safely.",
            ) from exc

    def _execute_new(
        self,
        *,
        session: Session,
        idempotency: CommandIdempotencyService,
        reservation: NewCommandReservation,
        request_id: uuid.UUID,
        candidate_id: uuid.UUID,
        command: ResolveDuplicateRequest,
        correlation_id: uuid.UUID,
        actor: AuthenticatedHuman,
    ) -> DuplicateResolutionOutcome:
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
        candidate = session.scalar(
            select(DuplicateCandidate)
            .where(
                DuplicateCandidate.id == candidate_id,
                DuplicateCandidate.service_request_id == request_id,
            )
            .with_for_update()
        )
        if candidate is None:
            return self._guard(
                idempotency,
                reservation,
                404,
                "RESOURCE_NOT_FOUND",
                "The requested resource was not found.",
            )
        if (
            service_request.status != "DuplicateReview"
            or service_request.current_queue != "DuplicateReview"
            or candidate.resolution_status != "Pending"
            or candidate.stale_at is not None
            or candidate.deterministic_score < 60
        ):
            return self._guard(
                idempotency,
                reservation,
                409,
                "DUPLICATE_RESOLUTION_CONFLICT",
                "The candidate cannot be resolved from its current state.",
            )
        if command.command.decision == "ConfirmedDuplicate" and command.command.rationale is None:
            return self._guard(
                idempotency,
                reservation,
                422,
                "VALIDATION_FAILED",
                "Confirmed duplicate resolution requires a rationale.",
            )
        database_now = session.scalar(select(func.now()))
        if database_now is None or database_now.tzinfo is None or database_now.utcoffset() is None:
            raise RuntimeError("database time must be timezone-aware")
        rationale = command.command.rationale or "Not duplicate after bounded evidence review."
        rationale_reference = (
            "rationale-sha256:" + hashlib.sha256(rationale.encode("utf-8")).hexdigest()
        )
        candidate.resolution_status = command.command.decision
        candidate.resolved_by_actor_id = actor.actor_id
        candidate.resolution_rationale_reference = rationale_reference
        candidate.resolved_at = database_now
        old_queue = service_request.current_queue
        service_request.version += 1
        if command.command.decision == "ConfirmedDuplicate":
            service_request.status = "ClosedDuplicate"
            service_request.current_queue = None
            service_request.review_required = False
            service_request.review_reason_codes = []
            transition_event = "service_request.closed_duplicate"
        else:
            other_pending = session.scalar(
                select(func.count())
                .select_from(DuplicateCandidate)
                .where(
                    DuplicateCandidate.service_request_id == service_request.id,
                    DuplicateCandidate.id != candidate.id,
                    DuplicateCandidate.resolution_status == "Pending",
                    DuplicateCandidate.stale_at.is_(None),
                    DuplicateCandidate.deterministic_score >= 60,
                )
            )
            if other_pending:
                service_request.status = "DuplicateReview"
                service_request.current_queue = "DuplicateReview"
                service_request.review_required = True
                service_request.review_reason_codes = ["REVIEW_POSSIBLE_DUPLICATE"]
                transition_event = "service_request.duplicate_review_required"
            else:
                service_request.status = "TriagePending"
                service_request.current_queue = None
                service_request.review_required = False
                service_request.review_reason_codes = []
                transition_event = "service_request.triage_reopened"
        session.flush()

        safe_result = {
            "service_request_id": str(service_request.id),
            "duplicate_candidate_id": str(candidate.id),
            "candidate_resolution": candidate.resolution_status,
            "service_request_status": service_request.status,
            "service_request_queue": service_request.current_queue,
        }
        safe_evidence = {
            **safe_result,
            "resolver_actor_id": str(actor.actor_id),
            "resolver_role": actor.role,
        }
        write_audit_and_optional_outbox(
            session,
            AuditSpec(
                event_name="duplicate_candidate.resolved",
                aggregate_type="DuplicateCandidate",
                aggregate_id=candidate.id,
                aggregate_version=1,
                actor_type="HumanActor",
                actor_reference_id=actor.actor_id,
                outcome=candidate.resolution_status,
                correlation_id=correlation_id,
                causation_id=reservation.command_id,
                command_id=reservation.command_id,
                reason_codes=(candidate.resolution_status,),
                safe_metadata=safe_evidence,
            ),
        )
        write_audit_and_optional_outbox(
            session,
            AuditSpec(
                event_name=transition_event,
                aggregate_type="ServiceRequest",
                aggregate_id=service_request.id,
                aggregate_version=service_request.version,
                actor_type="HumanActor",
                actor_reference_id=actor.actor_id,
                outcome=service_request.status,
                correlation_id=correlation_id,
                causation_id=reservation.command_id,
                command_id=reservation.command_id,
                reason_codes=(candidate.resolution_status,),
                safe_metadata=safe_evidence,
            ),
            OutboxSpec(event_type=transition_event, payload=safe_evidence),
        )
        if old_queue != service_request.current_queue:
            queue_evidence = {
                "service_request_id": str(service_request.id),
                "old_queue": old_queue,
                "new_queue": service_request.current_queue,
                "reason_codes": [candidate.resolution_status],
            }
            write_audit_and_optional_outbox(
                session,
                AuditSpec(
                    event_name="service_request.queue_changed",
                    aggregate_type="ServiceRequest",
                    aggregate_id=service_request.id,
                    aggregate_version=service_request.version,
                    actor_type="HumanActor",
                    actor_reference_id=actor.actor_id,
                    outcome="Changed",
                    correlation_id=correlation_id,
                    causation_id=reservation.command_id,
                    command_id=reservation.command_id,
                    reason_codes=(candidate.resolution_status,),
                    safe_metadata=queue_evidence,
                ),
                OutboxSpec(event_type="service_request.queue_changed", payload=queue_evidence),
            )
        completed = idempotency.complete(
            reservation,
            200,
            {
                "result": safe_result,
                "versions": {"service_request": service_request.version},
            },
        )
        return DuplicateResolutionOutcome(
            logical_http_status=200,
            command_id=completed.command_id,
            safe_snapshot=deepcopy(completed.safe_response_snapshot),
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
    ) -> DuplicateResolutionOutcome:
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
        return DuplicateResolutionOutcome(
            logical_http_status=status,
            command_id=completed.command_id,
            safe_snapshot=deepcopy(completed.safe_response_snapshot),
        )

    @staticmethod
    def _replay(replay: CompletedCommandReplay) -> DuplicateResolutionOutcome:
        return DuplicateResolutionOutcome(
            logical_http_status=replay.logical_http_status,
            command_id=replay.command_id,
            safe_snapshot=deepcopy(replay.safe_response_snapshot),
        )
