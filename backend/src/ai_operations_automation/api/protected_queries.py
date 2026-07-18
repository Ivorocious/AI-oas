"""The approved explicit Phase 2 protected read projections.

The queries deliberately use small, purpose-specific projections.  They do not
reuse command services, expose ORM objects, or make any state transition.
"""

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ai_operations_automation.api.correlation import resolve_request_correlation
from ai_operations_automation.auth.dependencies import (
    CommandAuthority,
    authenticated_query_principal,
    bearer,
    require_human_query,
)
from ai_operations_automation.auth.models import AuthenticatedHuman
from ai_operations_automation.db.dependencies import get_session_factory
from ai_operations_automation.db.models.ai_execution import AiInterpretation, IntegrationAttempt
from ai_operations_automation.db.models.decision import (
    DuplicateCandidate,
    ReviewedFactSet,
    RoutingDecision,
)
from ai_operations_automation.db.models.evidence import AuditEvent
from ai_operations_automation.db.models.intake import InboundDelivery, ServiceRequest
from ai_operations_automation.db.models.proposal import ApprovalDecision, ProposedAction
from ai_operations_automation.intake.errors import ErrorEnvelope, IntakeError
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService
from ai_operations_automation.protected_queries.cursors import decode_cursor, encode_cursor
from ai_operations_automation.protected_queries.models import (
    ApprovalManagerView,
    ApprovalsResponse,
    ApprovalsResult,
    ApprovalView,
    AttemptResponse,
    AttemptsResponse,
    AttemptsResult,
    AttemptView,
    AuditEventsResponse,
    AuditEventsResult,
    AuditEventView,
    DuplicateCandidatesResponse,
    DuplicateCandidatesResult,
    DuplicateCandidateView,
    InboundDeliveryResponse,
    InboundDeliveryView,
    InterpretationsResponse,
    InterpretationsResult,
    InterpretationView,
    ManagerApprovalsResponse,
    ManagerApprovalsResult,
    PageInfo,
    ProposalListResponse,
    ProposalListResult,
    ProposalResponse,
    ProposalView,
    RequestListItem,
    RequestListResponse,
    RequestListResult,
    RoutingDecisionsResponse,
    RoutingDecisionsResult,
    RoutingDecisionView,
    TimelineItem,
    TimelineResponse,
    TimelineResult,
    WorkflowAttemptResponse,
    WorkflowAttemptsResponse,
    WorkflowAttemptsResult,
    WorkflowAttemptView,
    WorkflowProposalResponse,
    WorkflowProposalView,
)

router = APIRouter(dependencies=[Depends(bearer)])

_CREDENTIAL_SECURITY_EVENT_NAMES = {"integration_attempt.callback_credential_replaced"}


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise IntakeError(
            404, "RESOURCE_NOT_FOUND", "The requested resource was not found."
        ) from None


def _limit(value: int) -> int:
    if value < 1 or value > 100:
        raise IntakeError(400, "INVALID_CURSOR", "The cursor is invalid.")
    return value


def _cursor_key(request: Request) -> bytes | None:
    value = request.app.state.settings.protected_query_cursor_signing_key
    return value.get_secret_value().encode() if value is not None else None


def _principal_scope(principal: CommandAuthority) -> str:
    if isinstance(principal, AuthenticatedHuman):
        return f"human:{principal.role}"
    return f"workflow:{principal.environment}:{principal.stable_service_id}"


def _marker(
    request: Request,
    principal: CommandAuthority,
    cursor: str | None,
    kind: str,
    filters: dict[str, str | None],
    ordering: str,
) -> tuple[datetime, str] | None:
    return decode_cursor(
        _cursor_key(request),
        cursor,
        kind,
        filters,
        ordering=ordering,
        principal_scope=_principal_scope(principal),
    )


def _next_cursor(
    request: Request,
    principal: CommandAuthority,
    kind: str,
    filters: dict[str, str | None],
    ordering: str,
    stamp: datetime,
    row_id: uuid.UUID,
) -> str:
    return encode_cursor(
        _cursor_key(request),
        kind,
        filters,
        stamp,
        str(row_id),
        ordering=ordering,
        principal_scope=_principal_scope(principal),
    )


def _safe_issues(value: list[dict] | None) -> list[str]:
    return sorted(
        {
            issue["code"]
            for issue in value or []
            if isinstance(issue, dict) and isinstance(issue.get("code"), str)
        }
    )


