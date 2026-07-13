"""Checkpoint 3 proposal lifecycle on PostgreSQL."""

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import Engine, func, insert, select, text

from ai_operations_automation.auth.models import AuthenticatedHuman
from ai_operations_automation.command_idempotency.canonicalization import canonical_command_hash
from ai_operations_automation.config import Settings
from ai_operations_automation.db import Base, create_database_engine, create_session_factory
from ai_operations_automation.proposal.models import (
    CreateDraftRequest,
    DecideProposalRequest,
    EditDraftRequest,
    MaterialRevisionRequest,
    RejectProposalRequest,
    SubmitProposalRequest,
)
from ai_operations_automation.proposal.service import ProposalLifecycleService
from alembic import command

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    value = create_database_engine(Settings(_env_file=None).database_url)
    yield value
    value.dispose()


@pytest.fixture(autouse=True)
def clean(engine: Engine) -> Iterator[None]:
    names = [
        name
        for name in Base.metadata.tables
        if name not in {"decision_policy_versions", "failure_recovery_policy_versions"}
    ]
    with engine.begin() as connection:
        connection.execute(
            text("TRUNCATE " + ", ".join(f'"{name}"' for name in names) + " CASCADE")
        )
    yield


def seed(engine: Engine):
    ids = {
        name: uuid.uuid4()
        for name in ("contact", "delivery", "request", "creator", "editor", "approver")
    }
    tables = Base.metadata.tables
    with engine.begin() as connection:
        for actor in (ids["creator"], ids["editor"], ids["approver"]):
            connection.execute(
                insert(tables["application_actors"]).values(
                    id=actor,
                    supabase_subject=str(actor),
                    display_label="Fixture actor",
                    status="Active",
                    version=1,
                )
            )
        connection.execute(
            insert(tables["contacts"]).values(
                id=ids["contact"],
                display_label="Proposal fixture",
                normalized_email="customer@example.test",
                version=1,
            )
        )
        connection.execute(
            insert(tables["inbound_deliveries"]).values(
                id=ids["delivery"],
                scope=str(uuid.uuid4()),
                idempotency_key_digest=uuid.uuid4().hex,
                processing_status="Received",
                schema_version="1.0",
                version=1,
                correlation_id=uuid.uuid4(),
            )
        )
        connection.execute(
            insert(tables["service_requests"]).values(
                id=ids["request"],
                originating_delivery_id=ids["delivery"],
                contact_id=ids["contact"],
                normalized_request_description="A proposal-ready request.",
                status="ReadyForAction",
                current_queue="StandardRequests",
                priority="Normal",
                version=1,
            )
        )
    return ids


def actor(actor_id, role):
    return AuthenticatedHuman(actor_id, str(actor_id), role)


def execute(service, intent, target, command_body, user, key):
    return service.execute(
        intent=intent,
        target_id=target,
        command=command_body,
        raw_idempotency_key=key,
        canonical_body_hash=canonical_command_hash(command_body),
        correlation_id=uuid.uuid4(),
        actor=user,
    )


def proposal(content: str):
    return {
        "action_type": "CustomerMessage",
        "destination": {"kind": "Email", "value": "customer@example.test"},
        "content": content,
        "scheduling": None,
    }


def counts(engine, *names):
    with engine.connect() as connection:
        return {
            name: int(
                connection.scalar(select(func.count()).select_from(Base.metadata.tables[name])) or 0
            )
            for name in names
        }


