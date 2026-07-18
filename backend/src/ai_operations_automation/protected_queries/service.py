"""Trusted in-process BackendService access to the approved safe projections.

This module deliberately defines no FastAPI dependency, token, API key, or machine
identity parser.  A caller must already be executing inside the application process.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql import Select

from ai_operations_automation.db.models.ai_execution import AiInterpretation, IntegrationAttempt
from ai_operations_automation.db.models.decision import (
    DuplicateCandidate,
    ReviewedFactSet,
    RoutingDecision,
)
from ai_operations_automation.db.models.evidence import AuditEvent
from ai_operations_automation.db.models.intake import InboundDelivery, ServiceRequest
from ai_operations_automation.db.models.proposal import ApprovalDecision, ProposedAction
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.protected_queries.models import (
    ApprovalManagerView,
    AttemptView,
    AuditEventView,
    DuplicateCandidateView,
    InboundDeliveryView,
    InterpretationView,
    ProposalView,
    RequestListItem,
    RoutingDecisionView,
    TimelineItem,
)
from ai_operations_automation.service_requests.models import ServiceRequestResult
from ai_operations_automation.service_requests.query import query_service_request

Marker = tuple[datetime, uuid.UUID]


@dataclass(frozen=True, slots=True)
class BackendPage[T]:
    items: tuple[T, ...]
    next_marker: Marker | None


def _not_found[T](value: T | None) -> T:
    if value is None:
        raise IntakeError(404, "RESOURCE_NOT_FOUND", "The requested resource was not found.")
    return value


def _limit(value: int) -> int:
    if value < 1 or value > 100:
        raise ValueError("limit must be between 1 and 100")
    return value


def _page[T](
    session: Session,
    statement: Select[Any],
    stamp_column: Any,
    id_column: Any,
    stamp_name: str,
    projector: Callable[[Any], T],
    *,
    after: Marker | None,
    limit: int,
) -> BackendPage[T]:
    limit = _limit(limit)
    if after is not None:
        stamp, row_id = after
        statement = statement.where(
            or_(stamp_column < stamp, and_(stamp_column == stamp, id_column < row_id))
        )
    rows = session.scalars(
        statement.order_by(stamp_column.desc(), id_column.desc()).limit(limit + 1)
    ).all()
    visible = rows[:limit]
    next_marker = None
    if len(rows) > limit:
        last = visible[-1]
        next_marker = (getattr(last, stamp_name), last.id)
    return BackendPage(tuple(projector(row) for row in visible), next_marker)


def _request_item(row: ServiceRequest) -> RequestListItem:
    return RequestListItem(
        id=row.id,
        status=row.status,
        category=row.category,
        priority=row.priority,
        current_queue=row.current_queue,
        review_required=row.review_required,
        created_at=row.created_at,
        updated_at=row.updated_at,
        version=row.version,
    )


def _delivery(row: InboundDelivery) -> InboundDeliveryView:
    return InboundDeliveryView(
        id=row.id,
        processing_status=row.processing_status,
        intake_outcome=row.intake_outcome,
        original_delivery_id=row.original_delivery_id,
        service_request_id=row.logical_result_request_id or row.created_request_id,
        received_at=row.received_at,
        completed_at=row.completed_at,
        version=row.version,
        sanitized_issue_codes=sorted(
            {
                item["code"]
                for item in row.sanitized_issues or []
                if isinstance(item, dict) and isinstance(item.get("code"), str)
            }
        ),
        sanitized_error_code=row.sanitized_error_code,
    )


def _timeline(row: AuditEvent) -> TimelineItem:
    return TimelineItem(
        id=row.id,
        event_name=row.event_name,
        aggregate_type=row.aggregate_type,
        aggregate_id=row.aggregate_id,
        aggregate_version=row.aggregate_version,
        outcome=row.outcome,
        reason_codes=row.reason_codes,
        occurred_at=row.occurred_at,
    )


def _interpretation(row: AiInterpretation) -> InterpretationView:
    return InterpretationView(
        id=row.id,
        interpretation_number=row.interpretation_number,
        summary=row.summary,
        suggested_category=row.suggested_category,
        missing_information=[str(value) for value in row.missing_information],
        confidence=row.confidence,
        result_schema_version=row.result_schema_version,
        prompt_version=row.prompt_version,
        adapter_name=row.adapter_name,
        adapter_version=row.adapter_version,
        safe_provider_correlation=row.safe_provider_correlation,
        warnings=[str(value) for value in row.warnings] if row.warnings else None,
        latency_ms=row.latency_ms,
        created_at=row.created_at,
    )


def _candidate(row: DuplicateCandidate) -> DuplicateCandidateView:
    return DuplicateCandidateView(
        id=row.id,
        candidate_type=row.candidate_type,
        candidate_service_request_id=row.candidate_service_request_id,
        deterministic_score=row.deterministic_score,
        reason_codes=row.reason_codes,
        resolution_status=row.resolution_status,
        resolution_rationale_reference=row.resolution_rationale_reference,
        resolved_at=row.resolved_at,
        stale_at=row.stale_at,
        detected_at=row.detected_at,
    )


def _routing(row: RoutingDecision) -> RoutingDecisionView:
    return RoutingDecisionView(
        id=row.id,
        decision_number=row.decision_number,
        policy_id=row.policy_id,
        policy_semantic_version=row.policy_semantic_version,
        policy_revision=row.policy_revision,
        policy_digest=row.policy_digest,
        evaluated_at=row.evaluated_at,
        ai_interpretation_id=row.ai_interpretation_id,
        ai_confidence=row.ai_confidence,
        missing_information_codes=row.missing_information_codes,
        prior_decision_id=row.prior_decision_id,
        reviewed_fact_set_id=row.reviewed_fact_set_id,
        final_category=row.final_category,
        final_priority=row.final_priority,
        final_status=row.final_status,
        final_queue=row.final_queue,
        review_required=row.review_required,
        review_reason_codes=row.review_reason_codes,
        category_reason_codes=row.category_reason_codes,
        priority_reason_codes=row.priority_reason_codes,
        decision_source=row.decision_source,
        created_at=row.created_at,
    )


def _proposal(row: ProposedAction) -> ProposalView:
    return ProposalView(
        id=row.id,
        service_request_id=row.service_request_id,
        proposal_series_id=row.proposal_series_id,
        proposal_number=row.proposal_number,
        logical_operation_id=row.logical_operation_id,
        version=row.version,
        state=row.state,
        action_type=row.action_type,
        destination_kind=row.destination_kind,
        destination_value=row.destination_value,
        content=row.content,
        scheduling_window_start=row.scheduling_window_start,
        scheduling_window_end=row.scheduling_window_end,
        scheduling_notes=row.scheduling_notes,
        payload_digest=row.payload_digest,
        supersedes_id=row.supersedes_id,
        superseded_by_id=row.superseded_by_id,
        current_approval_id=row.current_approval_id,
        submitted_at=row.submitted_at,
        terminal_at=row.terminal_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _approval(row: ApprovalDecision) -> ApprovalManagerView:
    return ApprovalManagerView(
        id=row.id,
        proposal_number=row.proposal_number,
        payload_digest=row.payload_digest,
        decision=row.decision,
        role_at_decision=row.role_at_decision,
        decided_at=row.decided_at,
        rationale_recorded=row.rationale_digest is not None,
    )


def _attempt(row: IntegrationAttempt) -> AttemptView:
    return AttemptView(
        id=row.id,
        logical_operation_id=row.logical_operation_id,
        service_request_id=row.service_request_id,
        operation_kind=row.operation_kind,
        proposed_action_id=row.proposed_action_id,
        attempt_number=row.attempt_number,
        state=row.state,
        version=row.version,
        adapter_name=row.adapter_name,
        adapter_version=row.adapter_version,
        safe_provider_correlation=row.safe_provider_correlation,
        sanitized_error_code=row.sanitized_error_code,
        recovery_disposition=row.recovery_disposition,
        next_eligible_at=row.next_eligible_at,
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


def _audit(row: AuditEvent) -> AuditEventView:
    return AuditEventView(
        id=row.id,
        event_name=row.event_name,
        aggregate_type=row.aggregate_type,
        aggregate_id=row.aggregate_id,
        aggregate_version=row.aggregate_version,
        actor_type=row.actor_type,
        outcome=row.outcome,
        reason_codes=row.reason_codes,
        occurred_at=row.occurred_at,
    )


def _request_timeline_scope(request_id: uuid.UUID):
    proposal_ids = select(ProposedAction.id).where(ProposedAction.service_request_id == request_id)
    return or_(
        and_(AuditEvent.aggregate_type == "ServiceRequest", AuditEvent.aggregate_id == request_id),
        and_(
            AuditEvent.aggregate_type == "InboundDelivery",
            AuditEvent.aggregate_id.in_(
                select(InboundDelivery.id).where(
                    or_(
                        InboundDelivery.created_request_id == request_id,
                        InboundDelivery.logical_result_request_id == request_id,
                    )
                )
            ),
        ),
        and_(
            AuditEvent.aggregate_type == "DuplicateCandidate",
            AuditEvent.aggregate_id.in_(
                select(DuplicateCandidate.id).where(
                    DuplicateCandidate.service_request_id == request_id
                )
            ),
        ),
        and_(
            AuditEvent.aggregate_type == "ReviewedFactSet",
            AuditEvent.aggregate_id.in_(
                select(ReviewedFactSet.id).where(ReviewedFactSet.service_request_id == request_id)
            ),
        ),
        and_(
            AuditEvent.aggregate_type == "RoutingDecision",
            AuditEvent.aggregate_id.in_(
                select(RoutingDecision.id).where(RoutingDecision.service_request_id == request_id)
            ),
        ),
        and_(
            AuditEvent.aggregate_type == "ProposedAction",
            AuditEvent.aggregate_id.in_(proposal_ids),
        ),
        and_(
            AuditEvent.aggregate_type == "ApprovalDecision",
            AuditEvent.aggregate_id.in_(
                select(ApprovalDecision.id).where(
                    ApprovalDecision.proposed_action_id.in_(proposal_ids)
                )
            ),
        ),
        and_(
            AuditEvent.aggregate_type == "IntegrationAttempt",
            AuditEvent.aggregate_id.in_(
                select(IntegrationAttempt.id).where(
                    IntegrationAttempt.service_request_id == request_id
                )
            ),
        ),
    )


class BackendProtectedQueryService:
    """Explicit trusted BackendService projection surface; never mounted as HTTP."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_service_request(
        self, request_id: uuid.UUID, correlation_id: uuid.UUID
    ) -> ServiceRequestResult:
        response = _not_found(
            query_service_request(self._session_factory, request_id, correlation_id)
        )
        return response.result

    def list_service_requests(
        self,
        *,
        queue: str | None = None,
        priority: str | None = None,
        status: str | None = None,
        after: Marker | None = None,
        limit: int = 50,
    ) -> BackendPage[RequestListItem]:
        conditions = [
            ServiceRequest.current_queue == queue if queue else True,
            ServiceRequest.priority == priority if priority else True,
            ServiceRequest.status == status if status else True,
        ]
        with self._session_factory() as session:
            return _page(
                session,
                select(ServiceRequest).where(*conditions),
                ServiceRequest.created_at,
                ServiceRequest.id,
                "created_at",
                _request_item,
                after=after,
                limit=limit,
            )

    def get_inbound_delivery(self, delivery_id: uuid.UUID) -> InboundDeliveryView:
        with self._session_factory() as session:
            return _delivery(_not_found(session.get(InboundDelivery, delivery_id)))

    def get_request_timeline(
        self, request_id: uuid.UUID, *, after: Marker | None = None, limit: int = 50
    ) -> BackendPage[TimelineItem]:
        with self._session_factory() as session:
            _not_found(session.get(ServiceRequest, request_id))
            return _page(
                session,
                select(AuditEvent).where(_request_timeline_scope(request_id)),
                AuditEvent.occurred_at,
                AuditEvent.id,
                "occurred_at",
                _timeline,
                after=after,
                limit=limit,
            )

    def list_ai_interpretations(
        self, request_id: uuid.UUID, *, after: Marker | None = None, limit: int = 50
    ) -> BackendPage[InterpretationView]:
        with self._session_factory() as session:
            _not_found(session.get(ServiceRequest, request_id))
            return _page(
                session,
                select(AiInterpretation).where(AiInterpretation.service_request_id == request_id),
                AiInterpretation.created_at,
                AiInterpretation.id,
                "created_at",
                _interpretation,
                after=after,
                limit=limit,
            )

    def list_duplicate_candidates(
        self, request_id: uuid.UUID, *, after: Marker | None = None, limit: int = 50
    ) -> BackendPage[DuplicateCandidateView]:
        with self._session_factory() as session:
            _not_found(session.get(ServiceRequest, request_id))
            return _page(
                session,
                select(DuplicateCandidate).where(
                    DuplicateCandidate.service_request_id == request_id
                ),
                DuplicateCandidate.detected_at,
                DuplicateCandidate.id,
                "detected_at",
                _candidate,
                after=after,
                limit=limit,
            )

    def list_routing_decisions(
        self, request_id: uuid.UUID, *, after: Marker | None = None, limit: int = 50
    ) -> BackendPage[RoutingDecisionView]:
        with self._session_factory() as session:
            _not_found(session.get(ServiceRequest, request_id))
            return _page(
                session,
                select(RoutingDecision).where(RoutingDecision.service_request_id == request_id),
                RoutingDecision.created_at,
                RoutingDecision.id,
                "created_at",
                _routing,
                after=after,
                limit=limit,
            )

    def list_proposed_actions(
        self, request_id: uuid.UUID, *, after: Marker | None = None, limit: int = 50
    ) -> BackendPage[ProposalView]:
        with self._session_factory() as session:
            _not_found(session.get(ServiceRequest, request_id))
            return _page(
                session,
                select(ProposedAction).where(ProposedAction.service_request_id == request_id),
                ProposedAction.created_at,
                ProposedAction.id,
                "created_at",
                _proposal,
                after=after,
                limit=limit,
            )

    def get_proposed_action(self, action_id: uuid.UUID) -> ProposalView:
        with self._session_factory() as session:
            return _proposal(_not_found(session.get(ProposedAction, action_id)))

    def list_proposal_approvals(
        self, action_id: uuid.UUID, *, after: Marker | None = None, limit: int = 50
    ) -> BackendPage[ApprovalManagerView]:
        with self._session_factory() as session:
            _not_found(session.get(ProposedAction, action_id))
            return _page(
                session,
                select(ApprovalDecision).where(ApprovalDecision.proposed_action_id == action_id),
                ApprovalDecision.decided_at,
                ApprovalDecision.id,
                "decided_at",
                _approval,
                after=after,
                limit=limit,
            )

    def list_proposal_integration_attempts(
        self, action_id: uuid.UUID, *, after: Marker | None = None, limit: int = 50
    ) -> BackendPage[AttemptView]:
        with self._session_factory() as session:
            proposal = _not_found(session.get(ProposedAction, action_id))
            return _page(
                session,
                select(IntegrationAttempt).where(
                    IntegrationAttempt.logical_operation_id == proposal.logical_operation_id,
                    IntegrationAttempt.service_request_id == proposal.service_request_id,
                ),
                IntegrationAttempt.created_at,
                IntegrationAttempt.id,
                "created_at",
                _attempt,
                after=after,
                limit=limit,
            )

    def get_integration_attempt(self, attempt_id: uuid.UUID) -> AttemptView:
        with self._session_factory() as session:
            return _attempt(_not_found(session.get(IntegrationAttempt, attempt_id)))

    def list_audit_events(
        self,
        aggregate_type: str,
        aggregate_id: uuid.UUID,
        *,
        after: Marker | None = None,
        limit: int = 50,
    ) -> BackendPage[AuditEventView]:
        with self._session_factory() as session:
            return _page(
                session,
                select(AuditEvent).where(
                    AuditEvent.aggregate_type == aggregate_type,
                    AuditEvent.aggregate_id == aggregate_id,
                ),
                AuditEvent.occurred_at,
                AuditEvent.id,
                "occurred_at",
                _audit,
                after=after,
                limit=limit,
            )
