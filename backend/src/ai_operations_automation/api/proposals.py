"""Human-authenticated proposal lifecycle command routes."""

import uuid
from copy import deepcopy
from typing import Annotated, Any, cast

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from ai_operations_automation.api.correlation import resolve_request_correlation
from ai_operations_automation.auth.dependencies import require_service_request_reader
from ai_operations_automation.auth.models import AuthenticatedHuman
from ai_operations_automation.command_idempotency.canonicalization import canonical_command_hash
from ai_operations_automation.human_review.parsing import (
    command_idempotency_key,
    validate_json_content_type,
)
from ai_operations_automation.intake.errors import ErrorEnvelope, IntakeError
from ai_operations_automation.proposal.models import (
    CreateDraftRequest,
    DecideProposalRequest,
    EditDraftRequest,
    MaterialRevisionRequest,
    ProposalCommandResponse,
    RejectProposalRequest,
    SubmitProposalRequest,
)
from ai_operations_automation.proposal.service import ProposalLifecycleService

router = APIRouter()
ERRORS = {
    status: {"model": ErrorEnvelope} for status in (400, 401, 403, 404, 409, 415, 422, 500, 503)
}


def get_proposal_service(request: Request) -> ProposalLifecycleService:
    service = getattr(request.app.state, "proposal_service", None)
    if service is None:
        raise IntakeError(
            503, "DEPENDENCY_UNAVAILABLE", "A required dependency is unavailable.", True
        )
    return cast(ProposalLifecycleService, service)


def _id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise IntakeError(
            404, "RESOURCE_NOT_FOUND", "The requested resource was not found."
        ) from exc


def _raise_error(status: int, snapshot: dict[str, Any]) -> None:
    if "error" in snapshot:
        error = snapshot["error"]
        raise IntakeError(
            status,
            error["code"],
            error["message"],
            bool(error.get("retryable", False)),
            details=error.get("details", []),
            current_versions=error.get("current_versions", {}),
        )


def _response(outcome: Any, correlation_id: uuid.UUID, *, location: bool = False) -> JSONResponse:
    _raise_error(outcome.logical_http_status, outcome.safe_snapshot)
    body = ProposalCommandResponse(
        correlation_id=correlation_id,
        command_id=outcome.command_id,
        **deepcopy(outcome.safe_snapshot),
    )
    headers = {"X-Correlation-ID": str(correlation_id)}
    if location:
        headers["Location"] = f"/api/v1/proposed-actions/{body.result.proposed_action_id}"
    return JSONResponse(
        status_code=outcome.logical_http_status,
        content=body.model_dump(mode="json"),
        headers=headers,
    )


def _execute(
    request: Request,
    service: ProposalLifecycleService,
    actor: AuthenticatedHuman,
    correlation_id: uuid.UUID,
    target_id: uuid.UUID,
    intent: str,
    command: Any,
    *,
    location: bool = False,
) -> JSONResponse:
    validate_json_content_type(request)
    outcome = service.execute(
        intent=intent,
        target_id=target_id,
        command=command,
        raw_idempotency_key=command_idempotency_key(request),
        canonical_body_hash=canonical_command_hash(command),
        correlation_id=correlation_id,
        actor=actor,
    )
    return _response(outcome, correlation_id, location=location)


Human = Annotated[AuthenticatedHuman, Depends(require_service_request_reader)]
Correlation = Annotated[uuid.UUID, Depends(resolve_request_correlation)]
Service = Annotated[ProposalLifecycleService, Depends(get_proposal_service)]


@router.post(
    "/api/v1/service-requests/{request_id}/proposed-actions",
    operation_id="create_proposal_draft",
    response_model=ProposalCommandResponse,
    status_code=201,
    responses=ERRORS,
)
def create_proposal_draft(
    request_id: str,
    request: Request,
    command: Annotated[CreateDraftRequest, Body()],
    correlation_id: Correlation,
    actor: Human,
    service: Service,
) -> JSONResponse:
    return _execute(
        request,
        service,
        actor,
        correlation_id,
        _id(request_id),
        "CreateProposalDraft",
        command,
        location=True,
    )


@router.put(
    "/api/v1/proposed-actions/{action_id}/draft",
    operation_id="edit_proposal_draft",
    response_model=ProposalCommandResponse,
    responses=ERRORS,
)
def edit_proposal_draft(
    action_id: str,
    request: Request,
    command: Annotated[EditDraftRequest, Body()],
    correlation_id: Correlation,
    actor: Human,
    service: Service,
) -> JSONResponse:
    return _execute(
        request, service, actor, correlation_id, _id(action_id), "EditProposalDraft", command
    )


@router.post(
    "/api/v1/proposed-actions/{action_id}/commands/submit-for-approval",
    operation_id="submit_proposal_for_approval",
    response_model=ProposalCommandResponse,
    responses=ERRORS,
)
def submit_proposal(
    action_id: str,
    request: Request,
    command: Annotated[SubmitProposalRequest, Body()],
    correlation_id: Correlation,
    actor: Human,
    service: Service,
) -> JSONResponse:
    return _execute(
        request, service, actor, correlation_id, _id(action_id), "SubmitProposal", command
    )


@router.post(
    "/api/v1/proposed-actions/{action_id}/commands/approve",
    operation_id="approve_proposal",
    response_model=ProposalCommandResponse,
    responses=ERRORS,
)
def approve_proposal(
    action_id: str,
    request: Request,
    command: Annotated[DecideProposalRequest, Body()],
    correlation_id: Correlation,
    actor: Human,
    service: Service,
) -> JSONResponse:
    return _execute(
        request, service, actor, correlation_id, _id(action_id), "ApproveProposal", command
    )


@router.post(
    "/api/v1/proposed-actions/{action_id}/commands/reject",
    operation_id="reject_proposal",
    response_model=ProposalCommandResponse,
    responses=ERRORS,
)
def reject_proposal(
    action_id: str,
    request: Request,
    command: Annotated[RejectProposalRequest, Body()],
    correlation_id: Correlation,
    actor: Human,
    service: Service,
) -> JSONResponse:
    return _execute(
        request, service, actor, correlation_id, _id(action_id), "RejectProposal", command
    )


@router.post(
    "/api/v1/proposed-actions/{action_id}/commands/create-material-revision",
    operation_id="create_material_revision",
    response_model=ProposalCommandResponse,
    status_code=201,
    responses=ERRORS,
)
def create_material_revision(
    action_id: str,
    request: Request,
    command: Annotated[MaterialRevisionRequest, Body()],
    correlation_id: Correlation,
    actor: Human,
    service: Service,
) -> JSONResponse:
    return _execute(
        request,
        service,
        actor,
        correlation_id,
        _id(action_id),
        "CreateMaterialRevision",
        command,
        location=True,
    )
