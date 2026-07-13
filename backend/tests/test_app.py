from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_operations_automation.app import create_app
from ai_operations_automation.auth.verifier import KeyDiscoveryFailure
from ai_operations_automation.config import Settings


def test_create_app_returns_fastapi_application() -> None:
    application = create_app(Settings(_env_file=None))

    assert isinstance(application, FastAPI)


def test_explicit_settings_are_reflected_by_application() -> None:
    settings = Settings(app_name="Test Operations API", app_environment="test", _env_file=None)

    application = create_app(settings)

    assert application.title == "Test Operations API"
    assert application.state.settings is settings


def test_app_construction_and_health_do_not_fetch_jwks(monkeypatch) -> None:
    def loader_factory(_url):
        def unexpected_fetch():
            raise AssertionError("JWKS must be lazy")

        return unexpected_fetch

    monkeypatch.setattr("ai_operations_automation.app.url_jwks_loader", loader_factory)
    client = TestClient(create_app(Settings(_env_file=None)))

    assert client.get("/health").status_code == 200


def test_unrelated_routes_do_not_generate_callback_credentials() -> None:
    calls = 0

    def generator():
        nonlocal calls
        calls += 1
        return "A" * 43

    application = create_app(Settings(_env_file=None), callback_credential_generator=generator)
    client = TestClient(application)
    assert calls == 0
    assert client.get("/health").status_code == 200
    assert client.post("/api/v1/intake/service-requests").status_code == 400
    assert (
        client.get("/api/v1/service-requests/00000000-0000-0000-0000-000000000001").status_code
        == 401
    )
    assert calls == 0


def test_missing_and_non_bearer_authentication_are_401() -> None:
    client = TestClient(create_app(Settings(_env_file=None)))
    path = "/api/v1/service-requests/00000000-0000-0000-0000-000000000001"

    missing = client.get(path)
    non_bearer = client.get(path, headers={"Authorization": "Basic abc"})

    assert missing.status_code == non_bearer.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"


def test_malformed_empty_and_duplicate_authorization_are_401() -> None:
    client = TestClient(create_app(Settings(_env_file=None)))
    path = "/api/v1/service-requests/00000000-0000-0000-0000-000000000001"

    malformed = client.get(path, headers={"Authorization": "Bearer"})
    empty = client.get(path, headers={"Authorization": "Bearer "})
    duplicate = client.get(
        path,
        headers=[("Authorization", "Bearer first"), ("Authorization", "Bearer second")],
    )

    assert malformed.status_code == empty.status_code == duplicate.status_code == 401


def test_invalid_correlation_precedes_missing_authentication() -> None:
    client = TestClient(create_app(Settings(_env_file=None)))
    response = client.get(
        "/api/v1/service-requests/00000000-0000-0000-0000-000000000001",
        headers={"X-Correlation-ID": "invalid"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_TRANSPORT_IDENTIFIER"


def test_jwks_discovery_failure_maps_to_safe_503() -> None:
    class FailingVerifier:
        def verify(self, _token):
            raise KeyDiscoveryFailure

    correlation = "00000000-0000-0000-0000-000000000123"
    client = TestClient(create_app(Settings(_env_file=None), jwt_verifier=FailingVerifier()))
    response = client.get(
        "/api/v1/service-requests/00000000-0000-0000-0000-000000000001",
        headers={"Authorization": "Bearer opaque", "X-Correlation-ID": correlation},
    )

    assert response.status_code == 503
    assert response.json()["error"]["correlation_id"] == correlation
    assert response.headers["x-correlation-id"] == correlation


def test_openapi_documents_protected_query_and_resolves_local_references() -> None:
    schema = create_app(Settings(_env_file=None)).openapi()
    operation = schema["paths"]["/api/v1/service-requests/{request_id}"]["get"]

    assert operation["security"]
    assert {"200", "400", "401", "403", "404", "500", "503"} <= set(operation["responses"])

    def walk(value):
        if isinstance(value, dict):
            reference = value.get("$ref")
            if reference and reference.startswith("#/"):
                target = schema
                for part in reference[2:].split("/"):
                    target = target[part]
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(schema)

    command = schema["paths"][
        "/api/v1/service-requests/{request_id}/commands/start-ai-interpretation"
    ]["post"]
    assert command["requestBody"]["content"]["application/json"]["schema"]
    expected_responses = {
        "200",
        "202",
        "400",
        "401",
        "404",
        "409",
        "415",
        "422",
        "500",
        "503",
    }
    assert expected_responses <= set(command["responses"])


def test_route_inventory_and_interpretation_reference_is_nullable_uuid() -> None:
    schema = create_app(Settings(_env_file=None)).openapi()
    assert set(schema["paths"]) == {
        "/health",
        "/api/v1/intake/service-requests",
        "/api/v1/service-requests/{request_id}",
        "/api/v1/service-requests/{request_id}/commands/start-ai-interpretation",
    }
    field = schema["components"]["schemas"]["ActiveReferences"]["properties"][
        "current_interpretation_id"
    ]
    assert {item.get("type") for item in field["anyOf"]} == {"string", "null"}
    assert any(item.get("format") == "uuid" for item in field["anyOf"])
