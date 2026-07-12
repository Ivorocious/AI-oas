from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_operations_automation.app import create_app
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


def test_missing_and_non_bearer_authentication_are_401() -> None:
    client = TestClient(create_app(Settings(_env_file=None)))
    path = "/api/v1/service-requests/00000000-0000-0000-0000-000000000001"

    missing = client.get(path)
    non_bearer = client.get(path, headers={"Authorization": "Basic abc"})

    assert missing.status_code == non_bearer.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"


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
