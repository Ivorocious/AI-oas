"""Focused transport contracts for all six production proposal routes."""

import json
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert
from test_proposal_approval_lifecycle import (
    actor,
    execute,
    proposal,
    seed,
)

from ai_operations_automation.app import create_app
from ai_operations_automation.auth.verifier import AuthenticationFailure
from ai_operations_automation.config import Settings
from ai_operations_automation.db import Base, create_session_factory
from ai_operations_automation.proposal.models import (
    CreateDraftRequest,
    SubmitProposalRequest,
)
from ai_operations_automation.proposal.service import ProposalLifecycleService

pytest_plugins = ["test_proposal_approval_lifecycle"]
pytestmark = pytest.mark.integration


class TokenVerifier:
    def verify(self, token: str) -> str:
        if token == "invalid":
            raise AuthenticationFailure
        return token


def grant(engine, actor_id, role):
    with engine.begin() as connection:
        connection.execute(
            insert(Base.metadata.tables["application_actor_role_assignments"]).values(
                id=uuid.uuid4(),
                actor_id=actor_id,
                role=role,
                assigned_by_actor_id=actor_id,
                effective_from=datetime.now(UTC),
                assignment_reason="proposal HTTP contract",
            )
        )


def client(engine):
    return TestClient(
        create_app(
            Settings(_env_file=None),
            create_session_factory(engine),
            jwt_verifier=TokenVerifier(),
        )
    )


def headers(actor_id, key="proposal-http-key"):
    return {
        "Authorization": f"Bearer {actor_id}",
        "Idempotency-Key": key,
        "X-Correlation-ID": str(uuid.uuid4()),
    }


def create_body(content="HTTP proposal", version=1):
    return {
        "schema_version": "1.0",
        "expected_versions": {"service_request": version},
        "proposal": proposal(content),
    }


SIX_ROUTES = (
    ("post", "/api/v1/service-requests/{request_id}/proposed-actions"),
    ("put", "/api/v1/proposed-actions/{action_id}/draft"),
    ("post", "/api/v1/proposed-actions/{action_id}/commands/submit-for-approval"),
    ("post", "/api/v1/proposed-actions/{action_id}/commands/approve"),
    ("post", "/api/v1/proposed-actions/{action_id}/commands/reject"),
    ("post", "/api/v1/proposed-actions/{action_id}/commands/create-material-revision"),
)


