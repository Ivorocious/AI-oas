"""Independent-session races for proposal commands."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select
from test_proposal_approval_lifecycle import (
    actor,
    execute,
    proposal,
    seed,
)

from ai_operations_automation.db import Base, create_session_factory
from ai_operations_automation.proposal.models import (
    CreateDraftRequest,
    DecideProposalRequest,
    EditDraftRequest,
    MaterialRevisionRequest,
    RejectProposalRequest,
    SubmitProposalRequest,
)
from ai_operations_automation.proposal.service import ProposalLifecycleService

pytest_plugins = ["test_proposal_approval_lifecycle"]
pytestmark = pytest.mark.integration


def race(*calls):
    barrier = Barrier(len(calls))

    def run(call):
        barrier.wait()
        return call()

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return [future.result() for future in [pool.submit(run, call) for call in calls]]


def test_concurrent_first_draft_has_one_complete_winner(engine) -> None:
    ids = seed(engine)
    service = ProposalLifecycleService(create_session_factory(engine))
    user = actor(ids["creator"], "OperationsAgent")
    command = CreateDraftRequest(
        schema_version="1.0",
        expected_versions={"service_request": 1},
        proposal=proposal("Concurrent draft"),
    )
    outcomes = race(
        lambda: execute(
            service, "CreateProposalDraft", ids["request"], command, user, "race-create-a"
        ),
        lambda: execute(
            service, "CreateProposalDraft", ids["request"], command, user, "race-create-b"
        ),
    )
    assert sorted(item.logical_http_status for item in outcomes) == [201, 409]
    tables = Base.metadata.tables
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(tables["proposed_actions"])) == 1
        assert (
            connection.scalar(select(func.count()).select_from(tables["logical_operations"])) == 1
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(tables["proposed_actions"])
                .where(tables["proposed_actions"].c.state == "Draft")
            )
            == 1
        )


def _draft(service, ids, user):
    created = execute(
        service,
        "CreateProposalDraft",
        ids["request"],
        CreateDraftRequest(
            schema_version="1.0",
            expected_versions={"service_request": 1},
            proposal=proposal("Race source"),
        ),
        user,
        f"setup-{uuid.uuid4()}",
    )
    return uuid.UUID(created.safe_snapshot["result"]["proposed_action_id"])


def test_edit_versus_submission_never_partly_freezes(engine) -> None:
    ids = seed(engine)
    service = ProposalLifecycleService(create_session_factory(engine))
    creator = actor(ids["creator"], "OperationsAgent")
    editor = actor(ids["editor"], "ManagerApprover")
    action_id = _draft(service, ids, creator)
    outcomes = race(
        lambda: execute(
            service,
            "EditProposalDraft",
            action_id,
            EditDraftRequest(
                schema_version="1.0",
                expected_versions={"proposed_action": 1},
                proposal=proposal("Raced edit"),
            ),
            editor,
            "race-edit",
        ),
        lambda: execute(
            service,
            "SubmitProposal",
            action_id,
            SubmitProposalRequest(
                schema_version="1.0", expected_versions={"service_request": 2, "proposed_action": 1}
            ),
            creator,
            "race-submit",
        ),
    )
    assert sorted(item.logical_http_status for item in outcomes) == [200, 409]
    tables = Base.metadata.tables
    with engine.connect() as connection:
        row = connection.execute(select(tables["proposed_actions"])).mappings().one()
        exclusion_count = connection.scalar(
            select(func.count()).select_from(tables["proposal_approval_exclusions"])
        )
        contributor_count = connection.scalar(
            select(func.count()).select_from(tables["proposed_action_contributors"])
        )
        if row["state"] == "PendingApproval":
            assert row["content"] == "Race source"
            assert exclusion_count == contributor_count == 1
        else:
            assert row["state"] == "Draft" and row["content"] == "Raced edit"
            assert exclusion_count == 0 and contributor_count == 2


def test_approval_versus_rejection_has_one_decision(engine) -> None:
    ids = seed(engine)
    service = ProposalLifecycleService(create_session_factory(engine))
    creator = actor(ids["creator"], "OperationsAgent")
    approver = actor(ids["approver"], "Administrator")
    editor = actor(ids["editor"], "ManagerApprover")
    action_id = _draft(service, ids, creator)
    submitted = execute(
        service,
        "SubmitProposal",
        action_id,
        SubmitProposalRequest(
            schema_version="1.0", expected_versions={"service_request": 2, "proposed_action": 1}
        ),
        creator,
        "race-decision-submit",
    )
    digest = submitted.safe_snapshot["result"]["payload_digest"]
    outcomes = race(
        lambda: execute(
            service,
            "ApproveProposal",
            action_id,
            DecideProposalRequest(
                schema_version="1.0",
                expected_versions={"service_request": 3, "proposed_action": 2},
                expected_payload_digest=digest,
            ),
            approver,
            "race-approve",
        ),
        lambda: execute(
            service,
            "RejectProposal",
            action_id,
            RejectProposalRequest(
                schema_version="1.0",
                expected_versions={"service_request": 3, "proposed_action": 2},
                expected_payload_digest=digest,
                rationale="A bounded independent rejection rationale.",
            ),
            editor,
            "race-reject",
        ),
    )
    assert sorted(item.logical_http_status for item in outcomes) == [200, 409]
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(Base.metadata.tables["approval_decisions"])
            )
            == 1
        )
        state = connection.scalar(select(Base.metadata.tables["proposed_actions"].c.state))
        assert state in {"Approved", "Rejected"}


def test_concurrent_material_revision_reuses_one_operation(engine) -> None:
    ids = seed(engine)
    service = ProposalLifecycleService(create_session_factory(engine))
    creator = actor(ids["creator"], "OperationsAgent")
    action_id = _draft(service, ids, creator)
    command = MaterialRevisionRequest(
        schema_version="1.0",
        expected_versions={"service_request": 2, "proposed_action": 1},
        proposal=proposal("Concurrent replacement"),
    )
    outcomes = race(
        lambda: execute(
            service, "CreateMaterialRevision", action_id, command, creator, "race-revision-a"
        ),
        lambda: execute(
            service, "CreateMaterialRevision", action_id, command, creator, "race-revision-b"
        ),
    )
    assert sorted(item.logical_http_status for item in outcomes) == [201, 409]
    tables = Base.metadata.tables
    with engine.connect() as connection:
        rows = connection.execute(select(tables["proposed_actions"])).mappings().all()
        assert len(rows) == 2
        assert len({row["proposal_series_id"] for row in rows}) == 1
        assert len({row["logical_operation_id"] for row in rows}) == 1
        assert sum(row["state"] == "Draft" for row in rows) == 1
        assert (
            connection.scalar(select(func.count()).select_from(tables["logical_operations"])) == 1
        )
