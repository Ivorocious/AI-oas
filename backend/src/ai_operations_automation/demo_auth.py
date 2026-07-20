"""Fail-closed, loopback-only synthetic browser authentication for the local demo.

This module deliberately issues identities, never roles.  The normal bearer dependency
still performs the authoritative actor and current-role lookup in PostgreSQL.
"""

import ipaddress
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from ai_operations_automation.auth.verifier import AuthenticationFailure
from ai_operations_automation.intake.errors import IntakeError

DEMO_ISSUER = "http://127.0.0.1:8000/demo-auth"
DEMO_AUDIENCE = "ai-operations-demo-browser"
DEMO_SUBJECTS = {"demo-manager", "demo-operations"}


def _loopback(host: str | None) -> bool:
    try:
        return host is not None and ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def require_demo_request(request: Request) -> None:
    settings = request.app.state.settings
    client_host = request.client.host if request.client else None
    if (
        settings.app_environment != "local"
        or not settings.demo_auth_enabled
        or not _loopback(client_host)
    ):
        raise IntakeError(404, "RESOURCE_NOT_FOUND", "The requested resource was not found.")


class DemoIssuer:
    """One process-lifetime RSA keypair.  Private material is never serialized."""

    def __init__(self) -> None:
        from cryptography.hazmat.primitives.asymmetric import rsa

        self._private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._kid = uuid.uuid4().hex

    def jwks(self) -> dict[str, list[dict[str, Any]]]:
        item = jwt.algorithms.RSAAlgorithm.to_jwk(self._private_key.public_key(), as_dict=True)
        item.update({"kid": self._kid, "use": "sig", "alg": "RS256"})
        return {"keys": [item]}

    def issue(self, subject: str) -> str:
        if subject not in DEMO_SUBJECTS:
            raise AuthenticationFailure
        now = datetime.now(UTC)
        return jwt.encode(
            {
                "iss": DEMO_ISSUER,
                "aud": DEMO_AUDIENCE,
                "sub": subject,
                "iat": now,
                "exp": now + timedelta(minutes=10),
            },
            self._private_key,
            algorithm="RS256",
            headers={"kid": self._kid},
        )


class DemoTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    persona: Literal["manager", "operations"]


class DemoTokenResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: Literal[600] = 600


router = APIRouter(prefix="/demo-auth", tags=["local-demo-auth"])


@router.get("/.well-known/jwks.json")
def public_jwks(request: Request) -> dict[str, list[dict[str, Any]]]:
    require_demo_request(request)
    return request.app.state.demo_issuer.jwks()


@router.post("/token", response_model=DemoTokenResponse)
def demo_token(command: DemoTokenRequest, request: Request) -> DemoTokenResponse:
    require_demo_request(request)
    subject = "demo-manager" if command.persona == "manager" else "demo-operations"
    return DemoTokenResponse(access_token=request.app.state.demo_issuer.issue(subject))