def test_create_edit_submit_approve_revision_preserves_operation_and_exclusions(
    engine: Engine,
) -> None:
    ids = seed(engine)
    service = ProposalLifecycleService(create_session_factory(engine))
    creator = actor(ids["creator"], "OperationsAgent")
    editor = actor(ids["editor"], "ManagerApprover")
    approver = actor(ids["approver"], "Administrator")

    create = CreateDraftRequest(
        schema_version="1.0",
        expected_versions={"service_request": 1},
        proposal=proposal("Initial response"),
    )
    created = execute(
        service, "CreateProposalDraft", ids["request"], create, creator, "proposal-create-001"
    )
    assert created.logical_http_status == 201
    result = created.safe_snapshot["result"]
    action_id = uuid.UUID(result["proposed_action_id"])
    operation_id = result["logical_operation_id"]

    replay = execute(
        service, "CreateProposalDraft", ids["request"], create, creator, "proposal-create-001"
    )
    assert replay.safe_snapshot == created.safe_snapshot
    assert counts(
        engine,
        "proposed_actions",
        "logical_operations",
        "integration_attempts",
        "attempt_callback_credentials",
    ) == {
        "proposed_actions": 1,
        "logical_operations": 1,
        "integration_attempts": 0,
        "attempt_callback_credentials": 0,
    }

    edited = execute(
        service,
        "EditProposalDraft",
        action_id,
        EditDraftRequest(
            schema_version="1.0",
            expected_versions={"proposed_action": 1},
            proposal=proposal("Materially edited response"),
        ),
        editor,
        "proposal-edit-001",
    )
    assert edited.logical_http_status == 200
    submitted = execute(
        service,
        "SubmitProposal",
        action_id,
        SubmitProposalRequest(
            schema_version="1.0", expected_versions={"service_request": 2, "proposed_action": 2}
        ),
        creator,
        "proposal-submit-001",
    )
    assert submitted.logical_http_status == 200
    digest = submitted.safe_snapshot["result"]["payload_digest"]
    self_decision = execute(
        service,
        "ApproveProposal",
        action_id,
        DecideProposalRequest(
            schema_version="1.0",
            expected_versions={"service_request": 3, "proposed_action": 3},
            expected_payload_digest=digest,
        ),
        editor,
        "proposal-self-approve",
    )
    assert (self_decision.logical_http_status, self_decision.safe_snapshot["error"]["code"]) == (
        403,
        "SELF_APPROVAL_FORBIDDEN",
    )

    approved = execute(
        service,
        "ApproveProposal",
        action_id,
        DecideProposalRequest(
            schema_version="1.0",
            expected_versions={"service_request": 3, "proposed_action": 3},
            expected_payload_digest=digest,
        ),
        approver,
        "proposal-approve-001",
    )
    assert approved.safe_snapshot["result"]["service_request_status"] == "ActionPendingExecution"
    decision_id = approved.safe_snapshot["result"]["approval_decision_id"]
    approval_replay = execute(
        service,
        "ApproveProposal",
        action_id,
        DecideProposalRequest(
            schema_version="1.0",
            expected_versions={"service_request": 3, "proposed_action": 3},
            expected_payload_digest=digest,
        ),
        approver,
        "proposal-approve-001",
    )
    assert approval_replay.safe_snapshot["result"]["approval_decision_id"] == decision_id
    replacement = execute(
        service,
        "CreateMaterialRevision",
        action_id,
        MaterialRevisionRequest(
            schema_version="1.0",
            expected_versions={"service_request": 4, "proposed_action": 4},
            proposal=proposal("Replacement response"),
        ),
        creator,
        "proposal-revise-001",
    )
    assert replacement.logical_http_status == 201
    replacement_result = replacement.safe_snapshot["result"]
    assert replacement_result["logical_operation_id"] == operation_id
    assert replacement_result["proposal_series_id"] == result["proposal_series_id"]
    assert replacement_result["proposal_number"] == 2
    assert replacement_result["source_proposed_action_id"] == str(action_id)
    assert replacement_result["source_proposal_state"] == "Superseded"
    assert (
        replacement_result["replacement_proposed_action_id"]
        == replacement_result["proposed_action_id"]
    )
    assert replacement_result["replacement_proposal_state"] == "Draft"
    assert replacement_result["recovery_cleared"] is False
    replacement_id = uuid.UUID(replacement_result["proposed_action_id"])
    assert counts(
        engine,
        "proposed_actions",
        "logical_operations",
        "approval_decisions",
        "integration_attempts",
        "attempt_callback_credentials",
    ) == {
        "proposed_actions": 2,
        "logical_operations": 1,
        "approval_decisions": 1,
        "integration_attempts": 0,
        "attempt_callback_credentials": 0,
    }
    with engine.connect() as connection:
        validity = connection.execute(
            select(Base.metadata.tables["audit_events"].c.safe_metadata).where(
                Base.metadata.tables["audit_events"].c.event_name
                == "approval.execution_validity_lost"
            )
        ).scalar_one()
        assert validity["approval_decision_id"] == decision_id
        assert (
            connection.scalar(
                select(func.count())
                .select_from(Base.metadata.tables["proposal_approval_exclusions"])
                .where(
                    Base.metadata.tables["proposal_approval_exclusions"].c.proposed_action_id
                    == replacement_id
                )
            )
            == 0
        )


