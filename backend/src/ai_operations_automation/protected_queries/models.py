"""Closed response schemas for protected operational projections."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PageInfo(_Closed):
    next_cursor: str | None


class RequestListItem(_Closed):
    id: uuid.UUID
    status: str
    category: str | None
    priority: str | None
    current_queue: str | None
    review_required: bool | None
    created_at: datetime
    updated_at: datetime
    version: int


class RequestListResult(_Closed):
    items: list[RequestListItem]
    page: PageInfo


class RequestListResponse(_Closed):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    result: RequestListResult


class InboundDeliveryView(_Closed):
    id: uuid.UUID
    processing_status: str
    intake_outcome: str | None
    original_delivery_id: uuid.UUID | None
    service_request_id: uuid.UUID | None
    received_at: datetime
    completed_at: datetime | None
    version: int
    sanitized_issue_codes: list[str]
    sanitized_error_code: str | None


class InboundDeliveryResponse(_Closed):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    result: InboundDeliveryView


class TimelineItem(_Closed):
    id: uuid.UUID
    event_name: str
    aggregate_type: str
    aggregate_id: uuid.UUID
    aggregate_version: int
    outcome: str
    reason_codes: list[str]
    occurred_at: datetime


class TimelineResult(_Closed):
    items: list[TimelineItem]
    page: PageInfo


class TimelineResponse(_Closed):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    result: TimelineResult


class InterpretationView(_Closed):
    id: uuid.UUID
    interpretation_number: int
    summary: str
    suggested_category: str
    missing_information: list[str]
    confidence: Decimal
    result_schema_version: str
    prompt_version: str
    adapter_name: str
    adapter_version: str
    safe_provider_correlation: str | None
    warnings: list[str] | None
    latency_ms: int | None
    created_at: datetime


class InterpretationsResult(_Closed):
    items: list[InterpretationView]
    page: PageInfo


class InterpretationsResponse(_Closed):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    result: InterpretationsResult


class DuplicateCandidateView(_Closed):
    id: uuid.UUID
    candidate_type: str
    candidate_service_request_id: uuid.UUID | None
    deterministic_score: int
    reason_codes: list[str]
    resolution_status: str
    resolution_rationale_reference: str | None
    resolved_at: datetime | None
    stale_at: datetime | None
    detected_at: datetime


class DuplicateCandidatesResult(_Closed):
    items: list[DuplicateCandidateView]
    page: PageInfo


class DuplicateCandidatesResponse(_Closed):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    result: DuplicateCandidatesResult


class RoutingDecisionView(_Closed):
    id: uuid.UUID
    decision_number: int
    policy_id: uuid.UUID
    policy_semantic_version: str
    policy_revision: int
    policy_digest: str
    evaluated_at: datetime
    ai_interpretation_id: uuid.UUID | None
    ai_confidence: Decimal | None
    missing_information_codes: list[str]
    prior_decision_id: uuid.UUID | None
    reviewed_fact_set_id: uuid.UUID | None
    final_category: str
    final_priority: str
    final_status: str
    final_queue: str
    review_required: bool
    review_reason_codes: list[str]
    category_reason_codes: list[str]
    priority_reason_codes: list[str]
    decision_source: str
    created_at: datetime


class RoutingDecisionsResult(_Closed):
    items: list[RoutingDecisionView]
    page: PageInfo


class RoutingDecisionsResponse(_Closed):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    result: RoutingDecisionsResult


class ProposalView(_Closed):
    id: uuid.UUID
    service_request_id: uuid.UUID
    proposal_series_id: uuid.UUID
    proposal_number: int
    logical_operation_id: uuid.UUID
    version: int
    state: str
    action_type: str
    destination_kind: str
    destination_value: str
    content: str
    scheduling_window_start: datetime | None
    scheduling_window_end: datetime | None
    scheduling_notes: str | None
    payload_digest: str
    supersedes_id: uuid.UUID | None
    superseded_by_id: uuid.UUID | None
    current_approval_id: uuid.UUID | None
    submitted_at: datetime | None
    terminal_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ProposalResponse(_Closed):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    result: ProposalView


class WorkflowProposalView(_Closed):
    id: uuid.UUID
    service_request_id: uuid.UUID
    logical_operation_id: uuid.UUID
    proposal_number: int
    version: int
    state: str
    action_type: str
    destination_kind: str
    destination_value: str
    content: str
    scheduling_window_start: datetime | None
    scheduling_window_end: datetime | None
    scheduling_notes: str | None
    payload_digest: str
    approval_valid: bool


class WorkflowProposalResponse(_Closed):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    result: WorkflowProposalView


class ProposalListResult(_Closed):
    items: list[ProposalView]
    page: PageInfo


class ProposalListResponse(_Closed):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    result: ProposalListResult


class ApprovalView(_Closed):
    id: uuid.UUID
    proposal_number: int
    payload_digest: str
    decision: str
    role_at_decision: str
    decided_at: datetime


class ApprovalManagerView(ApprovalView):
    rationale_recorded: bool


class ApprovalsResult(_Closed):
    items: list[ApprovalView]
    page: PageInfo


class ManagerApprovalsResult(_Closed):
    items: list[ApprovalManagerView]
    page: PageInfo


class ApprovalsResponse(_Closed):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    result: ApprovalsResult


class ManagerApprovalsResponse(_Closed):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    result: ManagerApprovalsResult


class AttemptView(_Closed):
    id: uuid.UUID
    logical_operation_id: uuid.UUID
    service_request_id: uuid.UUID
    operation_kind: str
    proposed_action_id: uuid.UUID | None
    attempt_number: int
    state: str
    version: int
    adapter_name: str
    adapter_version: str
    safe_provider_correlation: str | None
    sanitized_error_code: str | None
    recovery_disposition: str | None
    next_eligible_at: datetime | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class WorkflowAttemptView(_Closed):
    id: uuid.UUID
    logical_operation_id: uuid.UUID
    service_request_id: uuid.UUID
    operation_kind: str
    proposed_action_id: uuid.UUID | None
    attempt_number: int
    state: str
    version: int
    adapter_name: str
    adapter_version: str
    safe_provider_correlation: str | None
    sanitized_error_code: str | None
    callback_authorization_deadline: datetime
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class AttemptResponse(_Closed):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    result: AttemptView


class WorkflowAttemptResponse(_Closed):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    result: WorkflowAttemptView


class AttemptsResult(_Closed):
    items: list[AttemptView]
    page: PageInfo


class WorkflowAttemptsResult(_Closed):
    items: list[WorkflowAttemptView]
    page: PageInfo


class AttemptsResponse(_Closed):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    result: AttemptsResult


class WorkflowAttemptsResponse(_Closed):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    result: WorkflowAttemptsResult


class AuditEventView(_Closed):
    id: uuid.UUID
    event_name: str
    aggregate_type: str
    aggregate_id: uuid.UUID
    aggregate_version: int
    actor_type: str
    outcome: str
    reason_codes: list[str]
    occurred_at: datetime


class AuditEventsResult(_Closed):
    items: list[AuditEventView]
    page: PageInfo


class AuditEventsResponse(_Closed):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    result: AuditEventsResult