def _response(model, correlation_id: uuid.UUID) -> JSONResponse:
    return JSONResponse(
        content=model.model_dump(mode="json"), headers={"X-Correlation-ID": str(correlation_id)}
    )


def _not_found(value: object | None) -> object:
    if value is None:
        raise IntakeError(404, "RESOURCE_NOT_FOUND", "The requested resource was not found.")
    return value


def _run[T](request: Request, projection: Callable[[Session], T]) -> T:
    try:
        with get_session_factory(request)() as session:
            return projection(session)
    except IntakeError:
        raise
    except SQLAlchemyError as exc:
        raise IntakeError(
            503, "DEPENDENCY_UNAVAILABLE", "A required dependency is unavailable.", True
        ) from exc
    except Exception as exc:
        raise IntakeError(
            500, "INTERNAL_ERROR", "The request could not be completed safely."
        ) from exc


def _request_exists(session: Session, request_id: uuid.UUID) -> None:
    _not_found(session.scalar(select(ServiceRequest.id).where(ServiceRequest.id == request_id)))


def _assigned_attempts(principal: CommandAuthority):
    """Return a narrow assignment predicate for WorkflowService query scope."""
    assert isinstance(principal, AuthenticatedWorkflowService)
    return and_(
        IntegrationAttempt.assigned_workflow_service == principal.stable_service_id,
        IntegrationAttempt.workflow_environment == principal.environment,
    )


