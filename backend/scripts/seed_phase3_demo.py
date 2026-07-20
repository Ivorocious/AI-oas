"""Create the isolated Phase 3 demo graph through accepted application paths only.

The only SQL fixture rows are active human roles and one active WorkflowService
credential.  No actor/role management command exists in this repository; these
rows are constrained by the same PK/FK/unique assignment constraints exercised
by the Phase 2 integration fixtures.  All request lifecycle state is created by
intake, authenticated AI commands/callback, CompleteTriage, and proposal routes.
"""

import argparse
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import insert, text

from ai_operations_automation.app import create_app
from ai_operations_automation.config import Settings
from ai_operations_automation.db import Base, create_database_engine, create_session_factory
from ai_operations_automation.deterministic_decision import DEMO_DECISION_POLICY
from ai_operations_automation.machine_auth.authenticator import calculate_signature
from ai_operations_automation.machine_auth.canonicalization import canonical_signing_bytes
from ai_operations_automation.triage.models import (
    AuthoritativeDecisionFacts,
    CompleteTriageCommand,
    ExpectedDecisionPolicy,
)
from ai_operations_automation.triage.service import CompleteTriageService
from alembic import command

DEMO_DATABASE_PREFIX = "ai_ops_phase3_demo_"
WORKFLOW_SERVICE_ID = "workflow.phase3.demo"
WORKFLOW_REFERENCE = "phase3/local-demo-workflow"
_MACHINE_SECRET = b"phase3-demo-ephemeral-machine-secret"


class _Resolver:
    def resolve(self, reference: str) -> bytes:
        if reference != WORKFLOW_REFERENCE:
            raise RuntimeError("unknown local demo workflow reference")
        return _MACHINE_SECRET


