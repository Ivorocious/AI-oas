"""Atomic proposal, approval, rejection, and material-revision commands."""

import hashlib
import uuid
from copy import deepcopy
from datetime import datetime
from typing import Any

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
from ai_operations_automation.db.models.ai_execution import IntegrationAttempt, LogicalOperation
from ai_operations_automation.db.models.intake import ServiceRequest
from ai_operations_automation.db.models.proposal import (
    ApprovalDecision,
    ProposalApprovalExclusion,
    ProposedAction,
    ProposedActionContributor,
)
from ai_operations_automation.event_writing import (
    AuditSpec,
    OutboxSpec,
    write_audit_and_optional_outbox,
)
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.proposal.contracts import ProposalOutcome
from ai_operations_automation.proposal.digest import proposal_payload_digest
from ai_operations_automation.proposal.models import (
    CreateDraftRequest,
    DecideProposalRequest,
    EditDraftRequest,
    MaterialRevisionRequest,
    RejectProposalRequest,
    SubmitProposalRequest,
)

CREATE_ROUTE = "/api/v1/service-requests/{request_id}/proposed-actions"
EDIT_ROUTE = "/api/v1/proposed-actions/{action_id}/draft"
SUBMIT_ROUTE = "/api/v1/proposed-actions/{action_id}/commands/submit-for-approval"
APPROVE_ROUTE = "/api/v1/proposed-actions/{action_id}/commands/approve"
REJECT_ROUTE = "/api/v1/proposed-actions/{action_id}/commands/reject"
REVISE_ROUTE = "/api/v1/proposed-actions/{action_id}/commands/create-material-revision"
AUTHOR_ROLES = {"OperationsAgent", "ManagerApprover", "Administrator"}
APPROVER_ROLES = {"ManagerApprover", "Administrator"}


