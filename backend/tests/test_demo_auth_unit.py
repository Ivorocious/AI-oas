import jwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from ai_operations_automation.app import create_app
from ai_operations_automation.config import Settings
from ai_operations_automation.db import create_session_factory


def test_demo_auth_is_disabled_by_default_and_hidden() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    app = create_app(Settings(_env_file=None), create_session_factory(engine))
    assert (
        TestClient(app, client=("127.0.0.1", 50100))
        .post("/demo-auth/token", json={"persona": "manager"})
        .status_code
        == 404
    )


def test_demo_auth_requires_loopback_and_issues_no_role_claim() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    settings = Settings(_env_file=None, demo_auth_enabled=True)
    app = create_app(settings, create_session_factory(engine))
    blocked = TestClient(app, client=("203.0.113.10", 50100)).post(
        "/demo-auth/token", json={"persona": "manager"}
    )
    assert blocked.status_code == 404
    allowed = TestClient(app, client=("127.0.0.1", 50100)).post(
        "/demo-auth/token", json={"persona": "manager"}
    )
    assert allowed.status_code == 200
    token = allowed.json()["access_token"]
    key = (
        TestClient(app, client=("127.0.0.1", 50100))
        .get("/demo-auth/.well-known/jwks.json")
        .json()["keys"][0]
    )
    claims = jwt.decode(
        token,
        key=jwt.PyJWK.from_dict(key).key,
        algorithms=["RS256"],
        audience="ai-operations-demo-browser",
        issuer="http://127.0.0.1:8000/demo-auth",
    )
    assert claims["sub"] == "demo-manager"
    assert "role" not in claims
