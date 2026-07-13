import asyncio
import json
import uuid
from typing import cast

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ai_operations_automation.api.duplicate_resolution import (
    get_duplicate_resolution_service,
)
from ai_operations_automation.api.duplicate_resolution import (
    router as duplicate_resolution_router,
)
from ai_operations_automation.api.human_review import (
    get_human_review_service,
)
from ai_operations_automation.api.human_review import (
    router as human_review_router,
)
from ai_operations_automation.auth.dependencies import require_service_request_reader
from ai_operations_automation.auth.models import AuthenticatedHuman, HumanRole
from ai_operations_automation.auth.permissions import require_service_request_permission
from ai_operations_automation.command_idempotency.canonicalization import canonical_command_hash
from ai_operations_automation.duplicate_resolution.contracts import DuplicateResolutionOutcome
from ai_operations_automation.duplicate_resolution.models import (
    ResolveDuplicateRequest,
    ResolveDuplicateResponse,
)
from ai_operations_automation.duplicate_resolution.parsing import (
    parse_resolve_duplicate_command,
)
from ai_operations_automation.human_review.contracts import HumanReviewOutcome
from ai_operations_automation.human_review.models import (
    CompleteHumanReviewRequest,
    CompleteHumanReviewResponse,
)
from ai_operations_automation.human_review.parsing import (
    MAX_COMMAND_BODY_BYTES,
    parse_complete_human_review_command,
)
from ai_operations_automation.intake.errors import IntakeError

REQUEST_ID = uuid.UUID("00000000-0000-0000-0000-000000000101")
CANDIDATE_ID = uuid.UUID("00000000-0000-0000-0000-000000000102")
ACTOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000103")
POLICY_ID = uuid.UUID("00000000-0000-0000-0000-000000000104")
FACT_SET_ID = uuid.UUID("00000000-0000-0000-0000-000000000105")
DECISION_ID = uuid.UUID("00000000-0000-0000-0000-000000000106")
COMMAND_ID = uuid.UUID("00000000-0000-0000-0000-000000000107")


def duplicate_payload() -> dict:
    return {
        "schema_version": "1.0",
        "expected_versions": {"service_request": 4},
        "command": {
            "decision": "NotDuplicate",
            "rationale": "The inspected evidence refers to a different service need.",
        },
    }


def human_review_payload() -> dict:
    return {
        "schema_version": "1.0",
        "expected_versions": {"service_request": 7},
        "expected_policy": {
            "policy_key": "general-service-demo",
            "semantic_version": "1.0.0",
            "revision": 1,
        },
        "reviewed_facts": {
            "resolved_missing_information_codes": ["MISSING_SERVICE_LOCATION"],
            "corrected_category": "Repair",
            "corrected_requested_deadline": "2026-07-14T12:00:00Z",
            "corrected_service_interruption": "None",
            "corrected_damage_or_deterioration": "Active",
            "corrected_safety_or_continuity_concern": "None",
        },
        "addressed_review_reason_codes": [
            "REVIEW_MISSING_REQUIRED_INFORMATION",
            "REVIEW_CATEGORY_CONFLICT",
        ],
        "rationale": "Verified the location and repair symptoms with the customer.",
        "supporting_evidence_references": ["contact-log:case-1042"],
    }


def raw_request(body: bytes, *, key: str = "human-command-key-001") -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [
                (b"content-type", b"application/json"),
                (b"idempotency-key", key.encode()),
            ],
        },
        receive,
    )


class RecordingDuplicateService:
    def __init__(self, *, error: bool = False) -> None:
        self.calls: list[dict] = []
        self.error = error

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            return DuplicateResolutionOutcome(
                logical_http_status=409,
                command_id=COMMAND_ID,
                safe_snapshot={
                    "error": {
                        "code": "CONCURRENCY_CONFLICT",
                        "message": "The resource version does not match.",
                        "retryable": False,
                        "current_versions": {"service_request": 5},
                        "details": [],
                    }
                },
            )
        return DuplicateResolutionOutcome(
            logical_http_status=200,
            command_id=COMMAND_ID,
            safe_snapshot={
                "result": {
                    "service_request_id": str(REQUEST_ID),
                    "duplicate_candidate_id": str(CANDIDATE_ID),
                    "candidate_resolution": "NotDuplicate",
                    "service_request_status": "TriagePending",
                    "service_request_queue": None,
                },
                "versions": {"service_request": 5},
            },
        )


