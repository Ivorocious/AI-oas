from fastapi.testclient import TestClient

from ai_operations_automation.app import create_app
from ai_operations_automation.config import Settings


def test_health_returns_typed_safe_response() -> None:
    settings = Settings(
        app_name="AI Operations Automation API",
        app_environment="staging",
        api_v1_prefix="/internal-versioned-prefix",
        _env_file=None,
    )
    client = TestClient(create_app(settings))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "AI Operations Automation API",
    }
    response_text = response.text
    assert "staging" not in response_text
    assert "internal-versioned-prefix" not in response_text


def test_unknown_route_returns_fastapi_default_404() -> None:
    client = TestClient(create_app(Settings(_env_file=None)))

    response = client.get("/not-a-route")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