def _create_and_submit(service, ids, creator, *, prefix: str):
    created = execute(
        service,
        "CreateProposalDraft",
        ids["request"],
        CreateDraftRequest(
            schema_version="1.0",
            expected_versions={"service_request": 1},
            proposal=proposal(f"{prefix} source"),
        ),
        creator,
        f"{prefix}-create",
    )
    action_id = uuid.UUID(created.safe_snapshot["result"]["proposed_action_id"])
    submitted = execute(
        service,
        "SubmitProposal",
        action_id,
        SubmitProposalRequest(
            schema_version="1.0",
            expected_versions={"service_request": 2, "proposed_action": 1},
        ),
        creator,
        f"{prefix}-submit",
    )
    return action_id, submitted.safe_snapshot["result"]["payload_digest"]


def test_rejected_revision_has_decision_identity_and_no_false_superseded_event(engine) -> None:
    ids = seed(engine)
    service = ProposalLifecycleService(create_session_factory(engine))
    creator = actor(ids["creator"], "OperationsAgent")
    rejecter = actor(ids["approver"], "Administrator")
    action_id, digest = _create_and_submit(service, ids, creator, prefix="reject")
    command_body = RejectProposalRequest(
        schema_version="1.0",
        expected_versions={"service_request": 3, "proposed_action": 2},
        expected_payload_digest=digest,
        rationale="The proposed response requires a material correction.",
    )
    rejected = execute(
        service, "RejectProposal", action_id, command_body, rejecter, "reject-decision"
    )
    decision_id = rejected.safe_snapshot["result"]["approval_decision_id"]
    replay = execute(
        service, "RejectProposal", action_id, command_body, rejecter, "reject-decision"
    )
    assert replay.safe_snapshot["result"]["approval_decision_id"] == decision_id
    revised = execute(
        service,
        "CreateMaterialRevision",
        action_id,
        MaterialRevisionRequest(
            schema_version="1.0",
            expected_versions={"service_request": 4, "proposed_action": 3},
            proposal=proposal("Truthful rejected replacement"),
        ),
        creator,
        "reject-revision",
    )
    assert revised.safe_snapshot["result"]["source_proposal_state"] == "Rejected"
    tables = Base.metadata.tables
    with engine.connect() as connection:
        assert connection.scalar(
            select(tables["approval_decisions"].c.id).where(
                tables["approval_decisions"].c.id == uuid.UUID(decision_id)
            )
        ) == uuid.UUID(decision_id)
        false_audits = connection.scalar(
            select(func.count())
            .select_from(tables["audit_events"])
            .where(
                tables["audit_events"].c.aggregate_id == action_id,
                tables["audit_events"].c.event_name == "proposed_action.superseded",
            )
        )
        false_outbox = connection.scalar(
            select(func.count())
            .select_from(tables["outbox_messages"])
            .where(
                tables["outbox_messages"].c.aggregate_id == action_id,
                tables["outbox_messages"].c.event_type == "proposed_action.superseded",
            )
        )
        assert false_audits == false_outbox == 0


