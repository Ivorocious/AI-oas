"""Atomic trusted BackendService deterministic triage command."""

import hashlib
import json
import uuid
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from ai_operations_automation.command_idempotency.canonicalization import (
    canonical_command_hash,
)
from ai_operations_automation.command_idempotency.models import (
    CommandIdempotencyScope,
    CompletedCommandReplay,
    NewCommandReservation,
)
from ai_operations_automation.command_idempotency.service import CommandIdempotencyService
from ai_operations_automation.db.models.ai_execution import AiInterpretation
from ai_operations_automation.db.models.decision import (
    DuplicateCandidate,
    RoutingDecision,
    RoutingDecisionDuplicateCandidate,
)
from ai_operations_automation.db.models.intake import Contact, ServiceRequest
from ai_operations_automation.deterministic_decision import evaluate_decision
from ai_operations_automation.deterministic_decision.models import (
    AIAdvisory,
    CandidateDisposition,
    CandidateKind,
    DecisionEvaluationInput,
    DecisionSource,
    DuplicateCandidateInput,
    MissingInformationCode,
    ServiceCategory,
)
from ai_operations_automation.deterministic_decision.repository import (
    select_active_decision_policy,
)
from ai_operations_automation.event_writing import (
    AuditSpec,
    OutboxSpec,
    write_audit_and_optional_outbox,
    write_outbox_for_audit,
)
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.stale_attempts.service import BACKEND_SERVICE_ACTOR_ID
from ai_operations_automation.triage.evidence import (
    build_normalized_facts,
    description_evidence,
    normalized_digest,
)
from ai_operations_automation.triage.models import (
    CompleteTriageCommand,
    CompleteTriageOutcome,
)

ROUTE_TEMPLATE = "/internal/complete-triage"
MISSING_CODE_VALUES = {item.value: item for item in MissingInformationCode}


