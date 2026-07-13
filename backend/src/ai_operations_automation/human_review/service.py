"""Atomic bounded human-review recalculation command."""

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
from ai_operations_automation.db.models.ai_execution import AiInterpretation
from ai_operations_automation.db.models.decision import (
    DuplicateCandidate,
    ReviewedFactSet,
    RoutingDecision,
    RoutingDecisionDuplicateCandidate,
)
from ai_operations_automation.db.models.intake import ServiceRequest
from ai_operations_automation.deterministic_decision import DecisionPolicyError, evaluate_decision
from ai_operations_automation.deterministic_decision.models import (
    CandidateDisposition,
    DamageOrDeterioration,
    DecisionEvaluationInput,
    DecisionSource,
    MaterialImpact,
    MissingInformationCode,
    Priority,
    ReviewedFacts,
    SafetyOrContinuityConcern,
    ServiceCategory,
    ServiceInterruption,
    UrgentReviewDisposition,
)
from ai_operations_automation.deterministic_decision.repository import (
    select_active_decision_policy,
)
from ai_operations_automation.event_writing import (
    AuditSpec,
    OutboxSpec,
    write_audit_and_optional_outbox,
)
from ai_operations_automation.human_review.contracts import HumanReviewOutcome
from ai_operations_automation.human_review.models import CompleteHumanReviewRequest
from ai_operations_automation.intake.errors import IntakeError

ROUTE_TEMPLATE = "/api/v1/service-requests/{request_id}/commands/complete-human-review"
URGENT_ROLES = {"ManagerApprover", "Administrator"}


