"""Injected write failures prove proposal transactions leave no partial graph."""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from test_proposal_approval_lifecycle import (
    actor,
    execute,
    proposal,
    seed,
)

from ai_operations_automation.command_idempotency.service import CommandIdempotencyService
from ai_operations_automation.db import Base, create_session_factory
from ai_operations_automation.db.models.evidence import AuditEvent, OutboxMessage
from ai_operations_automation.db.models.proposal import (
    ApprovalDecision,
    ProposalApprovalExclusion,
    ProposedActionContributor,
)
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.proposal.models import (
    CreateDraftRequest,
    DecideProposalRequest,
    EditDraftRequest,
    MaterialRevisionRequest,
    SubmitProposalRequest,
)
from ai_operations_automation.proposal.service import ProposalLifecycleService

pytest_plugins = ["test_proposal_approval_lifecycle"]
pytestmark = pytest.mark.integration


def factory_failing_on(engine, model_type, *, predicate=lambda _item: True):
    failed = False

    class InjectedSession(Session):
        def flush(self, objects=None):
            nonlocal failed
            if not failed and any(
                isinstance(item, model_type) and predicate(item) for item in self.new
            ):
                failed = True
                raise RuntimeError("injected proposal write failure")
            return super().flush(objects)

    return sessionmaker(
        bind=engine,
        class_=InjectedSession,
        autoflush=False,
        expire_on_commit=False,
    )


def table_counts(engine, names):
    with engine.connect() as connection:
        return {
            name: int(
                connection.scalar(select(func.count()).select_from(Base.metadata.tables[name])) or 0
            )
            for name in names
        }


CREATE_GRAPH = (
    "proposed_actions",
    "proposed_action_contributors",
    "logical_operations",
    "audit_events",
    "outbox_messages",
    "command_idempotency_records",
)


@pytest.mark.parametrize("failure_model", [ProposedActionContributor, AuditEvent, OutboxMessage])
def test_first_draft_write_failure_rolls_back_operation_and_complete_graph(
    engine, failure_model
) -> None:
    ids = seed(engine)
    service = ProposalLifecycleService(factory_failing_on(engine, failure_model))
    command = CreateDraftRequest(
        schema_version="1.0",
        expected_versions={"service_request": 1},
        proposal=proposal("Rollback create"),
    )
    with pytest.raises(IntakeError):
        execute(
            service,
            "CreateProposalDraft",
            ids["request"],
            command,
            actor(ids["creator"], "OperationsAgent"),
            f"rollback-create-{failure_model.__name__}",
        )
    assert table_counts(engine, CREATE_GRAPH) == dict.fromkeys(CREATE_GRAPH, 0)
    request = Base.metadata.tables["service_requests"]
    with engine.connect() as connection:
        row = connection.execute(select(request)).mappings().one()
    assert row["version"] == 1 and row["current_proposed_action_id"] is None


def test_idempotency_completion_failure_rolls_back_first_draft(engine, monkeypatch) -> None:
    ids = seed(engine)
    service = ProposalLifecycleService(create_session_factory(engine))

    def fail_completion(*_args, **_kwargs):
        raise RuntimeError("injected completion failure")

    monkeypatch.setattr(CommandIdempotencyService, "complete", fail_completion)
    with pytest.raises(IntakeError):
        execute(
            service,
            "CreateProposalDraft",
            ids["request"],
            CreateDraftRequest(
                schema_version="1.0",
                expected_versions={"service_request": 1},
                proposal=proposal("Completion rollback"),
            ),
            actor(ids["creator"], "OperationsAgent"),
            "rollback-completion",
        )
    assert table_counts(engine, CREATE_GRAPH) == dict.fromkeys(CREATE_GRAPH, 0)


def _draft(service, ids, creator, *, prefix):
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
    return uuid.UUID(created.safe_snapshot["result"]["proposed_action_id"])