class ProposalLifecycleService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def execute(
        self,
        *,
        intent: str,
        target_id: uuid.UUID,
        command: Any,
        raw_idempotency_key: str,
        canonical_body_hash: str,
        correlation_id: uuid.UUID,
        actor: AuthenticatedHuman,
    ) -> ProposalOutcome:
        route = {
            "CreateProposalDraft": CREATE_ROUTE,
            "EditProposalDraft": EDIT_ROUTE,
            "SubmitProposal": SUBMIT_ROUTE,
            "ApproveProposal": APPROVE_ROUTE,
            "RejectProposal": REJECT_ROUTE,
            "CreateMaterialRevision": REVISE_ROUTE,
        }[intent]
        target_type = "ServiceRequest" if intent == "CreateProposalDraft" else "ProposedAction"
        try:
            with self.session_factory() as session:
                with session.begin():
                    idem = CommandIdempotencyService(session)
                    resolution = idem.reserve(
                        CommandIdempotencyScope(
                            actor_class="HumanActor",
                            actor_id=actor.actor_id,
                            command_intent=intent,
                            route_template=route,
                            target_type=target_type,
                            target_id=target_id,
                        ),
                        raw_idempotency_key,
                        canonical_body_hash,
                        correlation_id,
                    )
                    if isinstance(resolution, CompletedCommandReplay):
                        return self._replay(resolution)
                    if intent == "CreateProposalDraft":
                        return self._create(
                            session, idem, resolution, target_id, command, correlation_id, actor
                        )
                    request_id = session.scalar(
                        select(ProposedAction.service_request_id).where(
                            ProposedAction.id == target_id
                        )
                    )
                    if request_id is None:
                        return self._guard(
                            idem,
                            resolution,
                            404,
                            "RESOURCE_NOT_FOUND",
                            "The requested resource was not found.",
                        )
                    request = session.scalar(
                        select(ServiceRequest)
                        .where(ServiceRequest.id == request_id)
                        .with_for_update()
                    )
                    proposal = session.scalar(
                        select(ProposedAction)
                        .where(ProposedAction.id == target_id)
                        .with_for_update()
                    )
                    if (
                        request is None
                        or proposal is None
                        or proposal.service_request_id != request.id
                    ):
                        return self._guard(
                            idem,
                            resolution,
                            404,
                            "RESOURCE_NOT_FOUND",
                            "The requested resource was not found.",
                        )
                    if intent == "EditProposalDraft":
                        return self._edit(
                            session,
                            idem,
                            resolution,
                            request,
                            proposal,
                            command,
                            correlation_id,
                            actor,
                        )
                    if intent == "SubmitProposal":
                        return self._submit(
                            session,
                            idem,
                            resolution,
                            request,
                            proposal,
                            command,
                            correlation_id,
                            actor,
                        )
                    if intent in {"ApproveProposal", "RejectProposal"}:
                        return self._decide(
                            session,
                            idem,
                            resolution,
                            request,
                            proposal,
                            command,
                            correlation_id,
                            actor,
                            approved=intent == "ApproveProposal",
                        )
                    return self._revise(
                        session, idem, resolution, request, proposal, command, correlation_id, actor
                    )
        except IntakeError:
            raise
        except OperationalError as exc:
            raise IntakeError(
                503, "DEPENDENCY_UNAVAILABLE", "A required dependency is unavailable.", True
            ) from exc
        except SQLAlchemyError as exc:
            raise IntakeError(
                500, "INTERNAL_ERROR", "The request could not be completed safely."
            ) from exc
        except Exception as exc:
            raise IntakeError(
                500, "INTERNAL_ERROR", "The request could not be completed safely."
            ) from exc

    def _create(
        self,
        session: Session,
        idem: CommandIdempotencyService,
        reservation: NewCommandReservation,
        request_id: uuid.UUID,
        command: CreateDraftRequest,
        correlation_id: uuid.UUID,
        actor: AuthenticatedHuman,
    ) -> ProposalOutcome:
        if actor.role not in AUTHOR_ROLES:
            return self._guard(
                idem, reservation, 403, "FORBIDDEN", "The requested operation is not permitted."
            )
        request = session.scalar(
            select(ServiceRequest).where(ServiceRequest.id == request_id).with_for_update()
        )
        if request is None:
            return self._guard(
                idem,
                reservation,
                404,
                "RESOURCE_NOT_FOUND",
                "The requested resource was not found.",
            )
        if request.version != command.expected_versions.service_request:
            return self._version_guard(idem, reservation, request)
        family_count = session.scalar(
            select(func.count())
            .select_from(ProposedAction)
            .where(ProposedAction.service_request_id == request.id)
        )
        if (
            request.status not in {"ReadyForAction", "ActionRevisionRequired"}
            or request.current_proposed_action_id is not None
            or family_count
        ):
            return self._guard(
                idem,
                reservation,
                409,
                "INVALID_STATE_TRANSITION",
                "A proposal draft cannot be created from the current request state.",
            )
        series_id, operation_id, proposal_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        operation = LogicalOperation(
            id=operation_id,
            service_request_id=request.id,
            operation_kind="OutboundAction",
            proposal_series_id=series_id,
            version=1,
        )
        payload = command.proposal.model_dump(mode="python")
        proposal = self._proposal_row(
            proposal_id, request.id, series_id, operation_id, 1, actor.actor_id, payload
        )
        session.add_all([operation, proposal])
        session.flush()
        session.add(
            ProposedActionContributor(
                id=uuid.uuid4(),
                proposed_action_id=proposal.id,
                service_request_id=request.id,
                proposal_series_id=series_id,
                actor_id=actor.actor_id,
                contribution_kind="Creator",
                carried_forward=False,
            )
        )
        request.current_proposed_action_id = proposal.id
        request.version += 1
        session.flush()
        self._evidence(
            session,
            proposal,
            request,
            reservation,
            correlation_id,
            actor,
            "proposed_action.created",
            "proposed_action.draft_created",
            "Created",
        )
        return self._complete(idem, reservation, 201, proposal, request)

    def _edit(
        self,
        session: Session,
        idem: CommandIdempotencyService,
        reservation: NewCommandReservation,
        request: ServiceRequest,
        proposal: ProposedAction,
        command: EditDraftRequest,
        correlation_id: uuid.UUID,
        actor: AuthenticatedHuman,
    ) -> ProposalOutcome:
        if actor.role not in AUTHOR_ROLES:
            return self._guard(
                idem, reservation, 403, "FORBIDDEN", "The requested operation is not permitted."
            )
        if proposal.version != command.expected_versions.proposed_action:
            return self._proposal_version_guard(idem, reservation, request, proposal)
        if (
            request.current_proposed_action_id != proposal.id
            or proposal.state != "Draft"
            or proposal.submitted_at is not None
        ):
            return self._guard(
                idem,
                reservation,
                409,
                "INVALID_STATE_TRANSITION",
                "Only the active unfrozen draft may be edited.",
            )
        if self._operation_blocked(session, proposal):
            return self._guard(
                idem,
                reservation,
                409,
                "OUTBOUND_OPERATION_BLOCKED",
                "The outbound operation has active or successful evidence.",
            )
        payload = command.proposal.model_dump(mode="python")
        digest = proposal_payload_digest(payload)
        if digest == proposal.payload_digest:
            return self._complete(idem, reservation, 200, proposal, request)
        self._apply_payload(proposal, payload)
        proposal.payload_digest = digest
        proposal.version += 1
        existing = session.scalar(
            select(ProposedActionContributor).where(
                ProposedActionContributor.proposed_action_id == proposal.id,
                ProposedActionContributor.actor_id == actor.actor_id,
            )
        )
        if existing is None:
            session.add(
                ProposedActionContributor(
                    id=uuid.uuid4(),
                    proposed_action_id=proposal.id,
                    service_request_id=request.id,
                    proposal_series_id=proposal.proposal_series_id,
                    actor_id=actor.actor_id,
                    contribution_kind="MaterialEditor",
                    carried_forward=False,
                )
            )
        session.flush()
        self._evidence(
            session,
            proposal,
            request,
            reservation,
            correlation_id,
            actor,
            "proposed_action.draft_updated",
            "proposed_action.draft_updated",
            "Updated",
        )
        return self._complete(idem, reservation, 200, proposal, request)

    def _submit(
        self,
        session: Session,
        idem: CommandIdempotencyService,
        reservation: NewCommandReservation,
        request: ServiceRequest,
        proposal: ProposedAction,
        command: SubmitProposalRequest,
        correlation_id: uuid.UUID,
        actor: AuthenticatedHuman,
    ) -> ProposalOutcome:
        if actor.role not in AUTHOR_ROLES:
            return self._guard(
                idem, reservation, 403, "FORBIDDEN", "The requested operation is not permitted."
            )
        conflict = self._check_both_versions(
            idem, reservation, request, proposal, command.expected_versions
        )
        if conflict:
            return conflict
        if (
            request.current_proposed_action_id != proposal.id
            or proposal.state != "Draft"
            or request.status not in {"ReadyForAction", "ActionRevisionRequired"}
        ):
            return self._guard(
                idem,
                reservation,
                409,
                "INVALID_STATE_TRANSITION",
                "The proposal cannot be submitted from the current state.",
            )
        if self._operation_blocked(session, proposal):
            return self._guard(
                idem,
                reservation,
                409,
                "OUTBOUND_OPERATION_BLOCKED",
                "The outbound operation has active or successful evidence.",
            )
        if proposal.payload_digest != proposal_payload_digest(self._payload(proposal)):
            return self._guard(
                idem,
                reservation,
                409,
                "PROPOSAL_DIGEST_CONFLICT",
                "The stored proposal payload digest is inconsistent.",
            )
        contributors = session.scalars(
            select(ProposedActionContributor)
            .where(ProposedActionContributor.proposed_action_id == proposal.id)
            .with_for_update()
        ).all()
        if not contributors or not any(
            row.actor_id == proposal.creator_actor_id and row.contribution_kind == "Creator"
            for row in contributors
        ):
            return self._guard(
                idem,
                reservation,
                409,
                "PROPOSAL_ATTRIBUTION_INCOMPLETE",
                "Proposal contributor attribution is incomplete.",
            )
        now = session.scalar(select(func.now()))
        for contributor in contributors:
            session.add(
                ProposalApprovalExclusion(
                    id=uuid.uuid4(),
                    proposed_action_id=proposal.id,
                    excluded_actor_id=contributor.actor_id,
                    source_contributor_id=contributor.id,
                    frozen_at=now,
                )
            )
        proposal.state, proposal.submitted_at = "PendingApproval", now
        proposal.version += 1
        old_queue = request.current_queue
        request.status, request.current_queue = "AwaitingApproval", "HumanReview"
        request.version += 1
        session.flush()
        self._evidence(
            session,
            proposal,
            request,
            reservation,
            correlation_id,
            actor,
            "proposed_action.submitted",
            "proposed_action.submitted",
            "PendingApproval",
        )
        self._request_evidence(
            session,
            request,
            reservation,
            correlation_id,
            actor,
            "service_request.awaiting_approval",
            "service_request.awaiting_approval",
            old_queue,
        )
        return self._complete(idem, reservation, 200, proposal, request)

    def _decide(
        self,
        session: Session,
        idem: CommandIdempotencyService,
        reservation: NewCommandReservation,
        request: ServiceRequest,
        proposal: ProposedAction,
        command: DecideProposalRequest | RejectProposalRequest,
        correlation_id: uuid.UUID,
        actor: AuthenticatedHuman,
        *,
        approved: bool,
    ) -> ProposalOutcome:
        if actor.role not in APPROVER_ROLES:
            return self._guard(
                idem, reservation, 403, "FORBIDDEN", "The requested operation is not permitted."
            )
        conflict = self._check_both_versions(
            idem, reservation, request, proposal, command.expected_versions
        )
        if conflict:
            return conflict
        if (
            request.status != "AwaitingApproval"
            or proposal.state != "PendingApproval"
            or request.current_proposed_action_id != proposal.id
        ):
            return self._guard(
                idem,
                reservation,
                409,
                "INVALID_STATE_TRANSITION",
                "The proposal is not awaiting an approval decision.",
            )
        if (
            command.expected_payload_digest != proposal.payload_digest
            or proposal.submitted_at is None
        ):
            return self._guard(
                idem,
                reservation,
                409,
                "PROPOSAL_DIGEST_CONFLICT",
                "The exact frozen proposal digest does not match.",
            )
        exclusions = session.scalars(
            select(ProposalApprovalExclusion)
            .where(ProposalApprovalExclusion.proposed_action_id == proposal.id)
            .with_for_update()
        ).all()
        contributor_count = session.scalar(
            select(func.count())
            .select_from(ProposedActionContributor)
            .where(ProposedActionContributor.proposed_action_id == proposal.id)
        )
        if not exclusions or len(exclusions) != contributor_count:
            return self._guard(
                idem,
                reservation,
                409,
                "APPROVAL_EXCLUSIONS_INCOMPLETE",
                "The frozen approval exclusion set is incomplete.",
            )
        if any(row.excluded_actor_id == actor.actor_id for row in exclusions):
            return self._guard(
                idem,
                reservation,
                403,
                "SELF_APPROVAL_FORBIDDEN",
                "Proposal contributors cannot decide their represented work.",
            )
        if (
            session.scalar(
                select(ApprovalDecision).where(ApprovalDecision.proposed_action_id == proposal.id)
            )
            is not None
        ):
            return self._guard(
                idem,
                reservation,
                409,
                "APPROVAL_ALREADY_DECIDED",
                "This exact proposal already has a decision.",
            )
        if self._operation_blocked(session, proposal):
            return self._guard(
                idem,
                reservation,
                409,
                "OUTBOUND_OPERATION_BLOCKED",
                "The outbound operation is no longer eligible for a decision.",
            )
        decision = ApprovalDecision(
            id=uuid.uuid4(),
            proposed_action_id=proposal.id,
            proposal_number=proposal.proposal_number,
            payload_digest=proposal.payload_digest,
            decision="Approved" if approved else "Rejected",
            approver_actor_id=actor.actor_id,
            role_at_decision=actor.role,
            correlation_id=correlation_id,
            command_id=reservation.command_id,
            rationale_digest=None
            if approved
            else hashlib.sha256(command.rationale.encode("utf-8")).hexdigest(),
        )
        session.add(decision)
        session.flush()
        old_queue = request.current_queue
        proposal.version += 1
        if approved:
            proposal.state, proposal.current_approval_id = "Approved", decision.id
            request.status = "ActionPendingExecution"
            request.current_queue = (
                "StandardRequests"
                if request.priority in {"Low", "Normal"}
                else "PriorityRequests"
                if request.priority == "High"
                else "HumanReview"
            )
        else:
            proposal.state, proposal.current_approval_id = "Rejected", None
            proposal.terminal_at = session.scalar(select(func.now()))
            request.status, request.current_queue = "ActionRevisionRequired", "HumanReview"
        request.version += 1
        session.flush()
        event = "approval.approved" if approved else "approval.rejected"
        self._evidence(
            session,
            proposal,
            request,
            reservation,
            correlation_id,
            actor,
            "proposed_action.approved" if approved else "proposed_action.rejected",
            None,
            decision.decision,
        )
        self._decision_evidence(
            session, proposal, decision, request, reservation, correlation_id, actor, event
        )
        self._request_evidence(
            session,
            request,
            reservation,
            correlation_id,
            actor,
            "service_request.action_approved"
            if approved
            else "service_request.action_revision_required",
            "service_request.action_pending_execution"
            if approved
            else "service_request.action_revision_required",
            old_queue,
        )
        return self._complete(
            idem,
            reservation,
            200,
            proposal,
            request,
            extra_result={"approval_decision_id": str(decision.id)},
        )

    def _revise(
        self,
        session: Session,
        idem: CommandIdempotencyService,
        reservation: NewCommandReservation,
        request: ServiceRequest,
        source: ProposedAction,
        command: MaterialRevisionRequest,
        correlation_id: uuid.UUID,
        actor: AuthenticatedHuman,
    ) -> ProposalOutcome:
        if actor.role not in AUTHOR_ROLES:
            return self._guard(
                idem, reservation, 403, "FORBIDDEN", "The requested operation is not permitted."
            )
        conflict = self._check_both_versions(
            idem, reservation, request, source, command.expected_versions
        )
        if conflict:
            return conflict
        paths = {
            "Draft": {"ReadyForAction", "ActionRevisionRequired"},
            "PendingApproval": {"AwaitingApproval"},
            "Approved": {"ActionPendingExecution"},
            "Rejected": {"ActionRevisionRequired"},
            "RetryableExecutionFailure": {"RetryableFailure"},
        }
        if (
            request.current_proposed_action_id != source.id
            or source.state not in paths
            or request.status not in paths[source.state]
        ):
            return self._guard(
                idem,
                reservation,
                409,
                "INVALID_STATE_TRANSITION",
                "A material revision is not allowed from the current state.",
            )
        if (
            source.state == "RetryableExecutionFailure"
            and request.recovery_target != "ActionPendingExecution"
        ):
            return self._guard(
                idem,
                reservation,
                409,
                "INVALID_STATE_TRANSITION",
                "The execution recovery target does not permit revision.",
            )
        if source.state == "PendingApproval":
            contradictory_decision = session.scalar(
                select(ApprovalDecision.id).where(ApprovalDecision.proposed_action_id == source.id)
            )
            if source.current_approval_id is not None or contradictory_decision is not None:
                return self._guard(
                    idem,
                    reservation,
                    409,
                    "PROPOSAL_APPROVAL_GRAPH_INCONSISTENT",
                    "The proposal approval graph is inconsistent with its lifecycle state.",
                )
        prior_approval: ApprovalDecision | None = None
        if source.state in {"Approved", "RetryableExecutionFailure"}:
            prior_approval = session.scalar(
                select(ApprovalDecision)
                .where(
                    ApprovalDecision.id == source.current_approval_id,
                    ApprovalDecision.proposed_action_id == source.id,
                    ApprovalDecision.proposal_number == source.proposal_number,
                    ApprovalDecision.payload_digest == source.payload_digest,
                    ApprovalDecision.decision == "Approved",
                )
                .with_for_update()
            )
            if prior_approval is None:
                return self._guard(
                    idem,
                    reservation,
                    409,
                    "PROPOSAL_APPROVAL_GRAPH_INCONSISTENT",
                    "The proposal approval graph is inconsistent with its lifecycle state.",
                )
        if self._operation_blocked(session, source):
            return self._guard(
                idem,
                reservation,
                409,
                "OUTBOUND_OPERATION_BLOCKED",
                "The outbound operation has active or successful evidence.",
            )
        original_state = source.state
        existing_replacement = session.scalar(
            select(ProposedAction.id).where(ProposedAction.supersedes_id == source.id)
        )
        if existing_replacement is not None:
            return self._guard(
                idem,
                reservation,
                409,
                "MATERIAL_REVISION_EXISTS",
                "A replacement proposal already exists.",
            )
        now = session.scalar(select(func.now()))
        payload = command.proposal.model_dump(mode="python")
        replacement = self._proposal_row(
            uuid.uuid4(),
            request.id,
            source.proposal_series_id,
            source.logical_operation_id,
            source.proposal_number + 1,
            actor.actor_id,
            payload,
        )
        replacement.supersedes_id = source.id
        if original_state != "Rejected":
            source.state, source.current_approval_id, source.terminal_at = "Superseded", None, now
            source.version += 1
            session.flush()
        session.add(replacement)
        session.flush()
        source.superseded_by_id = replacement.id
        if original_state == "Rejected":
            source.version += 1
        contributors = session.scalars(
            select(ProposedActionContributor).where(
                ProposedActionContributor.proposed_action_id == source.id
            )
        ).all()
        session.add(
            ProposedActionContributor(
                id=uuid.uuid4(),
                proposed_action_id=replacement.id,
                service_request_id=request.id,
                proposal_series_id=replacement.proposal_series_id,
                actor_id=actor.actor_id,
                contribution_kind="Creator",
                carried_forward=False,
            )
        )
        for contributor in contributors:
            if contributor.actor_id == actor.actor_id:
                continue
            session.add(
                ProposedActionContributor(
                    id=uuid.uuid4(),
                    proposed_action_id=replacement.id,
                    service_request_id=request.id,
                    proposal_series_id=replacement.proposal_series_id,
                    actor_id=contributor.actor_id,
                    contribution_kind=contributor.contribution_kind,
                    carried_forward=True,
                    source_proposal_id=source.id,
                )
            )
        old_queue = request.current_queue
        if original_state != "Draft":
            request.status, request.current_queue = "ActionRevisionRequired", "HumanReview"
        recovery_cleared = request.recovery_target is not None
        if recovery_cleared:
            request.recovery_target = request.recovery_attempt_id = request.failure_summary_code = (
                None
            )
        request.current_proposed_action_id = replacement.id
        request.version += 1
        session.flush()
        if original_state != "Rejected":
            self._evidence(
                session,
                source,
                request,
                reservation,
                correlation_id,
                actor,
                "proposed_action.superseded",
                "proposed_action.superseded",
                "Revised",
            )
        self._evidence(
            session,
            replacement,
            request,
            reservation,
            correlation_id,
            actor,
            "proposed_action.version_created",
            "proposed_action.draft_created",
            "Draft",
        )
        if prior_approval is not None:
            validity = {
                "approval_decision_id": str(prior_approval.id),
                "source_proposed_action_id": str(source.id),
                "source_proposal_number": source.proposal_number,
                "source_proposal_version": source.version,
                "replacement_proposed_action_id": str(replacement.id),
                "proposal_series_id": str(source.proposal_series_id),
                "logical_operation_id": str(source.logical_operation_id),
                "recovery_cleared": recovery_cleared,
            }
            write_audit_and_optional_outbox(
                session,
                AuditSpec(
                    event_name="approval.execution_validity_lost",
                    aggregate_type="ProposedAction",
                    aggregate_id=source.id,
                    aggregate_version=source.version,
                    actor_type="HumanActor",
                    actor_reference_id=actor.actor_id,
                    outcome="Superseded",
                    correlation_id=correlation_id,
                    causation_id=reservation.command_id,
                    command_id=reservation.command_id,
                    safe_metadata=validity,
                ),
                OutboxSpec(event_type="approval.execution_validity_lost", payload=validity),
            )
        if request.status == "ActionRevisionRequired":
            self._request_evidence(
                session,
                request,
                reservation,
                correlation_id,
                actor,
                "service_request.action_revision_required",
                "service_request.action_revision_required",
                old_queue,
            )
        return self._complete(
            idem,
            reservation,
            201,
            replacement,
            request,
            extra_result={
                "source_proposed_action_id": str(source.id),
                "source_proposal_state": source.state,
                "replacement_proposed_action_id": str(replacement.id),
                "replacement_proposal_state": replacement.state,
                "recovery_cleared": recovery_cleared,
            },
        )

    @staticmethod
    def _proposal_row(
        proposal_id: uuid.UUID,
        request_id: uuid.UUID,
        series_id: uuid.UUID,
        operation_id: uuid.UUID,
        number: int,
        creator_id: uuid.UUID,
        payload: dict[str, Any],
    ) -> ProposedAction:
        row = ProposedAction(
            id=proposal_id,
            service_request_id=request_id,
            proposal_series_id=series_id,
            proposal_number=number,
            logical_operation_id=operation_id,
            version=1,
            state="Draft",
            action_type=payload["action_type"],
            destination_kind=payload["destination"]["kind"],
            destination_value=payload["destination"]["value"],
            content=payload["content"],
            payload_digest=proposal_payload_digest(payload),
            creator_actor_id=creator_id,
        )
        ProposalLifecycleService._apply_payload(row, payload)
        return row

    @staticmethod
    def _apply_payload(row: ProposedAction, payload: dict[str, Any]) -> None:
        row.action_type, row.destination_kind, row.destination_value, row.content = (
            payload["action_type"],
            payload["destination"]["kind"],
            payload["destination"]["value"],
            payload["content"],
        )
        schedule = payload.get("scheduling")
        row.scheduling_window_start = schedule["window_start"] if schedule else None
        row.scheduling_window_end = schedule["window_end"] if schedule else None
        if isinstance(row.scheduling_window_start, str):
            row.scheduling_window_start = datetime.fromisoformat(row.scheduling_window_start)
        if isinstance(row.scheduling_window_end, str):
            row.scheduling_window_end = datetime.fromisoformat(row.scheduling_window_end)
        row.scheduling_notes = schedule.get("notes") if schedule else None

    @staticmethod
    def _payload(row: ProposedAction) -> dict[str, Any]:
        schedule = (
            None
            if row.scheduling_window_start is None
            else {
                "window_start": row.scheduling_window_start,
                "window_end": row.scheduling_window_end,
                "notes": row.scheduling_notes,
            }
        )
        return {
            "action_type": row.action_type,
            "destination": {"kind": row.destination_kind, "value": row.destination_value},
            "content": row.content,
            "scheduling": schedule,
        }

    @staticmethod
    def _operation_blocked(session: Session, proposal: ProposedAction) -> bool:
        operation = session.scalar(
            select(LogicalOperation)
            .where(LogicalOperation.id == proposal.logical_operation_id)
            .with_for_update()
        )
        active = session.scalar(
            select(func.count())
            .select_from(IntegrationAttempt)
            .where(
                IntegrationAttempt.logical_operation_id == proposal.logical_operation_id,
                IntegrationAttempt.state.in_(("Pending", "Running", "Succeeded")),
            )
        )
        return operation is None or operation.succeeded_attempt_id is not None or bool(active)

    @staticmethod
    def _safe_result(proposal: ProposedAction, request: ServiceRequest) -> dict[str, Any]:
        return {
            "service_request_id": str(request.id),
            "proposed_action_id": str(proposal.id),
            "proposal_series_id": str(proposal.proposal_series_id),
            "logical_operation_id": str(proposal.logical_operation_id),
            "proposal_number": proposal.proposal_number,
            "proposal_state": proposal.state,
            "payload_digest": proposal.payload_digest,
            "service_request_status": request.status,
            "service_request_queue": request.current_queue,
        }

    def _complete(
        self,
        idem: CommandIdempotencyService,
        reservation: NewCommandReservation,
        status: int,
        proposal: ProposedAction,
        request: ServiceRequest,
        extra_result: dict[str, Any] | None = None,
    ) -> ProposalOutcome:
        result = self._safe_result(proposal, request)
        if extra_result is not None:
            result.update(extra_result)
        completed = idem.complete(
            reservation,
            status,
            {
                "result": result,
                "versions": {
                    "service_request": request.version,
                    "proposed_action": proposal.version,
                },
            },
        )
        return ProposalOutcome(
            status, completed.command_id, deepcopy(completed.safe_response_snapshot)
        )

    @staticmethod
    def _guard(
        idem: CommandIdempotencyService,
        reservation: NewCommandReservation,
        status: int,
        code: str,
        message: str,
        current_versions: dict[str, int] | None = None,
    ) -> ProposalOutcome:
        completed = idem.complete(
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
        return ProposalOutcome(
            status, completed.command_id, deepcopy(completed.safe_response_snapshot)
        )

    def _version_guard(
        self,
        idem: CommandIdempotencyService,
        reservation: NewCommandReservation,
        request: ServiceRequest,
    ) -> ProposalOutcome:
        return self._guard(
            idem,
            reservation,
            409,
            "CONCURRENCY_CONFLICT",
            "The resource version does not match the current version.",
            {"service_request": request.version},
        )

    def _proposal_version_guard(
        self,
        idem: CommandIdempotencyService,
        reservation: NewCommandReservation,
        request: ServiceRequest,
        proposal: ProposedAction,
    ) -> ProposalOutcome:
        return self._guard(
            idem,
            reservation,
            409,
            "CONCURRENCY_CONFLICT",
            "The resource version does not match the current version.",
            {"service_request": request.version, "proposed_action": proposal.version},
        )

    def _check_both_versions(
        self,
        idem: CommandIdempotencyService,
        reservation: NewCommandReservation,
        request: ServiceRequest,
        proposal: ProposedAction,
        expected: Any,
    ) -> ProposalOutcome | None:
        if (
            request.version != expected.service_request
            or proposal.version != expected.proposed_action
        ):
            return self._proposal_version_guard(idem, reservation, request, proposal)
        return None

    @staticmethod
    def _replay(replay: CompletedCommandReplay) -> ProposalOutcome:
        return ProposalOutcome(
            replay.logical_http_status, replay.command_id, deepcopy(replay.safe_response_snapshot)
        )

    def _evidence(
        self,
        session: Session,
        proposal: ProposedAction,
        request: ServiceRequest,
        reservation: NewCommandReservation,
        correlation_id: uuid.UUID,
        actor: AuthenticatedHuman,
        audit_name: str,
        outbox_name: str | None,
        outcome: str,
    ) -> None:
        safe = self._safe_result(proposal, request)
        write_audit_and_optional_outbox(
            session,
            AuditSpec(
                event_name=audit_name,
                aggregate_type="ProposedAction",
                aggregate_id=proposal.id,
                aggregate_version=proposal.version,
                actor_type="HumanActor",
                actor_reference_id=actor.actor_id,
                outcome=outcome,
                correlation_id=correlation_id,
                causation_id=reservation.command_id,
                command_id=reservation.command_id,
                safe_metadata=safe,
            ),
            OutboxSpec(event_type=outbox_name, payload=safe) if outbox_name else None,
        )

    def _decision_evidence(
        self,
        session: Session,
        proposal: ProposedAction,
        decision: ApprovalDecision,
        request: ServiceRequest,
        reservation: NewCommandReservation,
        correlation_id: uuid.UUID,
        actor: AuthenticatedHuman,
        event: str,
    ) -> None:
        safe = {
            **self._safe_result(proposal, request),
            "approval_decision_id": str(decision.id),
            "approver_actor_id": str(actor.actor_id),
            "role_at_decision": actor.role,
            "decision": decision.decision,
        }
        write_audit_and_optional_outbox(
            session,
            AuditSpec(
                event_name=event,
                aggregate_type="ApprovalDecision",
                aggregate_id=decision.id,
                aggregate_version=1,
                actor_type="HumanActor",
                actor_reference_id=actor.actor_id,
                outcome=decision.decision,
                correlation_id=correlation_id,
                causation_id=reservation.command_id,
                command_id=reservation.command_id,
                safe_metadata=safe,
            ),
            OutboxSpec(event_type=event, payload=safe),
        )

    def _request_evidence(
        self,
        session: Session,
        request: ServiceRequest,
        reservation: NewCommandReservation,
        correlation_id: uuid.UUID,
        actor: AuthenticatedHuman,
        audit_name: str,
        outbox_name: str,
        old_queue: str | None,
    ) -> None:
        safe = {
            "service_request_id": str(request.id),
            "service_request_status": request.status,
            "service_request_queue": request.current_queue,
            "service_request_version": request.version,
        }
        write_audit_and_optional_outbox(
            session,
            AuditSpec(
                event_name=audit_name,
                aggregate_type="ServiceRequest",
                aggregate_id=request.id,
                aggregate_version=request.version,
                actor_type="HumanActor",
                actor_reference_id=actor.actor_id,
                outcome=request.status,
                correlation_id=correlation_id,
                causation_id=reservation.command_id,
                command_id=reservation.command_id,
                safe_metadata=safe,
            ),
            OutboxSpec(event_type=outbox_name, payload=safe),
        )
        if old_queue != request.current_queue:
            queue_safe = {**safe, "old_queue": old_queue, "new_queue": request.current_queue}
            write_audit_and_optional_outbox(
                session,
                AuditSpec(
                    event_name="service_request.queue_changed",
                    aggregate_type="ServiceRequest",
                    aggregate_id=request.id,
                    aggregate_version=request.version,
                    actor_type="HumanActor",
                    actor_reference_id=actor.actor_id,
                    outcome="Changed",
                    correlation_id=correlation_id,
                    causation_id=reservation.command_id,
                    command_id=reservation.command_id,
                    safe_metadata=queue_safe,
                ),
                OutboxSpec(event_type="service_request.queue_changed", payload=queue_safe),
            )