class RecordingHumanReviewService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return HumanReviewOutcome(
            logical_http_status=200,
            command_id=COMMAND_ID,
            safe_snapshot={
                "result": {
                    "service_request_id": str(REQUEST_ID),
                    "reviewed_fact_set_id": str(FACT_SET_ID),
                    "routing_decision_id": str(DECISION_ID),
                    "routing_decision_version": 2,
                    "policy": {
                        "policy_id": str(POLICY_ID),
                        "policy_key": "general-service-demo",
                        "semantic_version": "1.0.0",
                        "revision": 1,
                        "content_digest": "a" * 64,
                    },
                    "category": "Repair",
                    "priority": "Normal",
                    "service_request_status": "ReadyForAction",
                    "service_request_queue": "StandardRequests",
                    "review_required": False,
                    "outstanding_review_reason_codes": [],
                },
                "versions": {"service_request": 8},
            },
        )


def test_duplicate_resolution_accepts_only_the_bounded_disposition() -> None:
    command = ResolveDuplicateRequest.model_validate(duplicate_payload())
    assert command.command.decision == "NotDuplicate"
    assert command.expected_versions.service_request == 4


def test_human_review_accepts_the_approved_bounded_fact_shape() -> None:
    command = CompleteHumanReviewRequest.model_validate(human_review_payload())
    assert command.expected_policy is not None
    assert command.expected_policy.policy_key == "general-service-demo"
    assert command.reviewed_facts.corrected_category == "Repair"
    assert command.reviewed_facts.corrected_requested_deadline.utcoffset() is not None


@pytest.mark.parametrize("version", [0, -1, True, 1.0, "1"])
@pytest.mark.parametrize(
    ("payload_factory", "model"),
    [
        (duplicate_payload, ResolveDuplicateRequest),
        (human_review_payload, CompleteHumanReviewRequest),
    ],
)
def test_human_commands_require_a_strict_positive_expected_request_version(
    payload_factory,
    model,
    version,
) -> None:
    payload = payload_factory()
    payload["expected_versions"]["service_request"] = version
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "field", "value"),
    [
        ((), "actor_id", str(ACTOR_ID)),
        ((), "role", "Administrator"),
        ((), "status", "ReadyForAction"),
        ((), "queue", "PriorityRequests"),
        (("command",), "candidate_id", str(CANDIDATE_ID)),
        (("command",), "service_request_status", "ClosedDuplicate"),
        (("command",), "metadata", {"arbitrary": "value"}),
    ],
)
def test_duplicate_resolution_rejects_identity_and_backend_owned_fields(
    path,
    field,
    value,
) -> None:
    payload = duplicate_payload()
    target = payload
    for part in path:
        target = target[part]
    target[field] = value
    with pytest.raises(ValidationError):
        ResolveDuplicateRequest.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "field", "value"),
    [
        ((), "actor_id", str(ACTOR_ID)),
        ((), "role", "ManagerApprover"),
        ((), "status", "ReadyForAction"),
        ((), "queue", "PriorityRequests"),
        ((), "priority", "Urgent"),
        ((), "routing_decision_id", str(DECISION_ID)),
        (("reviewed_facts",), "final_category", "Repair"),
        (("reviewed_facts",), "final_priority", "Low"),
        (("reviewed_facts",), "duplicate_resolution", "NotDuplicate"),
        (("reviewed_facts",), "approval_state", "Approved"),
        (("reviewed_facts",), "retry_eligible", True),
        (("reviewed_facts",), "notes", "note-only completion"),
    ],
)
def test_human_review_rejects_identity_and_backend_owned_outputs(path, field, value) -> None:
    payload = human_review_payload()
    target = payload
    for part in path:
        target = target[part]
    target[field] = value
    with pytest.raises(ValidationError):
        CompleteHumanReviewRequest.model_validate(payload)


def test_human_review_rejects_note_only_or_empty_reviewed_facts() -> None:
    payload = human_review_payload()
    payload["reviewed_facts"] = {}
    with pytest.raises(ValidationError):
        CompleteHumanReviewRequest.model_validate(payload)


@pytest.mark.parametrize("preference_present", [True, False])
def test_timing_preference_presence_is_an_allowlisted_material_reviewed_fact(
    preference_present,
) -> None:
    payload = human_review_payload()
    payload["reviewed_facts"] = {
        "corrected_timing_preference_present": preference_present,
    }
    command = CompleteHumanReviewRequest.model_validate(payload)
    assert command.reviewed_facts.corrected_timing_preference_present is preference_present