def _safe_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class CompleteTriageService:
    """Create candidates, one immutable decision, and request summary atomically."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def execute(
        self,
        *,
        request_id: uuid.UUID,
        command: CompleteTriageCommand,
        durable_command_key: str,
        correlation_id: uuid.UUID,
    ) -> CompleteTriageOutcome:
        try:
            with self.session_factory() as session:
                with session.begin():
                    idempotency = CommandIdempotencyService(session)
                    resolution = idempotency.reserve(
                        CommandIdempotencyScope(
                            actor_class="BackendService",
                            actor_id=BACKEND_SERVICE_ACTOR_ID,
                            command_intent="CompleteTriage",
                            route_template=ROUTE_TEMPLATE,
                            target_type="ServiceRequest",
                            target_id=request_id,
                        ),
                        durable_command_key,
                        canonical_command_hash(command),
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
        command: CompleteTriageCommand,
        correlation_id: uuid.UUID,
    ) -> CompleteTriageOutcome:
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
        if service_request.version != command.expected_service_request_version:
            return self._guard(
                idempotency,
                reservation,
                409,
                "CONCURRENCY_CONFLICT",
                "The resource version does not match the current version.",
                current_versions={"service_request": service_request.version},
            )
        if service_request.status != "TriagePending":
            return self._guard(
                idempotency,
                reservation,
                409,
                "INVALID_STATE_TRANSITION",
                "Triage cannot be completed from the current request state.",
            )
        if service_request.current_interpretation_id is None:
            return self._guard(
                idempotency,
                reservation,
                409,
                "TRIAGE_EVIDENCE_STALE",
                "Current validated interpretation evidence is required.",
            )
        contact = session.scalar(
            select(Contact).where(Contact.id == service_request.contact_id).with_for_update()
        )
        interpretation = session.scalar(
            select(AiInterpretation)
            .where(
                AiInterpretation.id == service_request.current_interpretation_id,
                AiInterpretation.service_request_id == service_request.id,
            )
            .with_for_update()
        )
        if contact is None or interpretation is None:
            return self._guard(
                idempotency,
                reservation,
                409,
                "TRIAGE_EVIDENCE_STALE",
                "Current validated routing evidence is unavailable.",
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
                "The active decision policy changed after the command was prepared.",
            )

        normalized_facts = build_normalized_facts(service_request, contact, command.facts)
        source_evidence_hash = _safe_hash(normalized_facts.model_dump(mode="json"))
        candidates, existing, prior_current_observations = self._candidate_inputs(
            session=session,
            source_request=service_request,
            normalized_facts=normalized_facts,
            database_now=database_now,
            policy=policy,
            source_evidence_hash=source_evidence_hash,
        )
        advisory = self._advisory(interpretation)
        interpretation_evidence_hash = _safe_hash(
            {
                "id": str(interpretation.id),
                "number": interpretation.interpretation_number,
                "summary": interpretation.summary,
                "suggested_category": interpretation.suggested_category,
                "missing_information": interpretation.missing_information,
                "confidence": str(interpretation.confidence),
                "warnings": interpretation.warnings or [],
                "input_hash": interpretation.input_hash,
                "configuration_hash": interpretation.configuration_hash,
            }
        )
        preview_input = DecisionEvaluationInput(
            evaluation_at=database_now,
            normalized_facts=normalized_facts,
            interpretation_id=interpretation.id,
            interpretation_version=interpretation.interpretation_number,
            interpretation_evidence_hash=interpretation_evidence_hash,
            ai_advisory=advisory,
            duplicate_candidates=tuple(candidates),
            routing_evidence_usable=command.facts.routing_evidence_usable,
            source=DecisionSource.INITIAL,
        )
        preview = evaluate_decision(preview_input, policy)
        observation_by_evidence: dict[tuple[str, uuid.UUID, str], DuplicateCandidate] = {}
        new_observations: list[DuplicateCandidate] = []
        for scored in preview.duplicate_candidates:
            key = (scored.candidate_kind.value, scored.candidate_id, scored.candidate_evidence_hash)
            observation = existing.get(key)
            if observation is None:
                observation = DuplicateCandidate(
                    id=uuid.uuid4(),
                    service_request_id=service_request.id,
                    candidate_type=scored.candidate_kind.value,
                    candidate_service_request_id=(
                        scored.candidate_id
                        if scored.candidate_kind is CandidateKind.SERVICE_REQUEST
                        else None
                    ),
                    candidate_contact_id=(
                        scored.candidate_id
                        if scored.candidate_kind is CandidateKind.CONTACT
                        else None
                    ),
                    policy_id=policy.id,
                    policy_semantic_version=policy.semantic_version,
                    policy_revision=policy.revision,
                    policy_digest=policy.content_digest,
                    source_evidence_hash=source_evidence_hash,
                    candidate_evidence_hash=scored.candidate_evidence_hash,
                    reason_codes=[item.value for item in scored.reason_codes],
                    deterministic_score=scored.score,
                    sanitized_display_evidence={"score_tier": self._score_tier(scored.score)},
                    resolution_status="Pending",
                )
                session.add(observation)
                session.flush()
                new_observations.append(observation)
            observation_by_evidence[key] = observation

        current_observation_ids = {
            observation.id for observation in observation_by_evidence.values()
        }
        replacement_by_target = {
            (
                observation.candidate_type,
                observation.candidate_service_request_id or observation.candidate_contact_id,
            ): observation
            for observation in observation_by_evidence.values()
        }
        staled_observations: list[DuplicateCandidate] = []
        for observation in prior_current_observations:
            if observation.id in current_observation_ids:
                continue
            observation.stale_at = database_now
            replacement = replacement_by_target.get(
                (
                    observation.candidate_type,
                    observation.candidate_service_request_id or observation.candidate_contact_id,
                )
            )
            if replacement is not None and replacement.id != observation.id:
                observation.superseded_by_candidate_id = replacement.id
            staled_observations.append(observation)

        bound_candidates: list[DuplicateCandidateInput] = []
        for candidate in candidates:
            key = (
                candidate.candidate_kind.value,
                candidate.candidate_id,
                candidate.candidate_evidence_hash,
            )
            observation = observation_by_evidence.get(key) or existing.get(key)
            if observation is None:
                bound_candidates.append(candidate)
                continue
            disposition = CandidateDisposition(observation.resolution_status)
            bound_candidates.append(
                candidate.model_copy(
                    update={
                        "observation_id": observation.id,
                        "disposition": disposition,
                        "eligible_record": observation.stale_at is None,
                    }
                )
            )
        final_input = preview_input.model_copy(
            update={"duplicate_candidates": tuple(bound_candidates)}
        )
        evaluation = evaluate_decision(final_input, policy)
        decision_number = (
            int(
                session.scalar(
                    select(func.coalesce(func.max(RoutingDecision.decision_number), 0)).where(
                        RoutingDecision.service_request_id == service_request.id
                    )
                )
                or 0
            )
            + 1
        )
        decision = RoutingDecision(
            id=uuid.uuid4(),
            service_request_id=service_request.id,
            decision_number=decision_number,
            policy_id=policy.id,
            policy_semantic_version=policy.semantic_version,
            policy_revision=policy.revision,
            policy_digest=policy.content_digest,
            evaluated_at=database_now,
            canonical_input_hash=evaluation.canonical_input_hash,
            canonical_input_snapshot=final_input.model_dump(mode="json"),
            ai_interpretation_id=interpretation.id,
            ai_interpretation_number=interpretation.interpretation_number,
            ai_confidence=interpretation.confidence,
            missing_information_codes=[item.value for item in evaluation.missing_information_codes],
            prior_decision_id=service_request.current_routing_decision_id,
            reviewed_fact_set_id=None,
            final_category=evaluation.final_category.value,
            final_priority=evaluation.final_priority.value,
            final_status=evaluation.final_status.value,
            final_queue=evaluation.final_queue.value,
            review_required=evaluation.review_required,
            review_reason_codes=[item.value for item in evaluation.review_reason_codes],
            category_reason_codes=[item.value for item in evaluation.category_reason_codes],
            priority_reason_codes=[item.value for item in evaluation.priority_reason_codes],
            decision_source=evaluation.source.value,
        )
        session.add(decision)
        session.flush()
        linked_observations: list[DuplicateCandidate] = []
        for position, scored in enumerate(evaluation.duplicate_candidates, start=1):
            observation_id = scored.observation_id
            if observation_id is None:
                continue
            observation = session.get(DuplicateCandidate, observation_id)
            if observation is None or observation.service_request_id != service_request.id:
                raise RuntimeError("evaluated duplicate observation is not request-owned")
            linked_observations.append(observation)
            role = (
                "StaleHistorical"
                if observation.stale_at is not None
                else (
                    "CurrentPending"
                    if observation.resolution_status == "Pending"
                    else "ResolvedHistorical"
                )
            )
            session.add(
                RoutingDecisionDuplicateCandidate(
                    routing_decision_id=decision.id,
                    position=position,
                    service_request_id=service_request.id,
                    duplicate_candidate_id=observation.id,
                    evidence_role=role,
                )
            )
        next_position = len(evaluation.duplicate_candidates) + 1
        for offset, observation in enumerate(
            sorted(staled_observations, key=lambda item: item.id.int)
        ):
            linked_observations.append(observation)
            session.add(
                RoutingDecisionDuplicateCandidate(
                    routing_decision_id=decision.id,
                    position=next_position + offset,
                    service_request_id=service_request.id,
                    duplicate_candidate_id=observation.id,
                    evidence_role="StaleHistorical",
                )
            )

        old_queue = service_request.current_queue
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
        for observation in new_observations:
            write_audit_and_optional_outbox(
                session,
                AuditSpec(
                    event_name="duplicate_candidate.created",
                    aggregate_type="DuplicateCandidate",
                    aggregate_id=observation.id,
                    aggregate_version=1,
                    actor_type="BackendService",
                    actor_reference_id=BACKEND_SERVICE_ACTOR_ID,
                    outcome="Pending",
                    correlation_id=correlation_id,
                    causation_id=reservation.command_id,
                    command_id=reservation.command_id,
                    reason_codes=tuple(observation.reason_codes),
                    safe_metadata={
                        "duplicate_candidate_id": str(observation.id),
                        "candidate_type": observation.candidate_type,
                        "score_tier": self._score_tier(observation.deterministic_score),
                        "policy": policy_evidence,
                    },
                ),
            )
        decision_evidence = {
            "service_request_id": str(service_request.id),
            "routing_decision_id": str(decision.id),
            "routing_decision_version": decision_number,
            "policy": policy_evidence,
            "source": evaluation.source.value,
            "category": evaluation.final_category.value,
            "priority": evaluation.final_priority.value,
            "status": evaluation.final_status.value,
            "queue": evaluation.final_queue.value,
            "review_required": evaluation.review_required,
            "review_reason_codes": [item.value for item in evaluation.review_reason_codes],
        }
        write_audit_and_optional_outbox(
            session,
            AuditSpec(
                event_name="routing_decision.created",
                aggregate_type="RoutingDecision",
                aggregate_id=decision.id,
                aggregate_version=decision_number,
                actor_type="BackendService",
                actor_reference_id=BACKEND_SERVICE_ACTOR_ID,
                outcome=evaluation.final_status.value,
                correlation_id=correlation_id,
                causation_id=reservation.command_id,
                command_id=reservation.command_id,
                reason_codes=tuple(
                    item.value
                    for item in (
                        *evaluation.review_reason_codes,
                        *evaluation.category_reason_codes,
                        *evaluation.priority_reason_codes,
                    )
                ),
                safe_metadata=decision_evidence,
            ),
        )
        outcome_event = {
            "ReadyForAction": "service_request.ready_for_action",
            "HumanReview": "service_request.human_review_required",
            "DuplicateReview": "service_request.duplicate_review_required",
        }[evaluation.final_status.value]
        request_evidence = {
            **decision_evidence,
            "service_request_version": service_request.version,
            "duplicate_candidate_ids": [str(item.id) for item in linked_observations],
        }
        triage_audit, _ = write_audit_and_optional_outbox(
            session,
            AuditSpec(
                event_name="service_request.triage_completed",
                aggregate_type="ServiceRequest",
                aggregate_id=service_request.id,
                aggregate_version=service_request.version,
                actor_type="BackendService",
                actor_reference_id=BACKEND_SERVICE_ACTOR_ID,
                outcome=evaluation.final_status.value,
                correlation_id=correlation_id,
                causation_id=reservation.command_id,
                command_id=reservation.command_id,
                reason_codes=tuple(item.value for item in evaluation.review_reason_codes),
                safe_metadata=request_evidence,
            ),
            OutboxSpec(
                event_type="service_request.triage_completed",
                payload=request_evidence,
            ),
        )
        write_outbox_for_audit(
            session,
            triage_audit,
            OutboxSpec(event_type=outcome_event, payload=request_evidence),
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
                    actor_type="BackendService",
                    actor_reference_id=BACKEND_SERVICE_ACTOR_ID,
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
                "result": decision_evidence,
                "versions": {"service_request": service_request.version},
            },
        )
        return CompleteTriageOutcome(
            logical_http_status=200,
            command_id=completed.command_id,
            safe_snapshot=deepcopy(completed.safe_response_snapshot),
            is_replay=False,
        )

    @staticmethod
    def _advisory(interpretation: AiInterpretation) -> AIAdvisory:
        missing = tuple(
            MISSING_CODE_VALUES[value]
            for value in interpretation.missing_information
            if isinstance(value, str) and value in MISSING_CODE_VALUES
        )
        warnings = {value for value in (interpretation.warnings or []) if isinstance(value, str)}
        possible_safety = bool(
            warnings
            & {
                "AI_POSSIBLE_SAFETY_OR_CONTINUITY",
                "POSSIBLE_SAFETY_OR_CONTINUITY",
            }
        )
        return AIAdvisory(
            confidence=Decimal(interpretation.confidence),
            suggested_category=ServiceCategory(interpretation.suggested_category),
            missing_information_codes=missing,
            possible_safety_or_continuity=possible_safety,
        )

    @staticmethod
    def _candidate_inputs(
        *,
        session: Session,
        source_request: ServiceRequest,
        normalized_facts,
        database_now,
        policy,
        source_evidence_hash: str,
    ) -> tuple[
        list[DuplicateCandidateInput],
        dict[tuple[str, uuid.UUID, str], DuplicateCandidate],
        list[DuplicateCandidate],
    ]:
        lookback = database_now - timedelta(days=policy.content.thresholds.duplicate_lookback_days)
        rows = session.execute(
            select(ServiceRequest, Contact)
            .join(Contact, Contact.id == ServiceRequest.contact_id)
            .where(
                ServiceRequest.id != source_request.id,
                ServiceRequest.created_at >= lookback,
                ServiceRequest.created_at <= database_now,
                ServiceRequest.status != "ClosedDuplicate",
            )
            .order_by(ServiceRequest.updated_at.desc(), ServiceRequest.id)
            .with_for_update(key_share=True)
        ).all()
        represented_contact_ids = {candidate.contact_id for candidate, _contact in rows}
        contact_rows = session.scalars(
            select(Contact)
            .where(
                Contact.id != source_request.contact_id,
                Contact.id.not_in(represented_contact_ids),
                Contact.updated_at >= lookback,
                Contact.updated_at <= database_now,
            )
            .order_by(Contact.updated_at.desc(), Contact.id)
            .with_for_update(key_share=True)
        ).all()
        prior_current_observations = list(
            session.scalars(
                select(DuplicateCandidate)
                .where(
                    DuplicateCandidate.service_request_id == source_request.id,
                    DuplicateCandidate.stale_at.is_(None),
                )
                .with_for_update()
            ).all()
        )
        existing_rows = (
            row
            for row in prior_current_observations
            if row.policy_id == policy.id and row.source_evidence_hash == source_evidence_hash
        )
        existing: dict[tuple[str, uuid.UUID, str], DuplicateCandidate] = {}
        for row in existing_rows:
            target_id = row.candidate_service_request_id or row.candidate_contact_id
            if target_id is not None:
                existing[(row.candidate_type, target_id, row.candidate_evidence_hash)] = row

        candidates: list[DuplicateCandidateInput] = []
        for candidate, candidate_contact in rows:
            fingerprint, token_digests = description_evidence(
                candidate.normalized_request_description
            )
            candidate_requested_service_date = None
            if candidate.current_routing_decision_id is not None:
                candidate_decision = session.scalar(
                    select(RoutingDecision).where(
                        RoutingDecision.id == candidate.current_routing_decision_id,
                        RoutingDecision.service_request_id == candidate.id,
                    )
                )
                if candidate_decision is not None:
                    try:
                        candidate_snapshot = DecisionEvaluationInput.model_validate(
                            candidate_decision.canonical_input_snapshot
                        )
                    except ValidationError:
                        candidate_snapshot = None
                    if candidate_snapshot is not None:
                        candidate_requested_service_date = (
                            candidate_snapshot.normalized_facts.requested_service_date
                        )
            candidate_evidence = {
                "candidate_request_id": str(candidate.id),
                "contact_id": str(candidate.contact_id),
                "normalized_email_digest": (
                    normalized_digest(candidate_contact.normalized_email)
                    if candidate_contact.normalized_email
                    else None
                ),
                "normalized_phone_digest": (
                    normalized_digest(candidate_contact.normalized_phone)
                    if candidate_contact.normalized_phone
                    else None
                ),
                "description_fingerprint": fingerprint,
                "description_token_digests": token_digests,
                "category": candidate.category,
                "location_digest": (
                    normalized_digest(candidate.location_context)
                    if candidate.location_context
                    else None
                ),
                "requested_service_date": candidate_requested_service_date,
                "activity_at": candidate.updated_at.isoformat(),
            }
            candidate_hash = _safe_hash(candidate_evidence)
            old = existing.get(("ServiceRequest", candidate.id, candidate_hash))
            disposition = (
                CandidateDisposition(old.resolution_status)
                if old is not None
                else CandidateDisposition.PENDING
            )
            category = ServiceCategory(candidate.category) if candidate.category else None
            candidates.append(
                DuplicateCandidateInput(
                    observation_id=old.id if old is not None else None,
                    candidate_kind=CandidateKind.SERVICE_REQUEST,
                    candidate_id=candidate.id,
                    candidate_activity_at=candidate.updated_at,
                    candidate_evidence_hash=candidate_hash,
                    disposition=disposition,
                    eligible_record=old is None or old.stale_at is None,
                    contact_id=candidate.contact_id,
                    normalized_email_digest=candidate_evidence["normalized_email_digest"],
                    normalized_phone_digest=candidate_evidence["normalized_phone_digest"],
                    description_fingerprint=fingerprint,
                    description_token_digests=token_digests,
                    final_category=category,
                    location_or_service_context_digest=candidate_evidence["location_digest"],
                    requested_service_date=candidate_requested_service_date,
                )
            )
        for candidate_contact in contact_rows:
            candidate_evidence = {
                "candidate_contact_id": str(candidate_contact.id),
                "normalized_email_digest": (
                    normalized_digest(candidate_contact.normalized_email)
                    if candidate_contact.normalized_email
                    else None
                ),
                "normalized_phone_digest": (
                    normalized_digest(candidate_contact.normalized_phone)
                    if candidate_contact.normalized_phone
                    else None
                ),
                "activity_at": candidate_contact.updated_at.isoformat(),
            }
            candidate_hash = _safe_hash(candidate_evidence)
            old = existing.get(("Contact", candidate_contact.id, candidate_hash))
            candidates.append(
                DuplicateCandidateInput(
                    observation_id=old.id if old is not None else None,
                    candidate_kind=CandidateKind.CONTACT,
                    candidate_id=candidate_contact.id,
                    candidate_activity_at=candidate_contact.updated_at,
                    candidate_evidence_hash=candidate_hash,
                    disposition=(
                        CandidateDisposition(old.resolution_status)
                        if old is not None
                        else CandidateDisposition.PENDING
                    ),
                    eligible_record=old is None or old.stale_at is None,
                    contact_id=candidate_contact.id,
                    normalized_email_digest=candidate_evidence["normalized_email_digest"],
                    normalized_phone_digest=candidate_evidence["normalized_phone_digest"],
                )
            )
        return candidates, existing, prior_current_observations

    @staticmethod
    def _score_tier(score: int) -> str:
        if score >= 80:
            return "VeryHigh"
        if score >= 60:
            return "Review"
        return "Retained"

    @staticmethod
    def _guard(
        idempotency: CommandIdempotencyService,
        reservation: NewCommandReservation,
        status: int,
        code: str,
        message: str,
        *,
        current_versions: dict[str, int] | None = None,
    ) -> CompleteTriageOutcome:
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
        return CompleteTriageOutcome(
            logical_http_status=status,
            command_id=completed.command_id,
            safe_snapshot=deepcopy(completed.safe_response_snapshot),
            is_replay=False,
        )

    @staticmethod
    def _replay(replay: CompletedCommandReplay) -> CompleteTriageOutcome:
        return CompleteTriageOutcome(
            logical_http_status=replay.logical_http_status,
            command_id=replay.command_id,
            safe_snapshot=deepcopy(replay.safe_response_snapshot),
            is_replay=True,
        )