def _request_timeline_scope(request_id: uuid.UUID):
    """Select audit rows belonging to the complete persisted request graph."""
    proposal_ids = select(ProposedAction.id).where(ProposedAction.service_request_id == request_id)
    return or_(
        and_(
            AuditEvent.aggregate_type == "ServiceRequest",
            AuditEvent.aggregate_id == request_id,
        ),
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


def _request_list_item(row: ServiceRequest) -> RequestListItem:
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


def _workflow_attempt(row: IntegrationAttempt) -> WorkflowAttemptView:
    return WorkflowAttemptView(
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
        callback_authorization_deadline=row.callback_authorization_deadline,
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
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


def _workflow_proposal(row: ProposedAction) -> WorkflowProposalView:
    return WorkflowProposalView(
        id=row.id,
        service_request_id=row.service_request_id,
        logical_operation_id=row.logical_operation_id,
        proposal_number=row.proposal_number,
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
        approval_valid=row.state in {"Approved", "PendingExecution", "Executed"}
        and row.current_approval_id is not None,
    )


@router.get(
    "/api/v1/service-requests",
    response_model=RequestListResponse,
    responses={
        400: {"model": ErrorEnvelope},
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        500: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)
def list_service_requests(
    request: Request,
    correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
    human: Annotated[AuthenticatedHuman, Depends(require_human_query)],
    queue: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50),
) -> JSONResponse:
    limit = _limit(limit)
    filters = {"queue": queue, "priority": priority, "status": status}
    ordering = "created_at:desc,id:desc"
    marker = _marker(request, human, cursor, "service-requests", filters, ordering)

    def projection(session: Session) -> RequestListResponse:
        conditions = [
            ServiceRequest.current_queue == queue if queue else True,
            ServiceRequest.priority == priority if priority else True,
            ServiceRequest.status == status if status else True,
        ]
        if marker:
            stamp, row_id = marker
            conditions.append(
                or_(
                    ServiceRequest.created_at < stamp,
                    and_(ServiceRequest.created_at == stamp, ServiceRequest.id < uuid.UUID(row_id)),
                )
            )
        rows = session.scalars(
            select(ServiceRequest)
            .where(*conditions)
            .order_by(ServiceRequest.created_at.desc(), ServiceRequest.id.desc())
            .limit(limit + 1)
        ).all()
        visible, extra = rows[:limit], len(rows) > limit
        next_cursor = (
            _next_cursor(
                request,
                human,
                "service-requests",
                filters,
                ordering,
                visible[-1].created_at,
                visible[-1].id,
            )
            if extra
            else None
        )
        return RequestListResponse(
            correlation_id=correlation_id,
            result=RequestListResult(
                items=[_request_list_item(row) for row in visible],
                page=PageInfo(next_cursor=next_cursor),
            ),
        )

    return _response(_run(request, projection), correlation_id)


@router.get(
    "/api/v1/inbound-deliveries/{delivery_id}",
    response_model=InboundDeliveryResponse,
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        500: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)
def get_inbound_delivery(
    delivery_id: str,
    request: Request,
    correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
    _human: Annotated[AuthenticatedHuman, Depends(require_human_query)],
) -> JSONResponse:
    parsed = _uuid(delivery_id)

    def projection(session: Session) -> InboundDeliveryResponse:
        row = _not_found(session.get(InboundDelivery, parsed))
        assert isinstance(row, InboundDelivery)
        return InboundDeliveryResponse(
            correlation_id=correlation_id,
            result=InboundDeliveryView(
                id=row.id,
                processing_status=row.processing_status,
                intake_outcome=row.intake_outcome,
                original_delivery_id=row.original_delivery_id,
                service_request_id=row.logical_result_request_id or row.created_request_id,
                received_at=row.received_at,
                completed_at=row.completed_at,
                version=row.version,
                sanitized_issue_codes=_safe_issues(row.sanitized_issues),
                sanitized_error_code=row.sanitized_error_code,
            ),
        )

    return _response(_run(request, projection), correlation_id)


@router.get(
    "/api/v1/service-requests/{request_id}/timeline",
    response_model=TimelineResponse,
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        500: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)
def get_timeline(
    request_id: str,
    request: Request,
    correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
    human: Annotated[AuthenticatedHuman, Depends(require_human_query)],
    cursor: str | None = None,
    limit: int = Query(default=50),
) -> JSONResponse:
    parsed = _uuid(request_id)
    limit = _limit(limit)
    filters = {"request_id": str(parsed)}
    ordering = "occurred_at:desc,id:desc"
    marker = _marker(request, human, cursor, "request-timeline", filters, ordering)

    def projection(session: Session) -> TimelineResponse:
        _request_exists(session, parsed)
        conditions = [_request_timeline_scope(parsed)]
        if human.role != "Administrator":
            conditions.append(AuditEvent.event_name.not_in(_CREDENTIAL_SECURITY_EVENT_NAMES))
        if marker:
            stamp, row_id = marker
            conditions.append(
                or_(
                    AuditEvent.occurred_at < stamp,
                    and_(AuditEvent.occurred_at == stamp, AuditEvent.id < uuid.UUID(row_id)),
                )
            )
        rows = session.scalars(
            select(AuditEvent)
            .where(*conditions)
            .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
            .limit(limit + 1)
        ).all()
        visible, extra = rows[:limit], len(rows) > limit
        next_cursor = (
            _next_cursor(
                request,
                human,
                "request-timeline",
                filters,
                ordering,
                visible[-1].occurred_at,
                visible[-1].id,
            )
            if extra
            else None
        )
        return TimelineResponse(
            correlation_id=correlation_id,
            result=TimelineResult(
                items=[
                    TimelineItem(
                        id=row.id,
                        event_name=row.event_name,
                        aggregate_type=row.aggregate_type,
                        aggregate_id=row.aggregate_id,
                        aggregate_version=row.aggregate_version,
                        outcome=row.outcome,
                        reason_codes=row.reason_codes,
                        occurred_at=row.occurred_at,
                    )
                    for row in visible
                ],
                page=PageInfo(next_cursor=next_cursor),
            ),
        )

    return _response(_run(request, projection), correlation_id)


@router.get(
    "/api/v1/service-requests/{request_id}/ai-interpretations",
    response_model=InterpretationsResponse,
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        500: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)
def list_interpretations(
    request_id: str,
    request: Request,
    correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
    principal: Annotated[CommandAuthority, Depends(authenticated_query_principal)],
    cursor: str | None = None,
    limit: int = Query(default=50),
) -> JSONResponse:
    parsed = _uuid(request_id)
    limit = _limit(limit)
    filters = {"request_id": str(parsed)}
    ordering = "created_at:desc,id:desc"
    marker = _marker(request, principal, cursor, "ai-interpretations", filters, ordering)

    def projection(session: Session) -> InterpretationsResponse:
        statement = select(AiInterpretation).where(AiInterpretation.service_request_id == parsed)
        if isinstance(principal, AuthenticatedWorkflowService):
            _not_found(
                session.scalar(
                    select(IntegrationAttempt.id).where(
                        IntegrationAttempt.service_request_id == parsed,
                        IntegrationAttempt.operation_kind == "AIInterpretation",
                        _assigned_attempts(principal),
                    )
                )
            )
            statement = statement.join(
                IntegrationAttempt,
                IntegrationAttempt.id == AiInterpretation.producing_attempt_id,
            ).where(_assigned_attempts(principal))
        else:
            _request_exists(session, parsed)
        if marker:
            stamp, row_id = marker
            statement = statement.where(
                or_(
                    AiInterpretation.created_at < stamp,
                    and_(
                        AiInterpretation.created_at == stamp,
                        AiInterpretation.id < uuid.UUID(row_id),
                    ),
                )
            )
        rows = session.scalars(
            statement.order_by(
                AiInterpretation.created_at.desc(), AiInterpretation.id.desc()
            ).limit(limit + 1)
        ).all()
        visible, extra = rows[:limit], len(rows) > limit
        next_cursor = (
            _next_cursor(
                request,
                principal,
                "ai-interpretations",
                filters,
                ordering,
                visible[-1].created_at,
                visible[-1].id,
            )
            if extra
            else None
        )
        return InterpretationsResponse(
            correlation_id=correlation_id,
            result=InterpretationsResult(
                items=[
                    InterpretationView(
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
                    for row in visible
                ],
                page=PageInfo(next_cursor=next_cursor),
            ),
        )

    return _response(_run(request, projection), correlation_id)


@router.get(
    "/api/v1/service-requests/{request_id}/duplicate-candidates",
    response_model=DuplicateCandidatesResponse,
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        500: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)
def list_duplicate_candidates(
    request_id: str,
    request: Request,
    correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
    human: Annotated[AuthenticatedHuman, Depends(require_human_query)],
    cursor: str | None = None,
    limit: int = Query(default=50),
) -> JSONResponse:
    parsed = _uuid(request_id)
    limit = _limit(limit)
    filters = {"request_id": str(parsed)}
    ordering = "detected_at:desc,id:desc"
    marker = _marker(request, human, cursor, "duplicate-candidates", filters, ordering)

    def projection(session: Session) -> DuplicateCandidatesResponse:
        _request_exists(session, parsed)
        conditions = [DuplicateCandidate.service_request_id == parsed]
        if marker:
            stamp, row_id = marker
            conditions.append(
                or_(
                    DuplicateCandidate.detected_at < stamp,
                    and_(
                        DuplicateCandidate.detected_at == stamp,
                        DuplicateCandidate.id < uuid.UUID(row_id),
                    ),
                )
            )
        rows = session.scalars(
            select(DuplicateCandidate)
            .where(*conditions)
            .order_by(DuplicateCandidate.detected_at.desc(), DuplicateCandidate.id.desc())
            .limit(limit + 1)
        ).all()
        visible, extra = rows[:limit], len(rows) > limit
        next_cursor = (
            _next_cursor(
                request,
                human,
                "duplicate-candidates",
                filters,
                ordering,
                visible[-1].detected_at,
                visible[-1].id,
            )
            if extra
            else None
        )
        return DuplicateCandidatesResponse(
            correlation_id=correlation_id,
            result=DuplicateCandidatesResult(
                items=[
                    DuplicateCandidateView(
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
                    for row in visible
                ],
                page=PageInfo(next_cursor=next_cursor),
            ),
        )

    return _response(_run(request, projection), correlation_id)


@router.get(
    "/api/v1/service-requests/{request_id}/routing-decisions",
    response_model=RoutingDecisionsResponse,
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        500: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)
def list_routing_decisions(
    request_id: str,
    request: Request,
    correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
    human: Annotated[AuthenticatedHuman, Depends(require_human_query)],
    cursor: str | None = None,
    limit: int = Query(default=50),
) -> JSONResponse:
    parsed = _uuid(request_id)
    limit = _limit(limit)
    filters = {"request_id": str(parsed)}
    ordering = "created_at:desc,id:desc"
    marker = _marker(request, human, cursor, "routing-decisions", filters, ordering)

    def projection(session: Session) -> RoutingDecisionsResponse:
        _request_exists(session, parsed)
        conditions = [RoutingDecision.service_request_id == parsed]
        if marker:
            stamp, row_id = marker
            conditions.append(
                or_(
                    RoutingDecision.created_at < stamp,
                    and_(
                        RoutingDecision.created_at == stamp,
                        RoutingDecision.id < uuid.UUID(row_id),
                    ),
                )
            )
        rows = session.scalars(
            select(RoutingDecision)
            .where(*conditions)
            .order_by(RoutingDecision.created_at.desc(), RoutingDecision.id.desc())
            .limit(limit + 1)
        ).all()
        visible, extra = rows[:limit], len(rows) > limit
        next_cursor = (
            _next_cursor(
                request,
                human,
                "routing-decisions",
                filters,
                ordering,
                visible[-1].created_at,
                visible[-1].id,
            )
            if extra
            else None
        )
        return RoutingDecisionsResponse(
            correlation_id=correlation_id,
            result=RoutingDecisionsResult(
                items=[
                    RoutingDecisionView(
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
                    for row in visible
                ],
                page=PageInfo(next_cursor=next_cursor),
            ),
        )

    return _response(_run(request, projection), correlation_id)


@router.get(
    "/api/v1/service-requests/{request_id}/proposed-actions",
    response_model=ProposalListResponse,
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        500: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)
def list_proposals(
    request_id: str,
    request: Request,
    correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
    human: Annotated[AuthenticatedHuman, Depends(require_human_query)],
    cursor: str | None = None,
    limit: int = Query(default=50),
) -> JSONResponse:
    parsed = _uuid(request_id)
    limit = _limit(limit)
    filters = {"request_id": str(parsed)}
    ordering = "created_at:desc,id:desc"
    marker = _marker(request, human, cursor, "proposed-actions", filters, ordering)

    def projection(session: Session) -> ProposalListResponse:
        _request_exists(session, parsed)
        conditions = [ProposedAction.service_request_id == parsed]
        if marker:
            stamp, row_id = marker
            conditions.append(
                or_(
                    ProposedAction.created_at < stamp,
                    and_(ProposedAction.created_at == stamp, ProposedAction.id < uuid.UUID(row_id)),
                )
            )
        rows = session.scalars(
            select(ProposedAction)
            .where(*conditions)
            .order_by(ProposedAction.created_at.desc(), ProposedAction.id.desc())
            .limit(limit + 1)
        ).all()
        visible, extra = rows[:limit], len(rows) > limit
        next_cursor = (
            _next_cursor(
                request,
                human,
                "proposed-actions",
                filters,
                ordering,
                visible[-1].created_at,
                visible[-1].id,
            )
            if extra
            else None
        )
        return ProposalListResponse(
            correlation_id=correlation_id,
            result=ProposalListResult(
                items=[_proposal(row) for row in visible],
                page=PageInfo(next_cursor=next_cursor),
            ),
        )

    return _response(_run(request, projection), correlation_id)


@router.get(
    "/api/v1/proposed-actions/{action_id}",
    response_model=ProposalResponse | WorkflowProposalResponse,
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        500: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)
def get_proposal(
    action_id: str,
    request: Request,
    correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
    principal: Annotated[CommandAuthority, Depends(authenticated_query_principal)],
) -> JSONResponse:
    parsed = _uuid(action_id)

    def projection(session: Session) -> ProposalResponse | WorkflowProposalResponse:
        if isinstance(principal, AuthenticatedWorkflowService):
            _not_found(
                session.scalar(
                    select(IntegrationAttempt.id).where(
                        IntegrationAttempt.proposed_action_id == parsed,
                        _assigned_attempts(principal),
                    )
                )
            )
            row = _not_found(session.get(ProposedAction, parsed))
            assert isinstance(row, ProposedAction)
            return WorkflowProposalResponse(
                correlation_id=correlation_id,
                result=_workflow_proposal(row),
            )
        return ProposalResponse(
            correlation_id=correlation_id,
            result=_proposal(_not_found(session.get(ProposedAction, parsed))),
        )

    return _response(
        _run(request, projection),
        correlation_id,
    )


@router.get(
    "/api/v1/proposed-actions/{action_id}/approvals",
    response_model=ApprovalsResponse | ManagerApprovalsResponse,
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        500: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)
def list_approvals(
    action_id: str,
    request: Request,
    correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
    human: Annotated[AuthenticatedHuman, Depends(require_human_query)],
    cursor: str | None = None,
    limit: int = Query(default=50),
) -> JSONResponse:
    parsed = _uuid(action_id)
    limit = _limit(limit)
    filters = {"action_id": str(parsed)}
    ordering = "decided_at:desc,id:desc"
    marker = _marker(request, human, cursor, "proposal-approvals", filters, ordering)

    def projection(session: Session) -> ApprovalsResponse | ManagerApprovalsResponse:
        _not_found(session.get(ProposedAction, parsed))
        conditions = [ApprovalDecision.proposed_action_id == parsed]
        if marker:
            stamp, row_id = marker
            conditions.append(
                or_(
                    ApprovalDecision.decided_at < stamp,
                    and_(
                        ApprovalDecision.decided_at == stamp,
                        ApprovalDecision.id < uuid.UUID(row_id),
                    ),
                )
            )
        rows = session.scalars(
            select(ApprovalDecision)
            .where(*conditions)
            .order_by(ApprovalDecision.decided_at.desc(), ApprovalDecision.id.desc())
            .limit(limit + 1)
        ).all()
        visible, extra = rows[:limit], len(rows) > limit
        next_cursor = (
            _next_cursor(
                request,
                human,
                "proposal-approvals",
                filters,
                ordering,
                visible[-1].decided_at,
                visible[-1].id,
            )
            if extra
            else None
        )
        common = [
            {
                "id": row.id,
                "proposal_number": row.proposal_number,
                "payload_digest": row.payload_digest,
                "decision": row.decision,
                "role_at_decision": row.role_at_decision,
                "decided_at": row.decided_at,
            }
            for row in visible
        ]
        if human.role == "OperationsAgent":
            return ApprovalsResponse(
                correlation_id=correlation_id,
                result=ApprovalsResult(
                    items=[ApprovalView(**value) for value in common],
                    page=PageInfo(next_cursor=next_cursor),
                ),
            )
        return ManagerApprovalsResponse(
            correlation_id=correlation_id,
            result=ManagerApprovalsResult(
                items=[
                    ApprovalManagerView(
                        **value,
                        rationale_recorded=row.rationale_digest is not None,
                    )
                    for value, row in zip(common, visible, strict=True)
                ],
                page=PageInfo(next_cursor=next_cursor),
            ),
        )

    return _response(_run(request, projection), correlation_id)


@router.get(
    "/api/v1/proposed-actions/{action_id}/integration-attempts",
    response_model=AttemptsResponse | WorkflowAttemptsResponse,
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        500: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)
def list_proposal_attempts(
    action_id: str,
    request: Request,
    correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
    principal: Annotated[CommandAuthority, Depends(authenticated_query_principal)],
    cursor: str | None = None,
    limit: int = Query(default=50),
) -> JSONResponse:
    parsed = _uuid(action_id)
    limit = _limit(limit)
    filters = {"action_id": str(parsed)}
    ordering = "created_at:desc,id:desc"
    marker = _marker(request, principal, cursor, "proposal-attempts", filters, ordering)

    def projection(session: Session) -> AttemptsResponse | WorkflowAttemptsResponse:
        proposal = _not_found(session.get(ProposedAction, parsed))
        assert isinstance(proposal, ProposedAction)
        statement = select(IntegrationAttempt).where(
            IntegrationAttempt.logical_operation_id == proposal.logical_operation_id,
            IntegrationAttempt.service_request_id == proposal.service_request_id,
        )
        if isinstance(principal, AuthenticatedWorkflowService):
            assignment = session.scalar(
                select(IntegrationAttempt)
                .where(
                    IntegrationAttempt.proposed_action_id == parsed,
                    _assigned_attempts(principal),
                )
                .order_by(IntegrationAttempt.created_at.desc(), IntegrationAttempt.id.desc())
            )
            _not_found(assignment)
            assert isinstance(assignment, IntegrationAttempt)
            statement = statement.where(
                _assigned_attempts(principal),
                IntegrationAttempt.proposed_action_id == parsed,
                IntegrationAttempt.logical_operation_id == assignment.logical_operation_id,
                IntegrationAttempt.service_request_id == assignment.service_request_id,
            )
        if marker:
            stamp, row_id = marker
            statement = statement.where(
                or_(
                    IntegrationAttempt.created_at < stamp,
                    and_(
                        IntegrationAttempt.created_at == stamp,
                        IntegrationAttempt.id < uuid.UUID(row_id),
                    ),
                )
            )
        rows = session.scalars(
            statement.order_by(
                IntegrationAttempt.created_at.desc(), IntegrationAttempt.id.desc()
            ).limit(limit + 1)
        ).all()
        visible, extra = rows[:limit], len(rows) > limit
        next_cursor = (
            _next_cursor(
                request,
                principal,
                "proposal-attempts",
                filters,
                ordering,
                visible[-1].created_at,
                visible[-1].id,
            )
            if extra
            else None
        )
        if isinstance(principal, AuthenticatedWorkflowService):
            return WorkflowAttemptsResponse(
                correlation_id=correlation_id,
                result=WorkflowAttemptsResult(
                    items=[_workflow_attempt(row) for row in visible],
                    page=PageInfo(next_cursor=next_cursor),
                ),
            )
        return AttemptsResponse(
            correlation_id=correlation_id,
            result=AttemptsResult(
                items=[_attempt(row) for row in visible],
                page=PageInfo(next_cursor=next_cursor),
            ),
        )

    return _response(_run(request, projection), correlation_id)


@router.get(
    "/api/v1/integration-attempts/{attempt_id}",
    response_model=AttemptResponse | WorkflowAttemptResponse,
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        500: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)
def get_attempt(
    attempt_id: str,
    request: Request,
    correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
    principal: Annotated[CommandAuthority, Depends(authenticated_query_principal)],
) -> JSONResponse:
    parsed = _uuid(attempt_id)

    def projection(session: Session) -> AttemptResponse | WorkflowAttemptResponse:
        statement = select(IntegrationAttempt).where(IntegrationAttempt.id == parsed)
        if isinstance(principal, AuthenticatedWorkflowService):
            statement = statement.where(_assigned_attempts(principal))
        row = _not_found(session.scalar(statement))
        assert isinstance(row, IntegrationAttempt)
        if isinstance(principal, AuthenticatedWorkflowService):
            return WorkflowAttemptResponse(
                correlation_id=correlation_id,
                result=_workflow_attempt(row),
            )
        return AttemptResponse(correlation_id=correlation_id, result=_attempt(row))

    return _response(
        _run(request, projection),
        correlation_id,
    )


@router.get(
    "/api/v1/audit-events",
    response_model=AuditEventsResponse,
    responses={
        400: {"model": ErrorEnvelope},
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        500: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)
def list_audit_events(
    request: Request,
    correlation_id: Annotated[uuid.UUID, Depends(resolve_request_correlation)],
    human: Annotated[AuthenticatedHuman, Depends(require_human_query)],
    aggregate_type: str,
    aggregate_id: str,
    cursor: str | None = None,
    limit: int = Query(default=50),
) -> JSONResponse:
    if human.role == "OperationsAgent":
        raise IntakeError(403, "FORBIDDEN", "The requested operation is not permitted.")
    parsed = _uuid(aggregate_id)
    limit = _limit(limit)
    filters = {"aggregate_type": aggregate_type, "aggregate_id": str(parsed)}
    ordering = "occurred_at:desc,id:desc"
    marker = _marker(request, human, cursor, "audit-events", filters, ordering)

    def projection(session: Session) -> AuditEventsResponse:
        conditions = [
            AuditEvent.aggregate_type == aggregate_type,
            AuditEvent.aggregate_id == parsed,
        ]
        if human.role == "ManagerApprover":
            conditions.append(AuditEvent.event_name.not_in(_CREDENTIAL_SECURITY_EVENT_NAMES))
        if marker:
            stamp, row_id = marker
            conditions.append(
                or_(
                    AuditEvent.occurred_at < stamp,
                    and_(AuditEvent.occurred_at == stamp, AuditEvent.id < uuid.UUID(row_id)),
                )
            )
        rows = session.scalars(
            select(AuditEvent)
            .where(*conditions)
            .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
            .limit(limit + 1)
        ).all()
        visible, extra = rows[:limit], len(rows) > limit
        next_cursor = (
            _next_cursor(
                request,
                human,
                "audit-events",
                filters,
                ordering,
                visible[-1].occurred_at,
                visible[-1].id,
            )
            if extra
            else None
        )
        return AuditEventsResponse(
            correlation_id=correlation_id,
            result=AuditEventsResult(
                items=[
                    AuditEventView(
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
                    for row in visible
                ],
                page=PageInfo(next_cursor=next_cursor),
            ),
        )

    return _response(_run(request, projection), correlation_id)