@pytest.mark.parametrize("invalid", [0, 1, "true", "false"])
def test_timing_preference_presence_is_a_strict_boolean(invalid) -> None:
    payload = human_review_payload()
    payload["reviewed_facts"] = {"corrected_timing_preference_present": invalid}
    with pytest.raises(ValidationError):
        CompleteHumanReviewRequest.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("corrected_material_impact", "Catastrophic"),
        ("corrected_service_interruption", "Resolved"),
        ("corrected_damage_or_deterioration", "Stable"),
        ("corrected_safety_or_continuity_concern", "Dismissed"),
        ("urgent_review_disposition", "BypassReview"),
    ],
)
def test_human_review_rejects_unapproved_fact_enum_values(field, value) -> None:
    payload = human_review_payload()
    payload["reviewed_facts"][field] = value
    with pytest.raises(ValidationError):
        CompleteHumanReviewRequest.model_validate(payload)


def test_human_review_parser_uses_specific_code_for_unknown_reviewed_fact() -> None:
    payload = human_review_payload()
    payload["reviewed_facts"]["final_priority"] = "Low"
    with pytest.raises(IntakeError) as caught:
        asyncio.run(parse_complete_human_review_command(raw_request(json.dumps(payload).encode())))
    assert (caught.value.status_code, caught.value.code) == (422, "REVIEW_FACT_NOT_ALLOWED")
    assert caught.value.details == [
        {"field": "reviewed_facts.final_priority", "issue_code": "UNKNOWN_FIELD"}
    ]


@pytest.mark.parametrize("body", [b"", b"{", b"\xff", b" " * (MAX_COMMAND_BODY_BYTES + 1)])
@pytest.mark.parametrize(
    "parser",
    [parse_resolve_duplicate_command, parse_complete_human_review_command],
)
def test_human_command_parsers_reject_invalid_bodies_safely(parser, body) -> None:
    with pytest.raises(IntakeError) as caught:
        asyncio.run(parser(raw_request(body)))
    assert (caught.value.status_code, caught.value.code) == (400, "INVALID_COMMAND")


def test_duplicate_response_rejects_inconsistent_backend_transition() -> None:
    with pytest.raises(ValidationError):
        ResolveDuplicateResponse.model_validate(
            {
                "correlation_id": str(uuid.uuid4()),
                "command_id": str(COMMAND_ID),
                "result": {
                    "service_request_id": str(REQUEST_ID),
                    "duplicate_candidate_id": str(CANDIDATE_ID),
                    "candidate_resolution": "ConfirmedDuplicate",
                    "service_request_status": "TriagePending",
                    "service_request_queue": None,
                },
                "versions": {"service_request": 5},
            }
        )


def test_human_review_response_rejects_inconsistent_incomplete_result() -> None:
    payload = RecordingHumanReviewService().execute().safe_snapshot
    payload["result"].update(
        {
            "service_request_status": "HumanReview",
            "service_request_queue": "HumanReview",
            "review_required": True,
            "outstanding_review_reason_codes": [],
        }
    )
    with pytest.raises(ValidationError):
        CompleteHumanReviewResponse.model_validate(
            {
                "correlation_id": str(uuid.uuid4()),
                "command_id": str(COMMAND_ID),
                **payload,
            }
        )


def test_current_human_role_permission_allows_only_the_three_application_roles() -> None:
    for role in ("OperationsAgent", "ManagerApprover", "Administrator"):
        actor = AuthenticatedHuman(ACTOR_ID, "subject", cast(HumanRole, role))
        assert require_service_request_permission(actor) is actor
    untrusted = AuthenticatedHuman(ACTOR_ID, "subject", cast(HumanRole, "EventPublisher"))
    with pytest.raises(IntakeError) as caught:
        require_service_request_permission(untrusted)
    assert (caught.value.status_code, caught.value.code) == (403, "FORBIDDEN")


def command_app(*, actor: AuthenticatedHuman | None, duplicate_service=None, review_service=None):
    app = FastAPI()

    @app.exception_handler(IntakeError)
    async def safe_error(request: Request, error: IntakeError):
        correlation = getattr(request.state, "correlation_id", uuid.uuid4())
        return JSONResponse(error.response(correlation), status_code=error.status_code)

    app.include_router(duplicate_resolution_router)
    app.include_router(human_review_router)
    if actor is not None:
        app.dependency_overrides[require_service_request_reader] = lambda: actor
    if duplicate_service is not None:
        app.dependency_overrides[get_duplicate_resolution_service] = lambda: duplicate_service
    if review_service is not None:
        app.dependency_overrides[get_human_review_service] = lambda: review_service
    return app