def test_pending_approval_with_existing_decision_fails_without_replacement(engine) -> None:
    ids = seed(engine)
    service = ProposalLifecycleService(create_session_factory(engine))
    creator = actor(ids["creator"], "OperationsAgent")
    action_id, digest = _create_and_submit(service, ids, creator, prefix="contradiction")
    tables = Base.metadata.tables
    with engine.begin() as connection:
        connection.execute(
            insert(tables["approval_decisions"]).values(
                id=uuid.uuid4(),
                proposed_action_id=action_id,
                proposal_number=1,
                payload_digest=digest,
                decision="Approved",
                approver_actor_id=ids["approver"],
                role_at_decision="Administrator",
                correlation_id=uuid.uuid4(),
                command_id=uuid.uuid4(),
            )
        )
    with engine.connect() as connection:
        before = (
            connection.scalar(select(func.count()).select_from(tables["proposed_actions"])),
            connection.scalar(select(func.count()).select_from(tables["outbox_messages"])),
        )
    outcome = execute(
        service,
        "CreateMaterialRevision",
        action_id,
        MaterialRevisionRequest(
            schema_version="1.0",
            expected_versions={"service_request": 3, "proposed_action": 2},
            proposal=proposal("Must not be created"),
        ),
        creator,
        "contradictory-revision",
    )
    assert outcome.safe_snapshot["error"]["code"] == "PROPOSAL_APPROVAL_GRAPH_INCONSISTENT"
    with engine.connect() as connection:
        after = (
            connection.scalar(select(func.count()).select_from(tables["proposed_actions"])),
            connection.scalar(select(func.count()).select_from(tables["outbox_messages"])),
        )
    assert after == before


def test_retryable_failure_revision_emits_prior_approval_identity_and_clears_recovery(
    engine,
) -> None:
    ids = seed(engine)
    service = ProposalLifecycleService(create_session_factory(engine))
    creator = actor(ids["creator"], "OperationsAgent")
    approver = actor(ids["approver"], "Administrator")
    action_id, digest = _create_and_submit(service, ids, creator, prefix="retryable")
    approved = execute(
        service,
        "ApproveProposal",
        action_id,
        DecideProposalRequest(
            schema_version="1.0",
            expected_versions={"service_request": 3, "proposed_action": 2},
            expected_payload_digest=digest,
        ),
        approver,
        "retryable-approve",
    )
    decision_id = approved.safe_snapshot["result"]["approval_decision_id"]
    tables = Base.metadata.tables
    operation_id, attempt_id = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            insert(tables["logical_operations"]).values(
                id=operation_id,
                service_request_id=ids["request"],
                operation_kind="AIInterpretation",
                input_hash="d" * 64,
                configuration_hash="e" * 64,
                prompt_version="recovery-prompt",
                result_schema_version="recovery-schema",
                provider_name="test-provider",
                model_name="test-model",
                adapter_name="test-adapter",
                adapter_version="1.0",
                version=1,
            )
        )
        connection.execute(
            insert(tables["integration_attempts"]).values(
                id=attempt_id,
                logical_operation_id=operation_id,
                service_request_id=ids["request"],
                operation_kind="AIInterpretation",
                attempt_number=1,
                state="Pending",
                version=1,
                adapter_name="test-adapter",
                adapter_version="1.0",
                assigned_workflow_service="workflow-test",
                workflow_environment="integration",
                callback_authorization_deadline=now + timedelta(hours=1),
            )
        )
        connection.execute(
            tables["proposed_actions"]
            .update()
            .where(tables["proposed_actions"].c.id == action_id)
            .values(state="RetryableExecutionFailure")
        )
        connection.execute(
            tables["service_requests"]
            .update()
            .where(tables["service_requests"].c.id == ids["request"])
            .values(
                status="RetryableFailure",
                current_queue="FailedRetryRequired",
                recovery_target="ActionPendingExecution",
                recovery_attempt_id=attempt_id,
                failure_summary_code="OUTBOUND_RETRYABLE",
            )
        )
    revised = execute(
        service,
        "CreateMaterialRevision",
        action_id,
        MaterialRevisionRequest(
            schema_version="1.0",
            expected_versions={"service_request": 4, "proposed_action": 3},
            proposal=proposal("Retryable replacement"),
        ),
        creator,
        "retryable-revision",
    )
    assert revised.safe_snapshot["result"]["recovery_cleared"] is True
    with engine.connect() as connection:
        evidence = connection.execute(
            select(tables["audit_events"].c.safe_metadata).where(
                tables["audit_events"].c.event_name == "approval.execution_validity_lost"
            )
        ).scalar_one()
        assert evidence["approval_decision_id"] == decision_id
        request_row = (
            connection.execute(
                select(tables["service_requests"]).where(
                    tables["service_requests"].c.id == ids["request"]
                )
            )
            .mappings()
            .one()
        )
        assert request_row["recovery_target"] is None
        assert request_row["recovery_attempt_id"] is None


