"""Checkpoint 3 proposal lifecycle on PostgreSQL."""

import uuid
from collections.abc import Iterator
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