@pytest.mark.parametrize(("method", "template"), SIX_ROUTES)
def test_all_proposal_routes_require_bearer_authentication(engine, method, template) -> None:
    response = client(engine).request(
        method, template.format(request_id=uuid.uuid4(), action_id=uuid.uuid4()), json={}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_invalid_bearer_and_concealed_uuid_fail_safely(engine) -> None:
    ids = seed(engine)
    grant(engine, ids["creator"], "OperationsAgent")
    api = client(engine)
    invalid = api.post(
        f"/api/v1/service-requests/{ids['request']}/proposed-actions",
        headers={"Authorization": "Bearer invalid", "Idempotency-Key": "invalid-token-key"},
        json=create_body(),
    )
    assert invalid.status_code == 401
    malformed = api.post(
        "/api/v1/service-requests/not-a-uuid/proposed-actions",
        headers=headers(ids["creator"], "malformed-id-key"),
        json=create_body(),
    )
    assert malformed.status_code == 404
    assert malformed.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_closed_body_versions_media_type_and_idempotency_contract(engine) -> None:
    ids = seed(engine)
    grant(engine, ids["creator"], "OperationsAgent")
    api = client(engine)
    unknown = create_body()
    unknown["actor_id"] = str(ids["creator"])
    assert (
        api.post(
            f"/api/v1/service-requests/{ids['request']}/proposed-actions",
            headers=headers(ids["creator"], "unknown-field-key"),
            json=unknown,
        ).status_code
        == 422
    )
    boolean_version = create_body(version=True)
    assert (
        api.post(
            f"/api/v1/service-requests/{ids['request']}/proposed-actions",
            headers=headers(ids["creator"], "boolean-version-key"),
            json=boolean_version,
        ).status_code
        == 422
    )
    unsupported = api.post(
        f"/api/v1/service-requests/{ids['request']}/proposed-actions",
        headers=headers(ids["creator"], "unsupported-media-key"),
        content=json.dumps(create_body()),
    )
    assert unsupported.status_code == 415
    missing_key = api.post(
        f"/api/v1/service-requests/{ids['request']}/proposed-actions",
        headers={"Authorization": f"Bearer {ids['creator']}"},
        json=create_body(),
    )
    assert missing_key.status_code == 400
    first = api.post(
        f"/api/v1/service-requests/{ids['request']}/proposed-actions",
        headers=headers(ids["creator"], "changed-body-key"),
        json=create_body("First body"),
    )
    assert first.status_code == 201
    changed = api.post(
        f"/api/v1/service-requests/{ids['request']}/proposed-actions",
        headers=headers(ids["creator"], "changed-body-key"),
        json=create_body("Changed body"),
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "COMMAND_IDEMPOTENCY_CONFLICT"


def _pending(engine, ids):
    service = ProposalLifecycleService(create_session_factory(engine))
    creator = actor(ids["creator"], "OperationsAgent")
    created = execute(
        service,
        "CreateProposalDraft",
        ids["request"],
        CreateDraftRequest(
            schema_version="1.0",
            expected_versions={"service_request": 1},
            proposal=proposal("HTTP decision source"),
        ),
        creator,
        "http-setup-create",
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
        "http-setup-submit",
    )
    return action_id, submitted.safe_snapshot["result"]["payload_digest"]


@pytest.mark.parametrize("command_name", ["approve", "reject"])
def test_operations_agent_cannot_decide(engine, command_name) -> None:
    ids = seed(engine)
    grant(engine, ids["editor"], "OperationsAgent")
    action_id, digest = _pending(engine, ids)
    body = {
        "schema_version": "1.0",
        "expected_versions": {"service_request": 3, "proposed_action": 2},
        "expected_payload_digest": digest,
    }
    if command_name == "reject":
        body["rationale"] = "A sufficiently bounded transport rejection rationale."
    response = client(engine).post(
        f"/api/v1/proposed-actions/{action_id}/commands/{command_name}",
        headers=headers(ids["editor"], f"ops-{command_name}-key"),
        json=body,
    )
    assert response.status_code == 403


@pytest.mark.parametrize("command_name", ["approve", "reject"])
def test_decision_first_execution_and_replay_return_same_uuid(engine, command_name) -> None:
    ids = seed(engine)
    grant(engine, ids["approver"], "Administrator")
    action_id, digest = _pending(engine, ids)
    body = {
        "schema_version": "1.0",
        "expected_versions": {"service_request": 3, "proposed_action": 2},
        "expected_payload_digest": digest,
    }
    if command_name == "reject":
        body["rationale"] = "A sufficiently bounded transport rejection rationale."
    key = f"http-{command_name}-replay"
    first = client(engine).post(
        f"/api/v1/proposed-actions/{action_id}/commands/{command_name}",
        headers=headers(ids["approver"], key),
        json=body,
    )
    replay = client(engine).post(
        f"/api/v1/proposed-actions/{action_id}/commands/{command_name}",
        headers=headers(ids["approver"], key),
        json=body,
    )
    assert first.status_code == replay.status_code == 200
    assert (
        first.json()["result"]["approval_decision_id"]
        == replay.json()["result"]["approval_decision_id"]
    )
    assert first.json()["correlation_id"] != replay.json()["correlation_id"]


def test_material_revision_http_response_is_closed_and_identifies_both_versions(engine) -> None:
    ids = seed(engine)
    grant(engine, ids["creator"], "OperationsAgent")
    service = ProposalLifecycleService(create_session_factory(engine))
    created = execute(
        service,
        "CreateProposalDraft",
        ids["request"],
        CreateDraftRequest(
            schema_version="1.0",
            expected_versions={"service_request": 1},
            proposal=proposal("HTTP revision source"),
        ),
        actor(ids["creator"], "OperationsAgent"),
        "http-revision-create",
    )
    action_id = created.safe_snapshot["result"]["proposed_action_id"]
    response = client(engine).post(
        f"/api/v1/proposed-actions/{action_id}/commands/create-material-revision",
        headers=headers(ids["creator"], "http-revision-key"),
        json={
            "schema_version": "1.0",
            "expected_versions": {"service_request": 2, "proposed_action": 1},
            "proposal": proposal("HTTP replacement"),
        },
    )
    assert response.status_code == 201
    result = response.json()["result"]
    assert result["source_proposed_action_id"] == action_id
    assert result["source_proposal_state"] == "Superseded"
    assert result["replacement_proposed_action_id"] == result["proposed_action_id"]
    assert result["replacement_proposal_state"] == "Draft"
    assert result["recovery_cleared"] is False
    assert len(create_app(Settings(_env_file=None)).openapi()["paths"]) == 19
