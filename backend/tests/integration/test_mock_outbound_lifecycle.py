"""Focused PostgreSQL coverage for the mock outbound lifecycle."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, insert, select, text, update

from ai_operations_automation.app import create_app
from ai_operations_automation.attempt_callbacks.models import (
    OutboundRetryableFailureCallbackRequest,
    OutboundSuccessCallbackRequest,
)
from ai_operations_automation.attempt_callbacks.outbound_service import (
    OutboundAttemptCallbackService,
)
from ai_operations_automation.attempt_start.service import AttemptStartService
from ai_operations_automation.auth.dependencies import authenticated_query_principal
from ai_operations_automation.auth.models import AuthenticatedHuman
from ai_operations_automation.command_idempotency.canonicalization import canonical_command_hash
from ai_operations_automation.config import Settings
from ai_operations_automation.db import Base, create_database_engine, create_session_factory
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService
from ai_operations_automation.proposal.models import (
    CreateDraftRequest,
    DecideProposalRequest,
    SubmitProposalRequest,
)
from ai_operations_automation.proposal.service import ProposalLifecycleService
from ai_operations_automation.protected_queries import BackendProtectedQueryService
from ai_operations_automation.protected_queries.cursors import encode_cursor
from ai_operations_automation.retry_outbound.models import RetryOutboundRequest
from ai_operations_automation.retry_outbound.service import RetryOutboundService
from ai_operations_automation.stale_attempts.service import AssessStaleAttemptService
from ai_operations_automation.start_outbound.models import StartOutboundRequest
from ai_operations_automation.start_outbound.service import StartOutboundService
from alembic import command as alembic_command

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
CREDENTIAL = "A" * 43


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    alembic_command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
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


def _seed_approved(engine: Engine):
    ids = {name: uuid.uuid4() for name in ("contact", "delivery", "request", "creator", "approver")}
    tables = Base.metadata.tables
    with engine.begin() as connection:
        for actor in (ids["creator"], ids["approver"]):
            connection.execute(
                insert(tables["application_actors"]).values(
                    id=actor,
                    supabase_subject=str(actor),
                    display_label="Actor",
                    status="Active",
                    version=1,
                )
            )
        connection.execute(
            insert(tables["contacts"]).values(
                id=ids["contact"],
                display_label="Customer",
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
                normalized_request_description="Outbound fixture",
                status="ReadyForAction",
                current_queue="StandardRequests",
                priority="Normal",
                version=1,
            )
        )
    service = ProposalLifecycleService(create_session_factory(engine))
    creator = AuthenticatedHuman(ids["creator"], str(ids["creator"]), "OperationsAgent")
    approver = AuthenticatedHuman(ids["approver"], str(ids["approver"]), "Administrator")

    def execute(intent, target, body, actor, key):
        return service.execute(
            intent=intent,
            target_id=target,
            command=body,
            raw_idempotency_key=key,
            canonical_body_hash=canonical_command_hash(body),
            correlation_id=uuid.uuid4(),
            actor=actor,
        )

    proposal = {
        "action_type": "CustomerMessage",
        "destination": {"kind": "Email", "value": "customer@example.test"},
        "content": "This is simulated outbound content.",
        "scheduling": None,
    }
    created = execute(
        "CreateProposalDraft",
        ids["request"],
        CreateDraftRequest(
            schema_version="1.0", expected_versions={"service_request": 1}, proposal=proposal
        ),
        creator,
        "outbound-create",
    )
    action_id = uuid.UUID(created.safe_snapshot["result"]["proposed_action_id"])
    submitted = execute(
        "SubmitProposal",
        action_id,
        SubmitProposalRequest(
            schema_version="1.0", expected_versions={"service_request": 2, "proposed_action": 1}
        ),
        creator,
        "outbound-submit",
    )
    approved = execute(
        "ApproveProposal",
        action_id,
        DecideProposalRequest(
            schema_version="1.0",
            expected_versions={"service_request": 3, "proposed_action": 2},
            expected_payload_digest=submitted.safe_snapshot["result"]["payload_digest"],
        ),
        approver,
        "outbound-approve",
    )
    return action_id, approved


def _start_running(engine: Engine, prefix: str):
    action_id, approved = _seed_approved(engine)
    factory = create_session_factory(engine)
    machine = AuthenticatedWorkflowService(
        uuid.uuid4(), "workflow.outbound.test", "test", "WorkflowService", uuid.uuid4(), 1
    )
    start = StartOutboundRequest(
        schema_version="1.0",
        expected_versions={
            "service_request": approved.safe_snapshot["versions"]["service_request"],
            "proposed_action": approved.safe_snapshot["versions"]["proposed_action"],
        },
        command={},
    )
    started = StartOutboundService(factory, lambda: CREDENTIAL).execute(
        action_id=action_id,
        command=start,
        raw_idempotency_key=f"{prefix}-start-key",
        canonical_body_hash=canonical_command_hash(start),
        correlation_id=uuid.uuid4(),
        machine=machine,
    )
    attempt_id = uuid.UUID(started.safe_snapshot["result"]["integration_attempt_id"])
    AttemptStartService(factory).execute(
        attempt_id=attempt_id,
        expected_attempt_version=1,
        raw_idempotency_key=f"{prefix}-claim-key",
        canonical_body_hash="a" * 64,
        correlation_id=uuid.uuid4(),
        machine=machine,
    )
    return factory, machine, action_id, attempt_id


def test_start_claim_and_simulated_success_complete_exact_graph(engine: Engine) -> None:
    action_id, approved = _seed_approved(engine)
    factory = create_session_factory(engine)
    machine = AuthenticatedWorkflowService(
        uuid.uuid4(), "workflow.outbound.test", "test", "WorkflowService", uuid.uuid4(), 1
    )
    start_command = StartOutboundRequest(
        schema_version="1.0",
        expected_versions={
            "service_request": approved.safe_snapshot["versions"]["service_request"],
            "proposed_action": approved.safe_snapshot["versions"]["proposed_action"],
        },
        command={},
    )
    started = StartOutboundService(factory, lambda: CREDENTIAL).execute(
        action_id=action_id,
        command=start_command,
        raw_idempotency_key="start-outbound-key",
        canonical_body_hash=canonical_command_hash(start_command),
        correlation_id=uuid.uuid4(),
        machine=machine,
    )
    assert started.logical_http_status == 202
    assert started.callback_plaintext == CREDENTIAL
    attempt_id = uuid.UUID(started.safe_snapshot["result"]["integration_attempt_id"])
    claimed = AttemptStartService(factory).execute(
        attempt_id=attempt_id,
        expected_attempt_version=1,
        raw_idempotency_key="claim-outbound-key",
        canonical_body_hash="b" * 64,
        correlation_id=uuid.uuid4(),
        machine=machine,
    )
    assert claimed.safe_snapshot["result"]["operation_kind"] == "OutboundAction"
    callback = OutboundSuccessCallbackRequest(
        schema_version="1.0",
        expected_versions={"integration_attempt": 2},
        evidence={
            "result_schema_version": "mock-outbound-result-v1",
            "adapter_version": "1.0",
            "simulated_outcome": "Applied",
            "safe_provider_correlation": "mock-correlation-1",
            "safe_evidence_reference": "mock-evidence-1",
            "safe_evidence_hash": "c" * 64,
        },
    )
    outcome = OutboundAttemptCallbackService(factory).succeed(
        attempt_id=attempt_id,
        command=callback,
        raw_idempotency_key="outbound-success-key",
        canonical_body_hash=canonical_command_hash(callback),
        correlation_id=uuid.uuid4(),
        machine=machine,
        supplied_credential=CREDENTIAL,
    )
    assert outcome.safe_snapshot["result"]["attempt_state"] == "Succeeded"
    assert outcome.safe_snapshot["result"]["proposal_state"] == "Executed"
    assert outcome.safe_snapshot["result"]["service_request_status"] == "Completed"
    with engine.connect() as connection:
        attempt = (
            connection.execute(
                select(Base.metadata.tables["integration_attempts"]).where(
                    Base.metadata.tables["integration_attempts"].c.id == attempt_id
                )
            )
            .mappings()
            .one()
        )
        assert attempt["operation_kind"] == "OutboundAction"
        assert (
            attempt["proposal_payload_digest"]
            == started.safe_snapshot["result"]["proposal_payload_digest"]
        )


def test_assigned_workflow_query_projects_exact_outbound_proposal_and_attempt(
    engine: Engine,
) -> None:
    factory, machine, action_id, attempt_id = _start_running(engine, "query-scope")
    app = create_app(
        Settings(
            app_environment="test",
            protected_query_cursor_signing_key="synthetic-query-cursor-key-for-tests-only-0001",
            _env_file=None,
        ),
        factory,
    )
    app.dependency_overrides[authenticated_query_principal] = lambda: machine
    api = TestClient(app)

    proposal = api.get(f"/api/v1/proposed-actions/{action_id}")
    history = api.get(f"/api/v1/proposed-actions/{action_id}/integration-attempts")
    attempt = api.get(f"/api/v1/integration-attempts/{attempt_id}")
    assert proposal.status_code == history.status_code == attempt.status_code == 200
    assert proposal.json()["result"] == {
        **proposal.json()["result"],
        "id": str(action_id),
        "service_request_id": str(attempt.json()["result"]["service_request_id"]),
        "logical_operation_id": attempt.json()["result"]["logical_operation_id"],
        "approval_valid": True,
    }
    assert [item["id"] for item in history.json()["result"]["items"]] == [str(attempt_id)]
    assert history.json()["result"]["page"]["next_cursor"] is None
    combined = proposal.text + history.text + attempt.text
    for forbidden in (
        "credential_hash",
        "callback_credential",
        "assigned_workflow_service",
        "workflow_environment",
        "approval_rationale",
        "rationale_digest",
        "raw_provider",
    ):
        assert forbidden not in combined


def test_proposal_approval_and_attempt_lists_paginate_with_exact_assignment(
    engine: Engine,
) -> None:
    from tests.integration.test_protected_service_request import clone_row, walk_pages

    factory, machine, action_id, attempt_id = _start_running(engine, "query-pagination")
    tables = Base.metadata.tables
    fixed = datetime.now(UTC) - timedelta(days=2)
    proposal_ids = [action_id]
    attempt_ids = [attempt_id]
    with engine.begin() as connection:
        action = (
            connection.execute(
                select(tables["proposed_actions"]).where(
                    tables["proposed_actions"].c.id == action_id
                )
            )
            .mappings()
            .one()
        )
        operation = (
            connection.execute(
                select(tables["logical_operations"]).where(
                    tables["logical_operations"].c.id == action["logical_operation_id"]
                )
            )
            .mappings()
            .one()
        )
        approval = (
            connection.execute(
                select(tables["approval_decisions"]).where(
                    tables["approval_decisions"].c.id == action["current_approval_id"]
                )
            )
            .mappings()
            .one()
        )
        connection.execute(
            update(tables["proposed_actions"])
            .where(tables["proposed_actions"].c.id == action_id)
            .values(created_at=fixed, updated_at=fixed)
        )
        for number in (2, 3):
            series_id = uuid.uuid4()
            operation_id = uuid.uuid4()
            proposal_id = uuid.uuid4()
            clone_row(
                connection,
                tables["logical_operations"],
                operation["id"],
                id=operation_id,
                proposal_series_id=series_id,
                outbound_key_scope=None,
                outbound_key_digest=None,
                succeeded_attempt_id=None,
                safe_outcome_summary=None,
                created_at=fixed,
                updated_at=fixed,
            )
            clone_row(
                connection,
                tables["proposed_actions"],
                action_id,
                id=proposal_id,
                proposal_series_id=series_id,
                logical_operation_id=operation_id,
                proposal_number=1,
                state="Draft",
                payload_digest=("d" if number == 2 else "e") * 64,
                supersedes_id=None,
                superseded_by_id=None,
                current_approval_id=None,
                submitted_at=None,
                terminal_at=None,
                created_at=fixed,
                updated_at=fixed,
            )
            proposal_ids.append(proposal_id)

        connection.execute(
            update(tables["integration_attempts"])
            .where(tables["integration_attempts"].c.id == attempt_id)
            .values(created_at=fixed, updated_at=fixed, started_at=fixed)
        )
        for number, assigned_service in (
            (2, machine.stable_service_id),
            (3, "workflow.outbound.other"),
        ):
            cloned_id = uuid.uuid4()
            clone_row(
                connection,
                tables["integration_attempts"],
                attempt_id,
                id=cloned_id,
                attempt_number=number,
                state="TerminalFailure",
                assigned_workflow_service=assigned_service,
                callback_authorization_deadline=datetime.now(UTC) + timedelta(hours=1),
                created_at=fixed,
                updated_at=fixed,
                started_at=fixed,
                completed_at=fixed,
                result_hash=None,
                sanitized_error_code="SYNTHETIC_QUERY_FAILURE",
            )
            attempt_ids.append(cloned_id)

    human = AuthenticatedHuman(uuid.uuid4(), "query-pagination-admin", "Administrator")
    app = create_app(
        Settings(
            app_environment="test",
            protected_query_cursor_signing_key="synthetic-query-cursor-key-for-tests-only-0001",
            _env_file=None,
        ),
        factory,
    )
    app.dependency_overrides[authenticated_query_principal] = lambda: human
    api = TestClient(app)
    request_id = action["service_request_id"]
    proposals_path = f"/api/v1/service-requests/{request_id}/proposed-actions"
    attempts_path = f"/api/v1/proposed-actions/{action_id}/integration-attempts"
    approvals_path = f"/api/v1/proposed-actions/{action_id}/approvals"

    proposal_seen, proposal_cursor = walk_pages(api, proposals_path, {})
    attempt_seen, attempt_cursor = walk_pages(api, attempts_path, {})
    assert set(proposal_seen) == {str(value) for value in proposal_ids}
    assert set(attempt_seen) == {str(value) for value in attempt_ids}
    assert proposal_cursor is not None and attempt_cursor is not None
    assert api.get(f"{proposals_path}?limit=100").json()["result"]["page"] == {"next_cursor": None}
    assert api.get(f"{attempts_path}?limit=100").json()["result"]["page"] == {"next_cursor": None}

    approval_page = api.get(f"{approvals_path}?limit=1")
    assert approval_page.status_code == 200
    assert [item["id"] for item in approval_page.json()["result"]["items"]] == [str(approval["id"])]
    assert approval_page.json()["result"]["page"]["next_cursor"] is None
    assert api.get(f"{approvals_path}?limit=0").status_code == 400
    assert api.get(f"{approvals_path}?limit=101").status_code == 400
    approval_cursor = encode_cursor(
        b"synthetic-query-cursor-key-for-tests-only-0001",
        "proposal-approvals",
        {"action_id": str(action_id)},
        approval["decided_at"],
        str(approval["id"]),
        ordering="decided_at:desc,id:desc",
        principal_scope="human:Administrator",
    )
    exhausted = api.get(f"{approvals_path}?limit=1&cursor={approval_cursor}")
    assert exhausted.status_code == 200
    assert exhausted.json()["result"] == {"items": [], "page": {"next_cursor": None}}
    assert (
        api.get(
            f"/api/v1/proposed-actions/{proposal_ids[1]}/approvals?limit=1&cursor={approval_cursor}"
        ).status_code
        == 400
    )
    tampered = approval_cursor[:-1] + ("A" if approval_cursor[-1] != "A" else "B")
    assert api.get(f"{approvals_path}?limit=1&cursor={tampered}").status_code == 400

    backend = BackendProtectedQueryService(factory)
    assert {item.id for item in backend.list_proposed_actions(request_id).items} == set(
        proposal_ids
    )
    assert backend.get_proposed_action(action_id).id == action_id
    assert [item.id for item in backend.list_proposal_approvals(action_id).items] == [
        approval["id"]
    ]
    assert {item.id for item in backend.list_proposal_integration_attempts(action_id).items} == set(
        attempt_ids
    )
    assert backend.get_integration_attempt(attempt_id).id == attempt_id

    app.dependency_overrides[authenticated_query_principal] = lambda: machine
    workflow_first = api.get(f"{attempts_path}?limit=1")
    assert workflow_first.status_code == 200
    workflow_cursor = workflow_first.json()["result"]["page"]["next_cursor"]
    assert workflow_cursor is not None
    workflow_seen, _ = walk_pages(api, attempts_path, {})
    assert set(workflow_seen) == {str(attempt_ids[0]), str(attempt_ids[1])}
    assert api.get(f"/api/v1/proposed-actions/{proposal_ids[1]}").status_code == 404
    assert (
        api.get(f"/api/v1/proposed-actions/{proposal_ids[1]}/integration-attempts").status_code
        == 404
    )
    other_machine = AuthenticatedWorkflowService(
        uuid.uuid4(),
        "workflow.outbound.other",
        "test",
        "WorkflowService",
        uuid.uuid4(),
        1,
    )
    app.dependency_overrides[authenticated_query_principal] = lambda: other_machine
    assert api.get(f"{attempts_path}?limit=1&cursor={workflow_cursor}").status_code == 400
    other_history = api.get(attempts_path)
    assert other_history.status_code == 200
    assert [item["id"] for item in other_history.json()["result"]["items"]] == [str(attempt_ids[2])]


@pytest.mark.parametrize("role", ["OperationsAgent", "ManagerApprover", "Administrator"])
def test_each_human_role_reads_known_proposal_approval_and_attempt_graph(
    engine: Engine, role: str
) -> None:
    factory, _machine, action_id, attempt_id = _start_running(engine, f"query-human-{role.lower()}")
    human = AuthenticatedHuman(uuid.uuid4(), f"query-human-{role.lower()}", role)
    app = create_app(
        Settings(
            app_environment="test",
            protected_query_cursor_signing_key="synthetic-query-cursor-key-for-tests-only-0001",
            _env_file=None,
        ),
        factory,
    )
    app.dependency_overrides[authenticated_query_principal] = lambda: human
    api = TestClient(app)
    paths = (
        f"/api/v1/proposed-actions/{action_id}",
        f"/api/v1/proposed-actions/{action_id}/approvals",
        f"/api/v1/proposed-actions/{action_id}/integration-attempts",
        f"/api/v1/integration-attempts/{attempt_id}",
    )

    responses = [api.get(path) for path in paths]
    assert all(response.status_code == 200 for response in responses)
    approval = responses[1].json()["result"]["items"][0]
    if role == "OperationsAgent":
        assert "rationale_recorded" not in approval
    else:
        assert approval["rationale_recorded"] is False
    assert "rationale_digest" not in "".join(response.text for response in responses)


def test_known_not_applied_retry_reuses_operation_and_stable_key(engine: Engine) -> None:
    action_id, approved = _seed_approved(engine)
    factory = create_session_factory(engine)
    machine = AuthenticatedWorkflowService(
        uuid.uuid4(), "workflow.outbound.test", "test", "WorkflowService", uuid.uuid4(), 1
    )
    start_command = StartOutboundRequest(
        schema_version="1.0",
        expected_versions={
            "service_request": approved.safe_snapshot["versions"]["service_request"],
            "proposed_action": approved.safe_snapshot["versions"]["proposed_action"],
        },
        command={},
    )
    started = StartOutboundService(factory, lambda: CREDENTIAL).execute(
        action_id=action_id,
        command=start_command,
        raw_idempotency_key="start-outbound-retry",
        canonical_body_hash=canonical_command_hash(start_command),
        correlation_id=uuid.uuid4(),
        machine=machine,
    )
    attempt_id = uuid.UUID(started.safe_snapshot["result"]["integration_attempt_id"])
    AttemptStartService(factory).execute(
        attempt_id=attempt_id,
        expected_attempt_version=1,
        raw_idempotency_key="claim-outbound-retry",
        canonical_body_hash="d" * 64,
        correlation_id=uuid.uuid4(),
        machine=machine,
    )
    failed_command = OutboundRetryableFailureCallbackRequest(
        schema_version="1.0",
        expected_versions={"integration_attempt": 2},
        evidence={
            "failure_code": "WORKFLOW_FAILED_BEFORE_PROVIDER_INVOCATION",
            "adapter_version": "1.0",
            "failure_stage": "BeforeDispatch",
            "provider_invocation": "NotInvoked",
            "customer_side_effect": "KnownNotApplied",
            "safe_evidence_reference": "mock-failure-1",
            "safe_evidence_hash": "e" * 64,
        },
    )
    failed = OutboundAttemptCallbackService(factory).retryable_failure(
        attempt_id=attempt_id,
        command=failed_command,
        raw_idempotency_key="outbound-failure-key",
        canonical_body_hash=canonical_command_hash(failed_command),
        correlation_id=uuid.uuid4(),
        machine=machine,
        supplied_credential=CREDENTIAL,
    )
    assert failed.safe_snapshot["result"]["recovery_disposition"] == "RetrySameOperation"
    attempt_table = Base.metadata.tables["integration_attempts"]
    with engine.begin() as connection:
        prior = (
            connection.execute(select(attempt_table).where(attempt_table.c.id == attempt_id))
            .mappings()
            .one()
        )
        connection.execute(
            update(attempt_table)
            .where(attempt_table.c.id == attempt_id)
            .values(next_eligible_at=func.now())
        )
    retry_command = RetryOutboundRequest(
        schema_version="1.0",
        expected_versions={
            "service_request": failed.safe_snapshot["versions"]["service_request"],
            "proposed_action": failed.safe_snapshot["versions"]["proposed_action"],
        },
        command={
            "failed_attempt_id": attempt_id,
            "expected_failure_policy": {
                "policy_id": prior["failure_policy_id"],
                "semantic_version": prior["failure_policy_semantic_version"],
                "revision": prior["failure_policy_revision"],
                "content_digest": prior["failure_policy_digest"],
            },
        },
    )
    retried = RetryOutboundService(factory, lambda: "B" * 43).execute(
        action_id=action_id,
        command=retry_command,
        raw_idempotency_key="retry-outbound-key",
        canonical_body_hash=canonical_command_hash(retry_command),
        correlation_id=uuid.uuid4(),
        authority=machine,
    )
    assert retried.safe_snapshot["result"]["attempt_number"] == 2
    assert (
        retried.safe_snapshot["result"]["stable_outbound_key_reference"]
        == started.safe_snapshot["result"]["stable_outbound_key_reference"]
    )


def test_unknown_outcome_stays_running_then_terminalizes_at_deadline(engine: Engine) -> None:
    factory, machine, _action_id, attempt_id = _start_running(engine, "uncertain")
    uncertain = OutboundRetryableFailureCallbackRequest(
        schema_version="1.0",
        expected_versions={"integration_attempt": 2},
        evidence={
            "failure_code": "PROVIDER_TIMEOUT",
            "adapter_version": "1.0",
            "failure_stage": "ProviderProcessing",
            "provider_invocation": "Invoked",
            "customer_side_effect": "Unknown",
            "safe_evidence_reference": "mock-uncertainty-1",
            "safe_evidence_hash": "f" * 64,
        },
    )
    outcome = OutboundAttemptCallbackService(factory).retryable_failure(
        attempt_id=attempt_id,
        command=uncertain,
        raw_idempotency_key="uncertain-callback-key",
        canonical_body_hash=canonical_command_hash(uncertain),
        correlation_id=uuid.uuid4(),
        machine=machine,
        supplied_credential=CREDENTIAL,
    )
    assert outcome.safe_snapshot["result"]["attempt_state"] == "Running"
    assert outcome.safe_snapshot["result"]["recovery_disposition"] == "ReconcileBeforeRetry"
    attempt_table = Base.metadata.tables["integration_attempts"]
    with engine.begin() as connection:
        connection.execute(
            update(attempt_table)
            .where(attempt_table.c.id == attempt_id)
            .values(
                started_at=func.now() - text("interval '15 minutes'"),
                reconciliation_deadline=func.now(),
            )
        )
    assessed = AssessStaleAttemptService(factory).execute(
        attempt_id=attempt_id,
        expected_attempt_version=3,
        durable_command_key="uncertain-deadline-key",
        correlation_id=uuid.uuid4(),
    )
    assert assessed.safe_snapshot["result"]["attempt_state"] == "TerminalFailure"
    assert assessed.safe_snapshot["result"]["failure_code"] == "OUTBOUND_OUTCOME_UNRESOLVED"