def _require_isolated_target(settings: Settings, confirmation: str) -> str:
    database = settings.database_url.path.lstrip("/")
    if settings.app_environment != "local" or not settings.demo_auth_enabled:
        raise ValueError("local environment and explicit demo authentication are required")
    hosts = {item["host"] for item in settings.database_url.hosts()}
    if hosts - {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("database host must be loopback")
    if not database.startswith(DEMO_DATABASE_PREFIX) or confirmation != database:
        raise ValueError("database identity is not the explicitly confirmed Phase 3 demo target")
    return database


def _machine_headers(method: str, path: str, body: dict, nonce: int) -> dict[str, str]:
    raw = json.dumps(body, separators=(",", ":")).encode()
    timestamp = str(int(datetime.now(UTC).timestamp()))
    nonce_value = f"phase3-demo-nonce-{nonce}"
    signature = calculate_signature(
        _MACHINE_SECRET,
        canonical_signing_bytes(method, path.encode(), b"", timestamp, nonce_value, raw),
    )
    return {
        "Content-Type": "application/json",
        "Idempotency-Key": f"phase3-demo-machine-{nonce}",
        "X-Service-ID": WORKFLOW_SERVICE_ID,
        "X-Service-Timestamp": timestamp,
        "X-Service-Nonce": nonce_value,
        "X-Service-Signature": signature,
    }


def _token(client: TestClient, persona: str) -> str:
    response = client.post("/demo-auth/token", json={"persona": persona})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _human_headers(token: str, key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": key}


def _machine_post(
    client: TestClient, path: str, body: dict, nonce: int, credential: str | None = None
):
    headers = _machine_headers("POST", path, body, nonce)
    if credential is not None:
        headers["X-Attempt-Callback-Credential"] = credential
    return client.post(
        path, headers=headers, content=json.dumps(body, separators=(",", ":")).encode()
    )


def _fixture_identities(engine) -> None:
    """Create the narrow authorization fixture where no management service exists."""
    now = datetime.now(UTC)
    manager_id, operations_id, machine_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    tables = Base.metadata.tables
    with engine.begin() as connection:
        for actor_id, subject, label, role in (
            (manager_id, "demo-manager", "Demo ManagerApprover", "ManagerApprover"),
            (operations_id, "demo-operations", "Demo OperationsAgent", "OperationsAgent"),
        ):
            connection.execute(
                insert(tables["application_actors"]).values(
                    id=actor_id,
                    supabase_subject=subject,
                    display_label=label,
                    status="Active",
                    version=1,
                )
            )
            connection.execute(
                insert(tables["application_actor_role_assignments"]).values(
                    id=uuid.uuid4(),
                    actor_id=actor_id,
                    role=role,
                    assigned_by_actor_id=manager_id,
                    effective_from=now - timedelta(seconds=1),
                    assignment_reason="isolated Phase 3 local portfolio fixture",
                )
            )
        connection.execute(
            insert(tables["machine_identities"]).values(
                id=machine_id,
                service_type="WorkflowService",
                environment="local",
                stable_service_id=WORKFLOW_SERVICE_ID,
                display_label="Isolated Phase 3 synthetic workflow",
                status="Active",
                version=1,
            )
        )
        connection.execute(
            insert(tables["machine_credential_versions"]).values(
                id=uuid.uuid4(),
                machine_identity_id=machine_id,
                credential_version=1,
                external_secret_reference=WORKFLOW_REFERENCE,
                status="Current",
                activated_at=now - timedelta(seconds=1),
            )
        )


def seed(settings: Settings, database: str) -> uuid.UUID:
    engine = create_database_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            if connection.scalar(text("select current_database()")) != database:
                raise ValueError("connected database does not match confirmed target")
        preserved = {"decision_policy_versions", "failure_recovery_policy_versions"}
        names = [name for name in Base.metadata.tables if name not in preserved]
        with engine.begin() as connection:
            connection.execute(
                text("TRUNCATE " + ", ".join(f'"{name}"' for name in names) + " CASCADE")
            )
        _fixture_identities(engine)
        app = create_app(
            settings, create_session_factory(engine), machine_secret_resolver=_Resolver()
        )
        client = TestClient(app, client=("127.0.0.1", 55001))
        operations_token = _token(client, "operations")

        intake = client.post(
            "/api/v1/intake/service-requests",
            headers={"Idempotency-Key": "phase3-demo-intake"},
            json={
                "schema_version": "1.0",
                "contact": {
                    "display_name": "Riley Chen",
                    "email": "riley.chen@example.com",
                    "preferred_channel": "Email",
                },
                "service_request": {
                    "description": "Synthetic air-conditioning service request requiring approval.",
                    "location_context": "Synthetic downtown office",
                    "timing_preference": "Weekday morning",
                },
            },
        )
        assert intake.status_code == 201, intake.text
        request_id = uuid.UUID(intake.json()["result"]["service_request_id"])

        start_path = f"/api/v1/service-requests/{request_id}/commands/start-ai-interpretation"
        started = _machine_post(
            client,
            start_path,
            {"schema_version": "1.0", "expected_versions": {"service_request": 1}, "command": {}},
            1,
        )
        assert started.status_code == 202, started.text
        start_result = started.json()["result"]
        attempt_id = start_result["integration_attempt_id"]
        credential = start_result["callback_credential"]
        claim_path = f"/api/v1/integration-attempts/{attempt_id}/commands/start"
        claimed = _machine_post(
            client,
            claim_path,
            {
                "schema_version": "1.0",
                "expected_versions": {"integration_attempt": 1},
                "command": {},
            },
            2,
        )
        assert claimed.status_code == 200, claimed.text
        callback_path = f"/api/v1/integration-attempts/{attempt_id}/callbacks/succeeded"
        callback = {
            "schema_version": "1.0",
            "expected_versions": {"integration_attempt": 2},
            "evidence": {
                "result_schema_version": settings.ai_interpretation_result_schema_version,
                "adapter_version": settings.ai_adapter_version,
                "safe_provider_correlation": "phase3-demo-ai",
                "latency_ms": 25,
                "token_usage": {"input_tokens": 12, "output_tokens": 8},
                "interpretation": {
                    "summary": "Synthetic bounded repair interpretation.",
                    "suggested_category": "Repair",
                    "missing_information": [],
                    "confidence": "0.9300",
                    "warning_codes": [],
                },
            },
        }
        succeeded = _machine_post(client, callback_path, callback, 3, credential)
        assert succeeded.status_code == 200, succeeded.text

        current = client.get(
            f"/api/v1/service-requests/{request_id}",
            headers={"Authorization": f"Bearer {operations_token}"},
        )
        assert current.status_code == 200, current.text
        triage = CompleteTriageService(create_session_factory(engine)).execute(
            request_id=request_id,
            command=CompleteTriageCommand(
                expected_service_request_version=current.json()["result"]["service_request"][
                    "version"
                ],
                facts=AuthoritativeDecisionFacts(
                    explicit_category="Repair",
                    requested_deadline=datetime.now(UTC) + timedelta(days=3),
                    service_mode="OnSite",
                    access_constraints_known=True,
                    repair_symptoms_present=True,
                    repair_asset_context_present=True,
                ),
                expected_policy=ExpectedDecisionPolicy(
                    policy_key=DEMO_DECISION_POLICY.policy_key,
                    semantic_version=DEMO_DECISION_POLICY.semantic_version,
                    revision=DEMO_DECISION_POLICY.revision,
                ),
            ),
            durable_command_key="phase3-demo-complete-triage",
            correlation_id=uuid.uuid4(),
        )
        assert triage.logical_http_status == 200, triage.safe_snapshot
        current = client.get(
            f"/api/v1/service-requests/{request_id}",
            headers={"Authorization": f"Bearer {operations_token}"},
        )
        request_version = current.json()["result"]["service_request"]["version"]
        created = client.post(
            f"/api/v1/service-requests/{request_id}/proposed-actions",
            headers=_human_headers(operations_token, "phase3-demo-create"),
            json={
                "schema_version": "1.0",
                "expected_versions": {"service_request": request_version},
                "proposal": {
                    "action_type": "CustomerMessage",
                    "destination": {"kind": "Email", "value": "riley.chen@example.com"},
                    "content": (
                        "A synthetic technician visit is proposed for the next available "
                        "service window."
                    ),
                    "scheduling": None,
                },
            },
        )
        assert created.status_code == 201, created.text
        action_id = created.json()["result"]["proposed_action_id"]
        proposal = client.get(
            f"/api/v1/proposed-actions/{action_id}",
            headers={"Authorization": f"Bearer {operations_token}"},
        )
        assert proposal.status_code == 200, proposal.text
        submitted = client.post(
            f"/api/v1/proposed-actions/{action_id}/commands/submit-for-approval",
            headers=_human_headers(operations_token, "phase3-demo-submit"),
            json={
                "schema_version": "1.0",
                "expected_versions": {
                    "service_request": created.json()["versions"]["service_request"],
                    "proposed_action": proposal.json()["result"]["version"],
                },
            },
        )
        assert submitted.status_code == 200, submitted.text
        return request_id
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-database", required=True)
    args = parser.parse_args()
    if os.environ.get("AI_OPS_DEMO_AUTH_ENABLED", "").lower() != "true":
        raise SystemExit("AI_OPS_DEMO_AUTH_ENABLED=true is required")
    settings = Settings()
    database = _require_isolated_target(settings, args.confirm_database)
    command.upgrade(Config("alembic.ini"), "head")
    print(seed(settings, database))
    return 0


if __name__ == "__main__":
    sys.exit(main())