class CompleteHumanReviewService:
    """Record one immutable fact set and rerun the complete policy."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def execute(
        self,
        *,
        request_id: uuid.UUID,
        command: CompleteHumanReviewRequest,
        raw_idempotency_key: str,
        canonical_body_hash: str,
        correlation_id: uuid.UUID,
        actor: AuthenticatedHuman,
    ) -> HumanReviewOutcome:
        try:
            with self.session_factory() as session:
                with session.begin():
                    idempotency = CommandIdempotencyService(session)
                    resolution = idempotency.reserve(
                        CommandIdempotencyScope(
                            actor_class="HumanActor",
                            actor_id=actor.actor_id,
                            command_intent="CompleteHumanReview",
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
        command: CompleteHumanReviewRequest,
        correlation_id: uuid.UUID,
        actor: AuthenticatedHuman,
    ) -> HumanReviewOutcome:
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
        if (
            service_request.status != "HumanReview"
            or service_request.current_queue != "HumanReview"
            or service_request.current_routing_decision_id is None
            or not service_request.review_required
        ):
            return self._guard(
                idempotency,
                reservation,
                409,
                "INVALID_STATE_TRANSITION",
                "Human review cannot be completed from the current request state.",
            )
        prior = session.scalar(
            select(RoutingDecision)
            .where(
                RoutingDecision.id == service_request.current_routing_decision_id,
                RoutingDecision.service_request_id == service_request.id,
            )
            .with_for_update()
        )
        interpretation = session.scalar(
            select(AiInterpretation)
            .where(
                AiInterpretation.id == service_request.current_interpretation_id,
                AiInterpretation.service_request_id == service_request.id,
            )
            .with_for_update()
        )
        if (
            prior is None
            or interpretation is None
            or prior.ai_interpretation_id != interpretation.id
        ):
            return self._guard(
                idempotency,
                reservation,
                409,
                "TRIAGE_EVIDENCE_STALE",
                "Current reviewed routing evidence is unavailable.",
            )
        pending_duplicate = session.scalar(
            select(func.count())
            .select_from(DuplicateCandidate)
            .where(
                DuplicateCandidate.service_request_id == service_request.id,
                DuplicateCandidate.resolution_status == "Pending",
                DuplicateCandidate.stale_at.is_(None),
                DuplicateCandidate.deterministic_score >= 60,
            )
        )
        if pending_duplicate:
            return self._guard(
                idempotency,
                reservation,
                409,
                "REVIEW_REQUIREMENTS_UNRESOLVED",
                "Duplicate review must be resolved through its dedicated command.",
            )
        addressed = set(command.addressed_review_reason_codes)
        current_codes = set(prior.review_reason_codes)
        if not addressed or not addressed <= current_codes:
            return self._guard(
                idempotency,
                reservation,
                409,
                "REVIEW_REQUIREMENTS_UNRESOLVED",
                "Addressed review reasons do not match the current decision.",
            )
        database_now = session.scalar(select(func.now()))
        if database_now is None or database_now.tzinfo is None or database_now.utcoffset() is None:
            raise RuntimeError("database time must be timezone-aware")
        policy = select_active_decision_policy(session, database_now)
        expected = command.expected_policy
        if expected is not None and (
            expected.policy_key != policy.policy_key
            or expected.semantic_version != policy.semantic_version
            or expected.revision != policy.revision
        ):
            return self._guard(
                idempotency,
                reservation,
                409,
                "POLICY_VERSION_CONFLICT",
                "The active decision policy changed after the review was prepared.",
            )
        try:
            prior_input = DecisionEvaluationInput.model_validate(prior.canonical_input_snapshot)
        except Exception as exc:
            raise RuntimeError("stored routing input is not reproducible") from exc
        fact_set_id = uuid.uuid4()
        rationale_reference = (
            "rationale-sha256:" + hashlib.sha256(command.rationale.encode("utf-8")).hexdigest()
        )
        previous_reviewed_facts = prior_input.reviewed_facts
        if previous_reviewed_facts is not None and prior.final_priority != "Urgent":
            previous_reviewed_facts = previous_reviewed_facts.model_copy(
                update={"urgent_review_disposition": None}
            )
        reviewed = self._reviewed_facts(
            command,
            fact_set_id,
            rationale_reference,
            previous_reviewed_facts,
        )
        refreshed_candidates = []
        for candidate_input in prior_input.duplicate_candidates:
            if candidate_input.observation_id is None:
                refreshed_candidates.append(candidate_input)
                continue
            row = session.scalar(
                select(DuplicateCandidate)
                .where(
                    DuplicateCandidate.id == candidate_input.observation_id,
                    DuplicateCandidate.service_request_id == service_request.id,
                )
                .with_for_update()
            )
            if row is None:
                raise RuntimeError("routing candidate evidence is no longer request-owned")
            refreshed_candidates.append(
                candidate_input.model_copy(
                    update={
                        "disposition": CandidateDisposition(row.resolution_status),
                        "eligible_record": row.stale_at is None,
                    }
                )
            )
        reviewed_ids = tuple((*prior_input.reviewed_fact_set_ids, fact_set_id))
        recalculation_input = prior_input.model_copy(
            update={
                "evaluation_at": database_now,
                "duplicate_candidates": tuple(refreshed_candidates),
                "reviewed_fact_set_ids": reviewed_ids,
                "reviewed_facts": reviewed,
                "source": DecisionSource.REVIEWED_FACT_RECALCULATION,
                "current_priority": Priority(prior.final_priority),
            }
        )
        try:
            evaluation = evaluate_decision(recalculation_input, policy)
        except DecisionPolicyError as exc:
            if exc.code != "URGENT_REVIEW_DISPOSITION_INVALID":
                raise
            return self._guard(
                idempotency,
                reservation,
                409,
                "REVIEW_REQUIREMENTS_UNRESOLVED",
                "The Urgent review disposition does not apply to the current evidence.",
            )
        if evaluation.requires_manager_or_administrator and actor.role not in URGENT_ROLES:
            return self._guard(
                idempotency,
                reservation,
                403,
                "FORBIDDEN",
                "The requested review requires manager or administrator authority.",
            )

        fact_snapshot = command.reviewed_facts.model_dump(mode="json", exclude_none=True)
        fact_set = ReviewedFactSet(
            id=fact_set_id,
            service_request_id=service_request.id,
            reviewed_actor_id=actor.actor_id,
            schema_version=command.schema_version,
            addressed_review_reason_codes=list(command.addressed_review_reason_codes),
            fact_snapshot=fact_snapshot,
            rationale_reference=rationale_reference,
            supporting_evidence_references=list(command.supporting_evidence_references),
        )
        session.add(fact_set)
        session.flush()
        decision = RoutingDecision(
            id=uuid.uuid4(),
            service_request_id=service_request.id,
            decision_number=prior.decision_number + 1,
            policy_id=policy.id,
            policy_semantic_version=policy.semantic_version,
            policy_revision=policy.revision,
            policy_digest=policy.content_digest,
            evaluated_at=database_now,
            canonical_input_hash=evaluation.canonical_input_hash,
            canonical_input_snapshot=recalculation_input.model_dump(mode="json"),
            ai_interpretation_id=interpretation.id,
            ai_interpretation_number=interpretation.interpretation_number,
            ai_confidence=interpretation.confidence,
            missing_information_codes=[item.value for item in evaluation.missing_information_codes],
            prior_decision_id=prior.id,
            reviewed_fact_set_id=fact_set.id,
            final_category=evaluation.final_category.value,
            final_priority=evaluation.final_priority.value,
            final_status=evaluation.final_status.value,
            final_queue=evaluation.final_queue.value,
            review_required=evaluation.review_required,
            review_reason_codes=[item.value for item in evaluation.review_reason_codes],
            category_reason_codes=[item.value for item in evaluation.category_reason_codes],
            priority_reason_codes=[item.value for item in evaluation.priority_reason_codes],
            decision_source=evaluation.source.value,
            reviewed_actor_id=actor.actor_id,
            reviewed_rationale_reference=rationale_reference,
        )
        session.add(decision)
        session.flush()
        for position, candidate in enumerate(evaluation.duplicate_candidates, start=1):
            if candidate.observation_id is None:
                continue
            row = session.get(DuplicateCandidate, candidate.observation_id)
            if row is None or row.service_request_id != service_request.id:
                raise RuntimeError("reviewed candidate evidence is not request-owned")
            role = (
                "StaleHistorical"
                if row.stale_at is not None
                else (
                    "CurrentPending" if row.resolution_status == "Pending" else "ResolvedHistorical"
                )
            )
            session.add(
                RoutingDecisionDuplicateCandidate(
                    routing_decision_id=decision.id,
                    position=position,
                    service_request_id=service_request.id,
                    duplicate_candidate_id=row.id,
                    evidence_role=role,
                )
            )
        old_queue = service_request.current_queue
        old_status = service_request.status
        service_request.version += 1
        service_request.category = evaluation.final_category.value
        service_request.priority = evaluation.final_priority.value
        service_request.status = evaluation.final_status.value
        service_request.current_queue = evaluation.final_queue.value
        service_request.current_routing_decision_id = decision.id
        service_request.review_required = evaluation.review_required
        service_request.review_reason_codes = [
            item.value for item in evaluation.review_reason_codes
        ]
        session.flush()

        policy_evidence = {
            "policy_id": str(policy.id),
            "policy_key": policy.policy_key,
            "semantic_version": policy.semantic_version,
            "revision": policy.revision,
            "content_digest": policy.content_digest,
        }
        safe_result = {
            "service_request_id": str(service_request.id),
            "reviewed_fact_set_id": str(fact_set.id),
            "routing_decision_id": str(decision.id),
            "routing_decision_version": decision.decision_number,
            "policy": policy_evidence,
            "category": evaluation.final_category.value,
            "priority": evaluation.final_priority.value,
            "service_request_status": evaluation.final_status.value,
            "service_request_queue": evaluation.final_queue.value,
            "review_required": evaluation.review_required,
            "outstanding_review_reason_codes": [
                item.value for item in evaluation.review_reason_codes
            ],
        }
        facts_evidence = {
            "service_request_id": str(service_request.id),
            "reviewed_fact_set_id": str(fact_set.id),
            "reviewed_actor_id": str(actor.actor_id),
            "reviewed_fact_names": sorted(fact_snapshot),
            "addressed_review_reason_codes": list(command.addressed_review_reason_codes),
            "rationale_reference": rationale_reference,
        }
        write_audit_and_optional_outbox(
            session,
            AuditSpec(
                event_name="reviewed_facts.recorded",
                aggregate_type="ReviewedFactSet",
                aggregate_id=fact_set.id,
                aggregate_version=1,
                actor_type="HumanActor",
                actor_reference_id=actor.actor_id,
                outcome="Recorded",
                correlation_id=correlation_id,
                causation_id=reservation.command_id,
                command_id=reservation.command_id,
                reason_codes=tuple(command.addressed_review_reason_codes),
                safe_metadata=facts_evidence,
            ),
        )
        write_audit_and_optional_outbox(
            session,
            AuditSpec(
                event_name="routing_decision.recalculated",
                aggregate_type="RoutingDecision",
                aggregate_id=decision.id,
                aggregate_version=decision.decision_number,
                actor_type="HumanActor",
                actor_reference_id=actor.actor_id,
                outcome=evaluation.final_status.value,
                correlation_id=correlation_id,
                causation_id=reservation.command_id,
                command_id=reservation.command_id,
                reason_codes=tuple(item.value for item in evaluation.review_reason_codes),
                safe_metadata={
                    **safe_result,
                    "prior_routing_decision_id": str(prior.id),
                    "source": evaluation.source.value,
                },
            ),
        )
        completion_event = (
            "service_request.human_review_incomplete"
            if evaluation.review_required
            else "service_request.human_review_completed"
        )
        consumer_change = (
            old_status != service_request.status or old_queue != service_request.current_queue
        )
        write_audit_and_optional_outbox(
            session,
            AuditSpec(
                event_name=completion_event,
                aggregate_type="ServiceRequest",
                aggregate_id=service_request.id,
                aggregate_version=service_request.version,
                actor_type="HumanActor",
                actor_reference_id=actor.actor_id,
                outcome=service_request.status,
                correlation_id=correlation_id,
                causation_id=reservation.command_id,
                command_id=reservation.command_id,
                reason_codes=tuple(item.value for item in evaluation.review_reason_codes),
                safe_metadata=safe_result,
            ),
            (
                OutboxSpec(
                    event_type=(
                        "service_request.ready_for_action"
                        if not evaluation.review_required
                        else "service_request.human_review_required"
                    ),
                    payload=safe_result,
                )
                if consumer_change
                else None
            ),
        )
        if old_queue != service_request.current_queue:
            queue_evidence = {
                "service_request_id": str(service_request.id),
                "old_queue": old_queue,
                "new_queue": service_request.current_queue,
                "routing_decision_id": str(decision.id),
                "policy": policy_evidence,
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
                    reason_codes=tuple(item.value for item in evaluation.review_reason_codes),
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
        return HumanReviewOutcome(
            logical_http_status=200,
            command_id=completed.command_id,
            safe_snapshot=deepcopy(completed.safe_response_snapshot),
        )

    @staticmethod
    def _reviewed_facts(
        command: CompleteHumanReviewRequest,
        fact_set_id: uuid.UUID,
        rationale_reference: str,
        previous: ReviewedFacts | None,
    ) -> ReviewedFacts:
        facts = command.reviewed_facts

        def current_or_previous[T](current: T | None, prior: T | None) -> T | None:
            return current if current is not None else prior

        previous_resolved = (
            set(previous.resolved_missing_information_codes) if previous is not None else set()
        )
        resolved_codes = previous_resolved | {
            MissingInformationCode(value) for value in facts.resolved_missing_information_codes
        }
        return ReviewedFacts(
            fact_set_id=fact_set_id,
            corrected_category=current_or_previous(
                ServiceCategory(facts.corrected_category)
                if facts.corrected_category is not None
                else None,
                previous.corrected_category if previous is not None else None,
            ),
            corrected_requested_deadline=current_or_previous(
                facts.corrected_requested_deadline,
                previous.corrected_requested_deadline if previous is not None else None,
            ),
            corrected_timing_preference_present=current_or_previous(
                facts.corrected_timing_preference_present,
                previous.corrected_timing_preference_present if previous is not None else None,
            ),
            corrected_timing_is_flexible=current_or_previous(
                facts.corrected_timing_is_flexible,
                previous.corrected_timing_is_flexible if previous is not None else None,
            ),
            corrected_safety_or_continuity_concern=current_or_previous(
                SafetyOrContinuityConcern(facts.corrected_safety_or_continuity_concern)
                if facts.corrected_safety_or_continuity_concern is not None
                else None,
                previous.corrected_safety_or_continuity_concern if previous is not None else None,
            ),
            corrected_service_interruption=current_or_previous(
                ServiceInterruption(facts.corrected_service_interruption)
                if facts.corrected_service_interruption is not None
                else None,
                previous.corrected_service_interruption if previous is not None else None,
            ),
            corrected_damage_or_deterioration=current_or_previous(
                DamageOrDeterioration(facts.corrected_damage_or_deterioration)
                if facts.corrected_damage_or_deterioration is not None
                else None,
                previous.corrected_damage_or_deterioration if previous is not None else None,
            ),
            corrected_material_impact=current_or_previous(
                MaterialImpact(facts.corrected_material_impact)
                if facts.corrected_material_impact is not None
                else None,
                previous.corrected_material_impact if previous is not None else None,
            ),
            resolved_missing_information_codes=tuple(
                code for code in MissingInformationCode if code in resolved_codes
            ),
            custom_scope_confirmed=current_or_previous(
                facts.custom_scope_confirmed,
                previous.custom_scope_confirmed if previous is not None else None,
            ),
            urgent_review_disposition=current_or_previous(
                UrgentReviewDisposition(facts.urgent_review_disposition)
                if facts.urgent_review_disposition is not None
                else None,
                previous.urgent_review_disposition if previous is not None else None,
            ),
            rationale_reference=rationale_reference,
            supporting_evidence_references=tuple(command.supporting_evidence_references),
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
    ) -> HumanReviewOutcome:
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
        return HumanReviewOutcome(
            logical_http_status=status,
            command_id=completed.command_id,
            safe_snapshot=deepcopy(completed.safe_response_snapshot),
        )

    @staticmethod
    def _replay(replay: CompletedCommandReplay) -> HumanReviewOutcome:
        return HumanReviewOutcome(
            logical_http_status=replay.logical_http_status,
            command_id=replay.command_id,
            safe_snapshot=deepcopy(replay.safe_response_snapshot),
        )
