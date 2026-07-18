"""Coherent Phase 2 acceptance scenarios over the real API and trusted services."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, insert, select, text, update

from ai_operations_automation.app import create_app
from ai_operations_automation.auth.verifier import AuthenticationFailure
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
from alembic import command as alembic_command

pytestmark = pytest.mark.integration
BACKEND_ROOT = Path(__file__).resolve().parents[2]
SERVICE_ID = "workflow.phase2-acceptance.test"
SECRET_REFERENCE = "test/phase2-acceptance-current"
MACHINE_SECRET = b"synthetic-phase2-acceptance-machine-secret"
CURSOR_KEY = "synthetic-phase2-acceptance-cursor-key-only"
POLICY_TABLES = {"decision_policy_versions", "failure_recovery_policy_versions"}
FORBIDDEN_QUERY_TERMS = (
    "callback_credential",
    "credential_hash",
    "raw_idempotency_key",
    "x-service-signature",
    "machine_secret",
    "nonce_digest",
    "raw_provider_payload",
)


class TokenVerifier:
    def verify(self, token: str) -> str:
        if token == "invalid":
            raise AuthenticationFailure
        return token


class Resolver:
    def resolve(self, reference: str) -> bytes:
        if reference != SECRET_REFERENCE:
            raise RuntimeError("unknown synthetic secret reference")
        return MACHINE_SECRET


class CredentialGenerator:
    def __init__(self) -> None:
        self._calls = 0
        self._lock = threading.Lock()

    def __call__(self) -> str:
        with self._lock:
            self._calls += 1
            character = chr(64 + self._calls)
        return character * 43


@dataclass(frozen=True, slots=True)
class AiRun:
    request_id: uuid.UUID
    operation_id: uuid.UUID
    attempt_id: uuid.UUID
    callback_credential: str


@dataclass(frozen=True, slots=True)
class ProposalRun:
    action_id: uuid.UUID
    payload_digest: str


@dataclass(slots=True)
class ScenarioContext:
    client: TestClient
    engine: Engine
    settings: Settings
    clock: datetime
    nonce_counter: int = 0

    def human_headers(self, subject: str, key: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {subject}",
            "X-Correlation-ID": str(uuid.uuid4()),
        }
        if key is not None:
            headers["Idempotency-Key"] = key
        return headers

    def machine_request(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        key: str | None = None,
        callback_credential: str | None = None,
        query: str = "",
    ):
        self.nonce_counter += 1
        nonce = f"phase2-scenario-nonce-{self.nonce_counter:04d}"
        body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        timestamp = str(int(self.clock.timestamp()))
        signature = calculate_signature(
            MACHINE_SECRET,
            canonical_signing_bytes(
                method,
                path.encode(),
                query.encode(),
                timestamp,
                nonce,
                body,
            ),
        )
        headers = {
            "X-Service-ID": SERVICE_ID,
            "X-Service-Timestamp": timestamp,
            "X-Service-Nonce": nonce,
            "X-Service-Signature": signature,
            "X-Correlation-ID": str(uuid.uuid4()),
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if key is not None:
            headers["Idempotency-Key"] = key
        if callback_credential is not None:
            headers["X-Attempt-Callback-Credential"] = callback_credential
        target = path if not query else f"{path}?{query}"
        return self.client.request(method, target, content=body or None, headers=headers)

    def intake(
        self,
        suffix: str,
        *,
        description: str,
        email: str | None = None,
        key: str | None = None,
    ):
        synthetic_phone = f"+63917{sum(ord(character) for character in suffix) % 10_000_000:07d}"
        payload = {
            "schema_version": "1.0",
            "contact": {
                "display_name": f"Synthetic {suffix}",
                "email": email or f"{suffix}@example.com",
                "phone": synthetic_phone,
                "preferred_channel": "Email",
            },
            "service_request": {
                "description": description,
                "location_context": f"Synthetic site {suffix}",
                "timing_preference": "Weekday morning",
            },
        }
        return self.client.post(
            "/api/v1/intake/service-requests",
            headers={"Idempotency-Key": key or f"intake-{suffix}"},
            json=payload,
        )

    def start_ai(self, request_id: uuid.UUID, suffix: str) -> AiRun:
        start_path = f"/api/v1/service-requests/{request_id}/commands/start-ai-interpretation"
        started = self.machine_request(
            "POST",
            start_path,
            payload={
                "schema_version": "1.0",
                "expected_versions": {
                    "service_request": self.row("service_requests", request_id)["version"]
                },
                "command": {},
            },
            key=f"start-ai-{suffix}",
        )
        assert started.status_code == 202, started.text
        result = started.json()["result"]
        attempt_id = uuid.UUID(result["integration_attempt_id"])
        claim_path = f"/api/v1/integration-attempts/{attempt_id}/commands/start"
        claimed = self.machine_request(
            "POST",
            claim_path,
            payload={
                "schema_version": "1.0",
                "expected_versions": {"integration_attempt": 1},
                "command": {},
            },
            key=f"claim-ai-{suffix}",
        )
        assert claimed.status_code == 200, claimed.text
        return AiRun(
            request_id=request_id,
            operation_id=uuid.UUID(result["logical_operation_id"]),
            attempt_id=attempt_id,
            callback_credential=result["callback_credential"],
        )

    def succeed_ai(
        self,
        run: AiRun,
        suffix: str,
        *,
        category: str,
        confidence: str,
        missing: list[str] | None = None,
    ):
        path = f"/api/v1/integration-attempts/{run.attempt_id}/callbacks/succeeded"
        payload = {
            "schema_version": "1.0",
            "expected_versions": {"integration_attempt": 2},
            "evidence": {
                "result_schema_version": self.settings.ai_interpretation_result_schema_version,
                "adapter_version": self.settings.ai_adapter_version,
                "safe_provider_correlation": f"synthetic-ai-{suffix}",
                "latency_ms": 25,
                "token_usage": {"input_tokens": 12, "output_tokens": 8},
                "interpretation": {
                    "summary": f"Bounded advisory result for {suffix}.",
                    "suggested_category": category,
                    "missing_information": missing or [],
                    "confidence": confidence,
                    "warning_codes": [],
                },
            },
        }
        return self.machine_request(
            "POST",
            path,
            payload=payload,
            key=f"ai-success-{suffix}",
            callback_credential=run.callback_credential,
        )

    def triage(
        self,
        request_id: uuid.UUID,
        suffix: str,
        facts: AuthoritativeDecisionFacts,
        *,
        expected_version: int | None = None,
    ):
        command = CompleteTriageCommand(
            expected_service_request_version=(
                expected_version
                if expected_version is not None
                else self.row("service_requests", request_id)["version"]
            ),
            facts=facts,
            expected_policy=ExpectedDecisionPolicy(
                policy_key=DEMO_DECISION_POLICY.policy_key,
                semantic_version=DEMO_DECISION_POLICY.semantic_version,
                revision=DEMO_DECISION_POLICY.revision,
            ),
        )
        return CompleteTriageService(create_session_factory(self.engine)).execute(
            request_id=request_id,
            command=command,
            durable_command_key=f"complete-triage-{suffix}",
            correlation_id=uuid.uuid4(),
        )

    def prepare_request(
        self,
        suffix: str,
        *,
        description: str,
        category: str,
        confidence: str,
        facts: AuthoritativeDecisionFacts,
        email: str | None = None,
        missing: list[str] | None = None,
    ) -> tuple[uuid.UUID, object]:
        accepted = self.intake(suffix, description=description, email=email)
        assert accepted.status_code == 201, accepted.text
        request_id = uuid.UUID(accepted.json()["result"]["service_request_id"])
        run = self.start_ai(request_id, suffix)
        succeeded = self.succeed_ai(
            run,
            suffix,
            category=category,
            confidence=confidence,
            missing=missing,
        )
        assert succeeded.status_code == 200, succeeded.text
        return request_id, self.triage(request_id, suffix, facts)

    def proposal(
        self, request_id: uuid.UUID, suffix: str, creator: str = "scenario-ops"
    ) -> ProposalRun:
        path = f"/api/v1/service-requests/{request_id}/proposed-actions"
        created = self.client.post(
            path,
            headers=self.human_headers(creator, f"proposal-create-{suffix}"),
            json={
                "schema_version": "1.0",
                "expected_versions": {
                    "service_request": self.row("service_requests", request_id)["version"]
                },
                "proposal": {
                    "action_type": "CustomerMessage",
                    "destination": {"kind": "Email", "value": f"{suffix}@example.com"},
                    "content": f"Synthetic approved content for {suffix}.",
                    "scheduling": None,
                },
            },
        )
        assert created.status_code == 201, created.text
        action_id = uuid.UUID(created.json()["result"]["proposed_action_id"])
        submitted = self.client.post(
            f"/api/v1/proposed-actions/{action_id}/commands/submit-for-approval",
            headers=self.human_headers(creator, f"proposal-submit-{suffix}"),
            json={
                "schema_version": "1.0",
                "expected_versions": {
                    "service_request": self.row("service_requests", request_id)["version"],
                    "proposed_action": self.row("proposed_actions", action_id)["version"],
                },
            },
        )
        assert submitted.status_code == 200, submitted.text
        return ProposalRun(action_id, submitted.json()["result"]["payload_digest"])

    def approve(self, proposal: ProposalRun, suffix: str, actor: str = "scenario-manager"):
        action = self.row("proposed_actions", proposal.action_id)
        return self.client.post(
            f"/api/v1/proposed-actions/{proposal.action_id}/commands/approve",
            headers=self.human_headers(actor, f"proposal-approve-{suffix}"),
            json={
                "schema_version": "1.0",
                "expected_versions": {
                    "service_request": self.row("service_requests", action["service_request_id"])[
                        "version"
                    ],
                    "proposed_action": action["version"],
                },
                "expected_payload_digest": proposal.payload_digest,
            },
        )

    def start_outbound(self, action_id: uuid.UUID, suffix: str) -> AiRun:
        action = self.row("proposed_actions", action_id)
        path = f"/api/v1/proposed-actions/{action_id}/commands/start-outbound"
        started = self.machine_request(
            "POST",
            path,
            payload={
                "schema_version": "1.0",
                "expected_versions": {
                    "service_request": self.row("service_requests", action["service_request_id"])[
                        "version"
                    ],
                    "proposed_action": action["version"],
                },
                "command": {},
            },
            key=f"start-outbound-{suffix}",
        )
        assert started.status_code == 202, started.text
        result = started.json()["result"]
        attempt_id = uuid.UUID(result["integration_attempt_id"])
        claimed = self.machine_request(
            "POST",
            f"/api/v1/integration-attempts/{attempt_id}/commands/start",
            payload={
                "schema_version": "1.0",
                "expected_versions": {"integration_attempt": 1},
                "command": {},
            },
            key=f"claim-outbound-{suffix}",
        )
        assert claimed.status_code == 200, claimed.text
        return AiRun(
            request_id=action["service_request_id"],
            operation_id=uuid.UUID(result["logical_operation_id"]),
            attempt_id=attempt_id,
            callback_credential=result["callback_credential"],
        )

    def outbound_callback(
        self,
        run: AiRun,
        suffix: str,
        *,
        outcome: str,
        expected_version: int | None = None,
    ):
        path = f"/api/v1/integration-attempts/{run.attempt_id}/callbacks/"
        if outcome == "success":
            path += "succeeded"
            evidence = {
                "result_schema_version": "mock-outbound-result-v1",
                "adapter_version": "1.0",
                "simulated_outcome": "Applied",
                "safe_provider_correlation": f"mock-success-{suffix}",
                "safe_evidence_reference": f"mock-evidence-{suffix}",
                "safe_evidence_hash": "c" * 64,
            }
        else:
            path += "retryable-failure"
            known = outcome == "known-failure"
            evidence = {
                "failure_code": (
                    "WORKFLOW_FAILED_BEFORE_PROVIDER_INVOCATION" if known else "PROVIDER_TIMEOUT"
                ),
                "adapter_version": "1.0",
                "failure_stage": "BeforeDispatch" if known else "ProviderProcessing",
                "provider_invocation": "NotInvoked" if known else "Invoked",
                "customer_side_effect": "KnownNotApplied" if known else "Unknown",
                "safe_evidence_reference": f"mock-failure-{suffix}",
                "safe_evidence_hash": "d" * 64,
            }
        return self.machine_request(
            "POST",
            path,
            payload={
                "schema_version": "1.0",
                "expected_versions": {
                    "integration_attempt": expected_version
                    if expected_version is not None
                    else self.row("integration_attempts", run.attempt_id)["version"]
                },
                "evidence": evidence,
            },
            key=f"outbound-callback-{suffix}",
            callback_credential=run.callback_credential,
        )

    def row(self, table_name: str, row_id: uuid.UUID):
        table = Base.metadata.tables[table_name]
        with self.engine.connect() as connection:
            return connection.execute(select(table).where(table.c.id == row_id)).mappings().one()

    def rows(self, table_name: str):
        with self.engine.connect() as connection:
            return list(
                connection.execute(select(Base.metadata.tables[table_name])).mappings().all()
            )

    def count(self, table_name: str) -> int:
        with self.engine.connect() as connection:
            return int(
                connection.scalar(
                    select(func.count()).select_from(Base.metadata.tables[table_name])
                )
                or 0
            )

    def query_graph(self, request_id: uuid.UUID, subject: str = "scenario-ops") -> str:
        headers = self.human_headers(subject)
        paths = (
            f"/api/v1/service-requests/{request_id}",
            f"/api/v1/service-requests/{request_id}/timeline",
            f"/api/v1/service-requests/{request_id}/ai-interpretations",
            f"/api/v1/service-requests/{request_id}/duplicate-candidates",
            f"/api/v1/service-requests/{request_id}/routing-decisions",
            f"/api/v1/service-requests/{request_id}/proposed-actions",
        )
        responses = [self.client.get(path, headers=headers) for path in paths]
        assert all(response.status_code == 200 for response in responses)
        serialized = "".join(response.text for response in responses).lower()
        assert all(term not in serialized for term in FORBIDDEN_QUERY_TERMS)
        return serialized


@pytest.fixture(scope="module")
def engine() -> Engine:
    alembic_command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")
    value = create_database_engine(Settings(_env_file=None).database_url)
    yield value
    value.dispose()


@pytest.fixture
def scenario(engine: Engine) -> ScenarioContext:
    names = [name for name in Base.metadata.tables if name not in POLICY_TABLES]
    quoted = ", ".join(f'"{name}"' for name in names)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {quoted} CASCADE"))

    clock = datetime.now(UTC)
    actors = {
        "scenario-ops": "OperationsAgent",
        "scenario-manager": "ManagerApprover",
        "scenario-admin": "Administrator",
    }
    with engine.begin() as connection:
        actor_ids: dict[str, uuid.UUID] = {}
        for subject, role in actors.items():
            actor_id = uuid.uuid4()
            actor_ids[subject] = actor_id
            connection.execute(
                insert(Base.metadata.tables["application_actors"]).values(
                    id=actor_id,
                    supabase_subject=subject,
                    display_label=f"Synthetic {role}",
                    status="Active",
                    version=1,
                )
            )
            connection.execute(
                insert(Base.metadata.tables["application_actor_role_assignments"]).values(
                    id=uuid.uuid4(),
                    actor_id=actor_id,
                    role=role,
                    assigned_by_actor_id=actor_id,
                    effective_from=clock - timedelta(minutes=1),
                    assignment_reason="Phase 2 scenario acceptance",
                )
            )
        identity_id = uuid.uuid4()
        connection.execute(
            insert(Base.metadata.tables["machine_identities"]).values(
                id=identity_id,
                service_type="WorkflowService",
                environment="test",
                stable_service_id=SERVICE_ID,
                display_label="Synthetic Phase 2 workflow",
                status="Active",
                version=1,
            )
        )
        connection.execute(
            insert(Base.metadata.tables["machine_credential_versions"]).values(
                id=uuid.uuid4(),
                machine_identity_id=identity_id,
                credential_version=1,
                external_secret_reference=SECRET_REFERENCE,
                status="Current",
                activated_at=clock - timedelta(days=1),
            )
        )

    settings = Settings(
        app_environment="test",
        protected_query_cursor_signing_key=CURSOR_KEY,
        _env_file=None,
    )
    app = create_app(
        settings,
        create_session_factory(engine),
        jwt_verifier=TokenVerifier(),
        machine_secret_resolver=Resolver(),
        machine_clock=lambda: clock,
        callback_credential_generator=CredentialGenerator(),
    )
    return ScenarioContext(TestClient(app), engine, settings, clock)


def repair_facts(**changes: object) -> AuthoritativeDecisionFacts:
    values: dict[str, object] = {
        "explicit_category": "Repair",
        "requested_deadline": datetime.now(UTC) + timedelta(days=10),
        "service_mode": "OnSite",
        "access_constraints_known": True,
        "repair_symptoms_present": True,
        "repair_asset_context_present": True,
    }
    values.update(changes)
    return AuthoritativeDecisionFacts(**values)


def routine_facts() -> AuthoritativeDecisionFacts:
    return AuthoritativeDecisionFacts(
        explicit_category="RoutineMaintenance",
        timing_is_flexible=True,
        requested_deadline=datetime.now(UTC) + timedelta(days=28),
        service_mode="OnSite",
        access_constraints_known=True,
        maintenance_asset_context_present=True,
    )


def inspection_facts() -> AuthoritativeDecisionFacts:
    return AuthoritativeDecisionFacts(
        explicit_category="Inspection",
        requested_deadline=datetime.now(UTC) + timedelta(days=10),
        service_mode="OnSite",
        access_constraints_known=True,
        inspection_subject_present=True,
        inspection_purpose_present=True,
    )


def assert_atomic_evidence(scenario: ScenarioContext) -> None:
    assert scenario.count("audit_events") > 0
    assert scenario.count("outbox_messages") > 0
    assert all(row["status"] == "Completed" for row in scenario.rows("command_idempotency_records"))


def test_scenario_01_valid_standard_request(scenario: ScenarioContext) -> None:
    facts = routine_facts()
    request_id, first = scenario.prepare_request(
        "s01-standard",
        description="Schedule routine ventilation maintenance.",
        category="RoutineMaintenance",
        confidence="0.9200",
        facts=facts,
    )
    before = {
        name: scenario.count(name)
        for name in ("routing_decisions", "audit_events", "outbox_messages")
    }
    replay = scenario.triage(request_id, "s01-standard", facts, expected_version=3)
    assert first.is_replay is False
    assert replay.is_replay and replay.safe_snapshot == first.safe_snapshot
    assert {name: scenario.count(name) for name in before} == before
    request = scenario.row("service_requests", request_id)
    assert (request["priority"], request["status"], request["current_queue"]) == (
        "Low",
        "ReadyForAction",
        "StandardRequests",
    )
    graph = scenario.query_graph(request_id)
    assert "routinemaintenance" in graph and "readyforaction" in graph
    assert_atomic_evidence(scenario)


def test_scenario_02_high_priority_request(scenario: ScenarioContext) -> None:
    facts = repair_facts(
        requested_deadline=datetime.now(UTC) + timedelta(hours=48),
        service_interruption="Active",
    )
    request_id, outcome = scenario.prepare_request(
        "s02-high",
        description="Repair an actively interrupted ventilation system.",
        category="Repair",
        confidence="0.9100",
        facts=facts,
    )
    result = outcome.safe_snapshot["result"]
    assert (result["priority"], result["queue"]) == ("High", "PriorityRequests")
    decision = scenario.row("routing_decisions", uuid.UUID(result["routing_decision_id"]))
    assert decision["policy_digest"] == DEMO_DECISION_POLICY.content_digest
    assert "PRIORITY_ACTIVE_INTERRUPTION" in decision["priority_reason_codes"]
    assert decision["canonical_input_snapshot"]["ai_advisory"].get("priority") is None
    replay = scenario.triage(request_id, "s02-high", facts, expected_version=3)
    assert replay.is_replay
    scenario.query_graph(request_id)
    assert_atomic_evidence(scenario)


def test_scenario_03_urgent_request_requires_exact_approval(scenario: ScenarioContext) -> None:
    request_id, triage = scenario.prepare_request(
        "s03-urgent",
        description="Urgent repair required within twenty-four hours.",
        category="Repair",
        confidence="0.9000",
        facts=repair_facts(
            requested_deadline=datetime.now(UTC) + timedelta(hours=23),
            service_interruption="Active",
        ),
    )
    assert triage.safe_snapshot["result"]["priority"] == "Urgent"
    path = f"/api/v1/service-requests/{request_id}/commands/complete-human-review"
    review = {
        "schema_version": "1.0",
        "expected_versions": {
            "service_request": scenario.row("service_requests", request_id)["version"]
        },
        "reviewed_facts": {"urgent_review_disposition": "ConfirmedAndActionable"},
        "addressed_review_reason_codes": ["REVIEW_URGENT_PRIORITY"],
        "rationale": "Synthetic urgent review rationale.",
        "supporting_evidence_references": ["case-note:s03"],
    }
    denied = scenario.client.post(
        path,
        headers=scenario.human_headers("scenario-ops", "s03-ops-review"),
        json=review,
    )
    assert denied.status_code == 403
    accepted = scenario.client.post(
        path,
        headers=scenario.human_headers("scenario-manager", "s03-manager-review"),
        json=review,
    )
    assert accepted.status_code == 200, accepted.text
    proposal = scenario.proposal(request_id, "s03", creator="scenario-manager")
    assert scenario.count("integration_attempts") == 1  # The completed AI attempt only.
    self_denied = scenario.approve(proposal, "s03-self", actor="scenario-manager")
    assert self_denied.status_code == 403
    assert self_denied.json()["error"]["code"] == "SELF_APPROVAL_FORBIDDEN"
    approved = scenario.approve(proposal, "s03-admin", actor="scenario-admin")
    assert approved.status_code == 200, approved.text
    approval = scenario.rows("approval_decisions")[0]
    assert approval["proposed_action_id"] == proposal.action_id
    assert approval["payload_digest"] == proposal.payload_digest
    assert scenario.count("integration_attempts") == 1
    scenario.query_graph(request_id, "scenario-manager")
    assert_atomic_evidence(scenario)


def test_scenario_04_invalid_submission_is_safely_rejected(scenario: ScenarioContext) -> None:
    first = scenario.intake("s04-invalid", description="tiny", key="s04-invalid-key")
    second = scenario.intake("s04-invalid", description="tiny", key="s04-invalid-key")
    assert first.status_code == second.status_code == 422
    assert first.json()["error"]["code"] == "INTAKE_VALIDATION_FAILED"
    assert scenario.count("service_requests") == 0
    assert scenario.count("accepted_intake_keys") == 0
    assert scenario.count("inbound_deliveries") == 2
    assert all(
        not row["event_type"].startswith("service_request.")
        for row in scenario.rows("outbox_messages")
    )
    assert all(
        row["event_name"] != "service_request.created" for row in scenario.rows("audit_events")
    )
    assert "tiny" not in (first.text + second.text)
    delivery_id = first.json()["error"]["delivery_id"]
    inspected = scenario.client.get(
        f"/api/v1/inbound-deliveries/{delivery_id}",
        headers=scenario.human_headers("scenario-ops"),
    )
    assert inspected.status_code == 200
    assert "tiny" not in inspected.text
    assert all(term not in inspected.text.lower() for term in FORBIDDEN_QUERY_TERMS)


def test_scenario_05_missing_information_review_recalculation(scenario: ScenarioContext) -> None:
    facts = AuthoritativeDecisionFacts(
        explicit_category="Installation",
        requested_deadline=datetime.now(UTC) + timedelta(days=10),
        service_mode="OnSite",
        access_constraints_known=True,
        installation_scope_present=True,
    )
    request_id, outcome = scenario.prepare_request(
        "s05-missing",
        description="Install a replacement unit; final target is pending.",
        category="Installation",
        confidence="0.8300",
        missing=["INSTALLATION_TARGET"],
        facts=facts,
    )
    assert (
        "REVIEW_MISSING_REQUIRED_INFORMATION"
        in outcome.safe_snapshot["result"]["review_reason_codes"]
    )
    path = f"/api/v1/service-requests/{request_id}/commands/complete-human-review"
    incomplete = scenario.client.post(
        path,
        headers=scenario.human_headers("scenario-ops", "s05-incomplete"),
        json={
            "schema_version": "1.0",
            "expected_versions": {
                "service_request": scenario.row("service_requests", request_id)["version"]
            },
            "reviewed_facts": {"resolved_missing_information_codes": ["MISSING_SERVICE_LOCATION"]},
            "addressed_review_reason_codes": ["REVIEW_MISSING_REQUIRED_INFORMATION"],
            "rationale": "Synthetic incomplete review.",
            "supporting_evidence_references": ["case-note:s05-incomplete"],
        },
    )
    assert incomplete.status_code == 200
    assert incomplete.json()["result"]["service_request_status"] == "HumanReview"
    completed = scenario.client.post(
        path,
        headers=scenario.human_headers("scenario-ops", "s05-complete"),
        json={
            "schema_version": "1.0",
            "expected_versions": {
                "service_request": scenario.row("service_requests", request_id)["version"]
            },
            "reviewed_facts": {
                "resolved_missing_information_codes": ["MISSING_INSTALLATION_TARGET"]
            },
            "addressed_review_reason_codes": ["REVIEW_MISSING_REQUIRED_INFORMATION"],
            "rationale": "Synthetic completed review.",
            "supporting_evidence_references": ["case-note:s05-complete"],
        },
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["result"]["service_request_status"] == "ReadyForAction"
    assert len(scenario.rows("reviewed_fact_sets")) == 2
    scenario.query_graph(request_id)
    assert_atomic_evidence(scenario)


def test_scenario_06_low_confidence_boundary_is_deterministic(scenario: ScenarioContext) -> None:
    low_id, low = scenario.prepare_request(
        "s06-low",
        description="Inspect a synthetic unit with uncertain advisory evidence.",
        category="Inspection",
        confidence="0.7400",
        facts=inspection_facts(),
    )
    assert low.safe_snapshot["result"]["status"] == "HumanReview"
    assert "REVIEW_LOW_AI_CONFIDENCE" in low.safe_snapshot["result"]["review_reason_codes"]
    boundary_id, boundary = scenario.prepare_request(
        "s06-boundary",
        description="Inspect a synthetic unit at the exact confidence threshold.",
        category="Inspection",
        confidence="0.7500",
        facts=inspection_facts(),
    )
    assert DEMO_DECISION_POLICY.content.thresholds.ai_confidence_review == Decimal("0.75")
    assert boundary.safe_snapshot["result"]["status"] == "ReadyForAction"
    assert "REVIEW_LOW_AI_CONFIDENCE" not in boundary.safe_snapshot["result"]["review_reason_codes"]
    scenario.query_graph(low_id)
    scenario.query_graph(boundary_id)
    assert_atomic_evidence(scenario)


def test_scenario_07_possible_duplicate_is_resolved_without_merge(
    scenario: ScenarioContext,
) -> None:
    shared_email = "duplicate-s07@example.com"
    original_id, original = scenario.prepare_request(
        "s07-original",
        description="Repair the original synthetic ventilation request.",
        category="Repair",
        confidence="0.9000",
        facts=repair_facts(),
        email=shared_email,
    )
    assert original.safe_snapshot["result"]["status"] == "ReadyForAction"
    duplicate_id, duplicate = scenario.prepare_request(
        "s07-duplicate",
        description="A distinct request from the same verified synthetic contact.",
        category="Repair",
        confidence="0.9000",
        facts=repair_facts(),
        email=shared_email,
    )
    assert duplicate.safe_snapshot["result"]["status"] == "DuplicateReview"
    candidates = [
        row
        for row in scenario.rows("duplicate_candidates")
        if row["service_request_id"] == duplicate_id
    ]
    assert candidates
    candidate = candidates[0]
    path = (
        f"/api/v1/service-requests/{duplicate_id}/duplicate-candidates/"
        f"{candidate['id']}/commands/resolve"
    )
    body = {
        "schema_version": "1.0",
        "expected_versions": {
            "service_request": scenario.row("service_requests", duplicate_id)["version"]
        },
        "command": {"decision": "ConfirmedDuplicate", "rationale": "Synthetic duplicate proof."},
    }
    first = scenario.client.post(
        path,
        headers=scenario.human_headers("scenario-ops", "s07-resolve"),
        json=body,
    )
    replay = scenario.client.post(
        path,
        headers=scenario.human_headers("scenario-ops", "s07-resolve"),
        json=body,
    )
    assert first.status_code == replay.status_code == 200
    assert first.json()["command_id"] == replay.json()["command_id"]
    assert scenario.row("service_requests", duplicate_id)["status"] == "ClosedDuplicate"
    assert scenario.row("service_requests", original_id)["status"] == "ReadyForAction"
    assert scenario.count("service_requests") == 2
    scenario.query_graph(duplicate_id)
    assert_atomic_evidence(scenario)


def test_scenario_08_repeated_webhook_delivery_is_one_logical_result(
    scenario: ScenarioContext,
) -> None:
    key = "s08-repeated-webhook"
    first = scenario.intake(
        "s08-replay",
        description="A repeatable synthetic webhook request.",
        key=key,
    )
    second = scenario.intake(
        "s08-replay",
        description="A repeatable synthetic webhook request.",
        key=key,
    )
    conflict = scenario.intake(
        "s08-replay",
        description="A materially different synthetic webhook request.",
        key=key,
    )
    assert (first.status_code, second.status_code, conflict.status_code) == (201, 200, 409)
    assert second.json()["result"]["intake_outcome"] == "IdempotentReplay"
    assert (
        second.json()["result"]["service_request_id"]
        == first.json()["result"]["service_request_id"]
    )
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert scenario.count("service_requests") == 1
    assert scenario.count("accepted_intake_keys") == 1
    assert scenario.count("logical_operations") == 0
    assert scenario.count("inbound_deliveries") == 3
    assert (
        sum(row["event_name"] == "service_request.created" for row in scenario.rows("audit_events"))
        == 1
    )


def test_scenario_09_ai_failure_followed_by_exact_retry(scenario: ScenarioContext) -> None:
    accepted = scenario.intake(
        "s09-ai-retry",
        description="A synthetic request whose AI adapter times out once.",
    )
    request_id = uuid.UUID(accepted.json()["result"]["service_request_id"])
    run = scenario.start_ai(request_id, "s09")
    path = f"/api/v1/integration-attempts/{run.attempt_id}/callbacks/retryable-failure"
    payload = {
        "schema_version": "1.0",
        "expected_versions": {"integration_attempt": 2},
        "evidence": {
            "failure_code": "PROVIDER_TIMEOUT",
            "adapter_version": scenario.settings.ai_adapter_version,
            "safe_provider_correlation": "synthetic-ai-timeout-s09",
            "safe_reason_codes": ["UPSTREAM_TIMEOUT"],
            "duration_ms": 30000,
        },
    }
    failed = scenario.machine_request(
        "POST",
        path,
        payload=payload,
        key="s09-failure",
        callback_credential=run.callback_credential,
    )
    replay = scenario.machine_request(
        "POST",
        path,
        payload=payload,
        key="s09-failure",
        callback_credential=run.callback_credential,
    )
    assert failed.status_code == replay.status_code == 200
    assert replay.json()["command_id"] == failed.json()["command_id"]
    prior = scenario.row("integration_attempts", run.attempt_id)
    assert prior["state"] == "RetryableFailure"
    assert prior["maximum_attempts"] == 3 and prior["remaining_attempts"] == 2
    # Advance only the policy-owned temporal boundary; lifecycle state remains service-controlled.
    with scenario.engine.begin() as connection:
        connection.execute(
            update(Base.metadata.tables["integration_attempts"])
            .where(Base.metadata.tables["integration_attempts"].c.id == run.attempt_id)
            .values(next_eligible_at=func.now())
        )
    retry_path = f"/api/v1/service-requests/{request_id}/commands/retry-ai"
    retry_payload = {
        "schema_version": "1.0",
        "expected_versions": {
            "service_request": scenario.row("service_requests", request_id)["version"]
        },
        "command": {
            "failed_attempt_id": str(run.attempt_id),
            "expected_failure_policy": {
                "policy_id": str(prior["failure_policy_id"]),
                "semantic_version": prior["failure_policy_semantic_version"],
                "revision": prior["failure_policy_revision"],
                "content_digest": prior["failure_policy_digest"],
            },
        },
    }
    retried = scenario.machine_request("POST", retry_path, payload=retry_payload, key="s09-retry")
    retry_replay = scenario.machine_request(
        "POST", retry_path, payload=retry_payload, key="s09-retry"
    )
    assert (retried.status_code, retry_replay.status_code) == (202, 200)
    assert "callback_credential" in retried.json()["result"]
    assert "callback_credential" not in retry_replay.json()["result"]
    replacement_id = uuid.UUID(retried.json()["result"]["integration_attempt_id"])
    replacement = scenario.row("integration_attempts", replacement_id)
    assert replacement["attempt_number"] == 2 and replacement["state"] == "Pending"
    assert replacement["logical_operation_id"] == run.operation_id
    scenario.query_graph(request_id)
    assert_atomic_evidence(scenario)


def _ready_outbound_request(
    scenario: ScenarioContext, suffix: str
) -> tuple[uuid.UUID, ProposalRun]:
    request_id, outcome = scenario.prepare_request(
        suffix,
        description=f"Synthetic outbound-ready request {suffix}.",
        category="Repair",
        confidence="0.9000",
        facts=repair_facts(),
    )
    assert outcome.safe_snapshot["result"]["status"] == "ReadyForAction"
    proposal = scenario.proposal(request_id, suffix)
    approved = scenario.approve(proposal, suffix)
    assert approved.status_code == 200, approved.text
    return request_id, proposal


def test_scenario_10_mock_outbound_failure_retry_and_uncertainty(
    scenario: ScenarioContext,
) -> None:
    request_id, proposal = _ready_outbound_request(scenario, "s10-known")
    first = scenario.start_outbound(proposal.action_id, "s10-known")
    failed = scenario.outbound_callback(first, "s10-known", outcome="known-failure")
    assert failed.status_code == 200, failed.text
    prior = scenario.row("integration_attempts", first.attempt_id)
    assert prior["recovery_disposition"] == "RetrySameOperation"
    assert prior["customer_side_effect"] == "KnownNotApplied"
    assert prior["maximum_attempts"] == 3
    with scenario.engine.begin() as connection:
        connection.execute(
            update(Base.metadata.tables["integration_attempts"])
            .where(Base.metadata.tables["integration_attempts"].c.id == first.attempt_id)
            .values(next_eligible_at=func.now())
        )
    action = scenario.row("proposed_actions", proposal.action_id)
    retry_path = f"/api/v1/proposed-actions/{proposal.action_id}/commands/retry-outbound"
    retry_payload = {
        "schema_version": "1.0",
        "expected_versions": {
            "service_request": scenario.row("service_requests", request_id)["version"],
            "proposed_action": action["version"],
        },
        "command": {
            "failed_attempt_id": str(first.attempt_id),
            "expected_failure_policy": {
                "policy_id": str(prior["failure_policy_id"]),
                "semantic_version": prior["failure_policy_semantic_version"],
                "revision": prior["failure_policy_revision"],
                "content_digest": prior["failure_policy_digest"],
            },
        },
    }
    retried = scenario.machine_request("POST", retry_path, payload=retry_payload, key="s10-retry")
    assert retried.status_code == 202, retried.text
    second_id = uuid.UUID(retried.json()["result"]["integration_attempt_id"])
    second_credential = retried.json()["result"]["callback_credential"]
    claimed = scenario.machine_request(
        "POST",
        f"/api/v1/integration-attempts/{second_id}/commands/start",
        payload={
            "schema_version": "1.0",
            "expected_versions": {"integration_attempt": 1},
            "command": {},
        },
        key="s10-second-claim",
    )
    assert claimed.status_code == 200
    second_run = AiRun(request_id, first.operation_id, second_id, second_credential)
    succeeded = scenario.outbound_callback(second_run, "s10-second", outcome="success")
    assert succeeded.status_code == 200, succeeded.text
    attempts = [
        row
        for row in scenario.rows("integration_attempts")
        if row["logical_operation_id"] == first.operation_id
    ]
    assert [row["attempt_number"] for row in attempts] == [1, 2]
    assert scenario.row("service_requests", request_id)["status"] == "Completed"

    uncertain_request, uncertain_proposal = _ready_outbound_request(scenario, "s10-unknown")
    uncertain_run = scenario.start_outbound(uncertain_proposal.action_id, "s10-unknown")
    uncertain = scenario.outbound_callback(uncertain_run, "s10-unknown", outcome="unknown")
    assert uncertain.status_code == 200
    assert uncertain.json()["result"]["attempt_state"] == "Running"
    assert uncertain.json()["result"]["recovery_disposition"] == "ReconcileBeforeRetry"
    assert scenario.row("service_requests", uncertain_request)["status"] == "ActionPendingExecution"
    scenario.query_graph(request_id)
    assert_atomic_evidence(scenario)


def test_scenario_11_approved_outbound_action_completes_safely(
    scenario: ScenarioContext,
) -> None:
    request_id, proposal = _ready_outbound_request(scenario, "s11-approved")
    run = scenario.start_outbound(proposal.action_id, "s11-approved")
    succeeded = scenario.outbound_callback(
        run, "s11-approved", outcome="success", expected_version=2
    )
    replay = scenario.outbound_callback(run, "s11-approved", outcome="success", expected_version=2)
    assert succeeded.status_code == replay.status_code == 200
    assert succeeded.json()["command_id"] == replay.json()["command_id"]
    request = scenario.row("service_requests", request_id)
    action = scenario.row("proposed_actions", proposal.action_id)
    operation = scenario.row("logical_operations", run.operation_id)
    attempt = scenario.row("integration_attempts", run.attempt_id)
    assert request["status"] == "Completed"
    assert action["state"] == "Executed"
    assert operation["succeeded_attempt_id"] == run.attempt_id
    assert attempt["state"] == "Succeeded"
    machine_proposal = scenario.machine_request(
        "GET", f"/api/v1/proposed-actions/{proposal.action_id}"
    )
    machine_attempt = scenario.machine_request(
        "GET", f"/api/v1/integration-attempts/{run.attempt_id}"
    )
    assert machine_proposal.status_code == machine_attempt.status_code == 200
    combined = (
        scenario.query_graph(request_id)
        + machine_proposal.text.lower()
        + machine_attempt.text.lower()
    )
    assert all(term not in combined for term in FORBIDDEN_QUERY_TERMS)
    assert "smtp" not in combined and "n8n" not in combined
    assert_atomic_evidence(scenario)


def test_scenario_12_rejected_action_requires_fresh_revision_approval(
    scenario: ScenarioContext,
) -> None:
    request_id, outcome = scenario.prepare_request(
        "s12-rejected",
        description="Synthetic request whose first outbound proposal is rejected.",
        category="Repair",
        confidence="0.9000",
        facts=repair_facts(),
    )
    assert outcome.safe_snapshot["result"]["status"] == "ReadyForAction"
    proposal = scenario.proposal(request_id, "s12")
    action = scenario.row("proposed_actions", proposal.action_id)
    body = {
        "schema_version": "1.0",
        "expected_versions": {
            "service_request": scenario.row("service_requests", request_id)["version"],
            "proposed_action": action["version"],
        },
        "expected_payload_digest": proposal.payload_digest,
        "rationale": "Synthetic bounded rejection rationale.",
    }
    path = f"/api/v1/proposed-actions/{proposal.action_id}/commands/reject"
    rejected = scenario.client.post(
        path,
        headers=scenario.human_headers("scenario-manager", "s12-reject"),
        json=body,
    )
    replay = scenario.client.post(
        path,
        headers=scenario.human_headers("scenario-manager", "s12-reject"),
        json=body,
    )
    assert rejected.status_code == replay.status_code == 200
    assert rejected.json()["command_id"] == replay.json()["command_id"]
    assert scenario.count("approval_decisions") == 1
    assert scenario.count("integration_attempts") == 1  # AI only.
    start_denied = scenario.machine_request(
        "POST",
        f"/api/v1/proposed-actions/{proposal.action_id}/commands/start-outbound",
        payload={
            "schema_version": "1.0",
            "expected_versions": {
                "service_request": scenario.row("service_requests", request_id)["version"],
                "proposed_action": scenario.row("proposed_actions", proposal.action_id)["version"],
            },
            "command": {},
        },
        key="s12-start-rejected",
    )
    assert start_denied.status_code == 409
    revised = scenario.client.post(
        f"/api/v1/proposed-actions/{proposal.action_id}/commands/create-material-revision",
        headers=scenario.human_headers("scenario-ops", "s12-revision"),
        json={
            "schema_version": "1.0",
            "expected_versions": {
                "service_request": scenario.row("service_requests", request_id)["version"],
                "proposed_action": scenario.row("proposed_actions", proposal.action_id)["version"],
            },
            "proposal": {
                "action_type": "CustomerMessage",
                "destination": {"kind": "Email", "value": "s12-revision@example.com"},
                "content": "Synthetic revised content requiring fresh approval.",
                "scheduling": None,
            },
        },
    )
    assert revised.status_code == 201, revised.text
    replacement_id = uuid.UUID(revised.json()["result"]["replacement_proposed_action_id"])
    replacement = scenario.row("proposed_actions", replacement_id)
    assert replacement["state"] == "Draft" and replacement["current_approval_id"] is None
    assert scenario.count("approval_decisions") == 1
    scenario.query_graph(request_id, "scenario-manager")
    assert_atomic_evidence(scenario)