def test_evidence_and_command_snapshots_exclude_content_and_rationale(engine) -> None:
    ids = seed(engine)
    service = ProposalLifecycleService(create_session_factory(engine))
    creator = actor(ids["creator"], "OperationsAgent")
    rejecter = actor(ids["approver"], "Administrator")
    sensitive_destination = "private-customer@example.test"
    sensitive_content = "Sensitive proposal content must stay operational only."
    sensitive_note = "Sensitive scheduling note must never enter evidence."
    sensitive_rationale = "Sensitive rejection rationale must remain digest-only evidence."
    payload = {
        "action_type": "SchedulingInvitation",
        "destination": {"kind": "Email", "value": sensitive_destination},
        "content": sensitive_content,
        "scheduling": {
            "window_start": "2026-08-01T09:00:00+08:00",
            "window_end": "2026-08-01T10:00:00+08:00",
            "notes": sensitive_note,
        },
    }
    created = execute(
        service,
        "CreateProposalDraft",
        ids["request"],
        CreateDraftRequest(
            schema_version="1.0",
            expected_versions={"service_request": 1},
            proposal=payload,
        ),
        creator,
        "safety-create",
    )
    action_id = uuid.UUID(created.safe_snapshot["result"]["proposed_action_id"])
    submitted = execute(
        service,
        "SubmitProposal",
        action_id,
        SubmitProposalRequest(
            schema_version="1.0",
            expected_versions={"service_request": 2, "proposed_action": 1},
        ),
        creator,
        "safety-submit",
    )
    execute(
        service,
        "RejectProposal",
        action_id,
        RejectProposalRequest(
            schema_version="1.0",
            expected_versions={"service_request": 3, "proposed_action": 2},
            expected_payload_digest=submitted.safe_snapshot["result"]["payload_digest"],
            rationale=sensitive_rationale,
        ),
        rejecter,
        "safety-reject",
    )
    tables = Base.metadata.tables
    with engine.connect() as connection:
        serialized = json.dumps(
            {
                "audit": connection.execute(select(tables["audit_events"].c.safe_metadata))
                .scalars()
                .all(),
                "outbox": connection.execute(select(tables["outbox_messages"].c.payload))
                .scalars()
                .all(),
                "commands": connection.execute(
                    select(tables["command_idempotency_records"].c.safe_response_snapshot)
                )
                .scalars()
                .all(),
            },
            sort_keys=True,
        )
    for forbidden in (
        sensitive_destination,
        sensitive_content,
        sensitive_note,
        sensitive_rationale,
        "Proposal fixture",
        "A proposal-ready request.",
    ):
        assert forbidden not in serialized