def test_exclusion_failure_preserves_unsubmitted_draft(engine) -> None:
    ids = seed(engine)
    creator = actor(ids["creator"], "OperationsAgent")
    normal = ProposalLifecycleService(create_session_factory(engine))
    action_id = _draft(normal, ids, creator, prefix="exclusion-rollback")
    before = table_counts(
        engine, ("audit_events", "outbox_messages", "command_idempotency_records")
    )
    failing = ProposalLifecycleService(factory_failing_on(engine, ProposalApprovalExclusion))
    with pytest.raises(IntakeError):
        execute(
            failing,
            "SubmitProposal",
            action_id,
            SubmitProposalRequest(
                schema_version="1.0",
                expected_versions={"service_request": 2, "proposed_action": 1},
            ),
            creator,
            "rollback-exclusion",
        )
    assert (
        table_counts(engine, ("proposal_approval_exclusions",))["proposal_approval_exclusions"] == 0
    )
    with engine.connect() as connection:
        proposal_row = (
            connection.execute(select(Base.metadata.tables["proposed_actions"])).mappings().one()
        )
        request_row = (
            connection.execute(select(Base.metadata.tables["service_requests"])).mappings().one()
        )
    assert proposal_row["state"] == "Draft" and proposal_row["version"] == 1
    assert request_row["status"] == "ReadyForAction" and request_row["version"] == 2
    assert table_counts(engine, before) == before


def test_decision_failure_preserves_pending_graph(engine) -> None:
    ids = seed(engine)
    creator = actor(ids["creator"], "OperationsAgent")
    approver = actor(ids["approver"], "Administrator")
    normal = ProposalLifecycleService(create_session_factory(engine))
    action_id = _draft(normal, ids, creator, prefix="decision-rollback")
    submitted = execute(
        normal,
        "SubmitProposal",
        action_id,
        SubmitProposalRequest(
            schema_version="1.0",
            expected_versions={"service_request": 2, "proposed_action": 1},
        ),
        creator,
        "decision-rollback-submit",
    )
    before = table_counts(
        engine, ("audit_events", "outbox_messages", "command_idempotency_records")
    )
    failing = ProposalLifecycleService(factory_failing_on(engine, ApprovalDecision))
    with pytest.raises(IntakeError):
        execute(
            failing,
            "ApproveProposal",
            action_id,
            DecideProposalRequest(
                schema_version="1.0",
                expected_versions={"service_request": 3, "proposed_action": 2},
                expected_payload_digest=submitted.safe_snapshot["result"]["payload_digest"],
            ),
            approver,
            "rollback-decision",
        )
    assert table_counts(engine, ("approval_decisions",))["approval_decisions"] == 0
    with engine.connect() as connection:
        proposal_row = (
            connection.execute(select(Base.metadata.tables["proposed_actions"])).mappings().one()
        )
        request_row = (
            connection.execute(select(Base.metadata.tables["service_requests"])).mappings().one()
        )
    assert proposal_row["state"] == "PendingApproval" and proposal_row["version"] == 2
    assert request_row["status"] == "AwaitingApproval" and request_row["version"] == 3
    assert table_counts(engine, before) == before


def test_carried_contributor_failure_preserves_revision_source(engine) -> None:
    ids = seed(engine)
    creator = actor(ids["creator"], "OperationsAgent")
    editor = actor(ids["editor"], "ManagerApprover")
    normal = ProposalLifecycleService(create_session_factory(engine))
    action_id = _draft(normal, ids, creator, prefix="revision-rollback")
    execute(
        normal,
        "EditProposalDraft",
        action_id,
        EditDraftRequest(
            schema_version="1.0",
            expected_versions={"proposed_action": 1},
            proposal=proposal("Contributor represented work"),
        ),
        editor,
        "revision-rollback-edit",
    )
    before = table_counts(
        engine,
        (
            "proposed_actions",
            "proposed_action_contributors",
            "audit_events",
            "outbox_messages",
            "command_idempotency_records",
        ),
    )
    failing = ProposalLifecycleService(
        factory_failing_on(
            engine,
            ProposedActionContributor,
            predicate=lambda item: item.carried_forward,
        )
    )
    with pytest.raises(IntakeError):
        execute(
            failing,
            "CreateMaterialRevision",
            action_id,
            MaterialRevisionRequest(
                schema_version="1.0",
                expected_versions={"service_request": 2, "proposed_action": 2},
                proposal=proposal("Replacement must roll back"),
            ),
            creator,
            "rollback-revision",
        )
    assert table_counts(engine, before) == before
    with engine.connect() as connection:
        proposal_row = (
            connection.execute(select(Base.metadata.tables["proposed_actions"])).mappings().one()
        )
        request_row = (
            connection.execute(select(Base.metadata.tables["service_requests"])).mappings().one()
        )
    assert proposal_row["state"] == "Draft" and proposal_row["superseded_by_id"] is None
    assert request_row["current_proposed_action_id"] == action_id and request_row["version"] == 2
