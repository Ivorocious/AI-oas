from typing import Any

import pytest

from ai_operations_automation.app import create_app
from ai_operations_automation.config import Settings

BATCH_1_ROUTES = {
    "/api/v1/integration-attempts/{attempt_id}/callbacks/succeeded": "AiSuccessCallbackRequest",
    "/api/v1/integration-attempts/{attempt_id}/callbacks/retryable-failure": (
        "AiRetryableFailureCallbackRequest"
    ),
    "/api/v1/integration-attempts/{attempt_id}/callbacks/terminal-failure": (
        "AiTerminalFailureCallbackRequest"
    ),
    "/api/v1/integration-attempts/{attempt_id}/commands/replace-callback-credential": (
        "ReplaceCallbackCredentialRequest"
    ),
    "/api/v1/service-requests/{request_id}/commands/retry-ai": "RetryAiRequest",
    "/api/v1/service-requests/{request_id}/commands/mark-terminal-failure": (
        "MarkTerminalFailureRequest"
    ),
}

EXPECTED_ROUTES = {
    "/health",
    "/api/v1/intake/service-requests",
    "/api/v1/service-requests/{request_id}",
    "/api/v1/service-requests/{request_id}/commands/start-ai-interpretation",
    "/api/v1/integration-attempts/{attempt_id}/commands/start",
    ("/api/v1/service-requests/{request_id}/duplicate-candidates/{candidate_id}/commands/resolve"),
    "/api/v1/service-requests/{request_id}/commands/complete-human-review",
    "/api/v1/service-requests/{request_id}/proposed-actions",
    "/api/v1/proposed-actions/{action_id}/draft",
    "/api/v1/proposed-actions/{action_id}/commands/submit-for-approval",
    "/api/v1/proposed-actions/{action_id}/commands/approve",
    "/api/v1/proposed-actions/{action_id}/commands/reject",
    "/api/v1/proposed-actions/{action_id}/commands/create-material-revision",
    "/api/v1/proposed-actions/{action_id}/commands/start-outbound",
    "/api/v1/proposed-actions/{action_id}/commands/retry-outbound",
    *BATCH_1_ROUTES,
}


@pytest.fixture(scope="module")
def openapi_schema() -> dict[str, Any]:
    return create_app(Settings(_env_file=None)).openapi()


def walk(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def test_batch1_route_inventory_is_exact_and_contains_no_stale_http_route(openapi_schema) -> None:
    assert set(openapi_schema["paths"]) == EXPECTED_ROUTES
    assert set(BATCH_1_ROUTES) <= set(openapi_schema["paths"])
    assert all("stale" not in path for path in openapi_schema["paths"])


@pytest.mark.parametrize(("path", "model_title"), BATCH_1_ROUTES.items())
def test_each_batch1_route_has_a_resolved_closed_json_request_body(
    openapi_schema, path, model_title
) -> None:
    operation = openapi_schema["paths"][path]["post"]
    request_body = operation["requestBody"]
    assert request_body["required"] is True
    body_schema = request_body["content"]["application/json"]["schema"]
    candidates = body_schema.get("oneOf", [body_schema])
    matching = [schema for schema in candidates if schema.get("title") == model_title]
    assert len(matching) == 1
    selected = matching[0]
    assert selected["type"] == "object"
    assert selected["additionalProperties"] is False
    assert not any(
        isinstance(item, dict) and ("$ref" in item or "$defs" in item) for item in walk(selected)
    )
    objects = [
        item for item in walk(selected) if isinstance(item, dict) and item.get("type") == "object"
    ]
    assert objects
    assert all(item.get("additionalProperties") is False for item in objects)


def test_batch1_route_responses_are_documented(openapi_schema) -> None:
    for path in BATCH_1_ROUTES:
        operation = openapi_schema["paths"][path]["post"]
        assert {"400", "401", "404", "409", "415", "422", "500", "503"} <= set(
            operation["responses"]
        )
    assert (
        "202"
        in openapi_schema["paths"]["/api/v1/service-requests/{request_id}/commands/retry-ai"][
            "post"
        ]["responses"]
    )


def test_batch1_authentication_modes_are_explicit(openapi_schema) -> None:
    schemes = openapi_schema["components"]["securitySchemes"]
    assert {"HTTPBearer", "WorkflowServiceHmac", "AttemptCallbackCredential"} <= set(schemes)
    for path in (
        "/api/v1/integration-attempts/{attempt_id}/callbacks/succeeded",
        "/api/v1/integration-attempts/{attempt_id}/callbacks/retryable-failure",
        "/api/v1/integration-attempts/{attempt_id}/callbacks/terminal-failure",
    ):
        assert openapi_schema["paths"][path]["post"]["security"] == [
            {"WorkflowServiceHmac": [], "AttemptCallbackCredential": []}
        ]
    retry = openapi_schema["paths"]["/api/v1/service-requests/{request_id}/commands/retry-ai"][
        "post"
    ]["security"]
    assert retry == [{"HTTPBearer": []}, {"WorkflowServiceHmac": []}]