@pytest.mark.parametrize("role", ["OperationsAgent", "ManagerApprover", "Administrator"])
def test_duplicate_route_injects_current_actor_and_maps_command(role) -> None:
    actor = AuthenticatedHuman(ACTOR_ID, "current-subject", cast(HumanRole, role))
    service = RecordingDuplicateService()
    client = TestClient(command_app(actor=actor, duplicate_service=service))
    correlation = str(uuid.uuid4())
    response = client.post(
        (
            f"/api/v1/service-requests/{REQUEST_ID}/duplicate-candidates/"
            f"{CANDIDATE_ID}/commands/resolve"
        ),
        json=duplicate_payload(),
        headers={"Idempotency-Key": "duplicate-resolve-key-001", "X-Correlation-ID": correlation},
    )
    assert response.status_code == 200
    assert response.headers["x-correlation-id"] == correlation
    assert response.json()["command_id"] == str(COMMAND_ID)
    call = service.calls[0]
    assert call["request_id"] == REQUEST_ID and call["candidate_id"] == CANDIDATE_ID
    assert call["actor"] is actor
    assert type(call["command"]) is ResolveDuplicateRequest
    assert call["raw_idempotency_key"] == "duplicate-resolve-key-001"
    assert call["canonical_body_hash"] == canonical_command_hash(call["command"])
    assert str(call["correlation_id"]) == correlation


@pytest.mark.parametrize("role", ["OperationsAgent", "ManagerApprover", "Administrator"])
def test_human_review_route_injects_current_actor_and_maps_bounded_facts(role) -> None:
    actor = AuthenticatedHuman(ACTOR_ID, "current-subject", cast(HumanRole, role))
    service = RecordingHumanReviewService()
    client = TestClient(command_app(actor=actor, review_service=service))
    correlation = str(uuid.uuid4())
    response = client.post(
        f"/api/v1/service-requests/{REQUEST_ID}/commands/complete-human-review",
        json=human_review_payload(),
        headers={"Idempotency-Key": "human-review-key-0001", "X-Correlation-ID": correlation},
    )
    assert response.status_code == 200
    assert response.headers["x-correlation-id"] == correlation
    assert response.json()["result"]["routing_decision_id"] == str(DECISION_ID)
    call = service.calls[0]
    assert call["request_id"] == REQUEST_ID and call["actor"] is actor
    assert type(call["command"]) is CompleteHumanReviewRequest
    assert call["raw_idempotency_key"] == "human-review-key-0001"
    assert call["canonical_body_hash"] == canonical_command_hash(call["command"])
    assert str(call["correlation_id"]) == correlation


@pytest.mark.parametrize(
    "path",
    [
        (
            f"/api/v1/service-requests/{REQUEST_ID}/duplicate-candidates/"
            f"{CANDIDATE_ID}/commands/resolve"
        ),
        f"/api/v1/service-requests/{REQUEST_ID}/commands/complete-human-review",
    ],
)
def test_human_command_routes_require_bearer_authentication_before_services(path) -> None:
    duplicate = RecordingDuplicateService()
    review = RecordingHumanReviewService()
    client = TestClient(command_app(actor=None, duplicate_service=duplicate, review_service=review))
    payload = human_review_payload() if "complete-human-review" in path else duplicate_payload()
    response = client.post(
        path,
        json=payload,
        headers={"Idempotency-Key": "unauthenticated-command-key"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert duplicate.calls == [] and review.calls == []


def test_route_maps_service_guard_snapshot_to_safe_error() -> None:
    actor = AuthenticatedHuman(ACTOR_ID, "current-subject", "OperationsAgent")
    service = RecordingDuplicateService(error=True)
    client = TestClient(command_app(actor=actor, duplicate_service=service))
    response = client.post(
        (
            f"/api/v1/service-requests/{REQUEST_ID}/duplicate-candidates/"
            f"{CANDIDATE_ID}/commands/resolve"
        ),
        json=duplicate_payload(),
        headers={"Idempotency-Key": "duplicate-conflict-key-001"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONCURRENCY_CONFLICT"
    assert response.json()["error"]["current_versions"] == {"service_request": 5}


def test_openapi_documents_bearer_security_closed_bodies_and_backend_authority() -> None:
    schema = command_app(actor=None).openapi()
    paths = (
        "/api/v1/service-requests/{request_id}/duplicate-candidates/"
        "{candidate_id}/commands/resolve",
        "/api/v1/service-requests/{request_id}/commands/complete-human-review",
    )
    assert schema["components"]["securitySchemes"]["HTTPBearer"] == {
        "type": "http",
        "scheme": "bearer",
    }
    for path in paths:
        operation = schema["paths"][path]["post"]
        assert operation["security"] == [{"HTTPBearer": []}]
        assert "backend" in operation["description"].lower()
        parameters = {item["name"]: item for item in operation["parameters"]}
        assert parameters["Idempotency-Key"]["required"] is True
        assert parameters["X-Correlation-ID"]["schema"]["format"] == "uuid"
        request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
        assert "$ref" not in json.dumps(request_schema)
