from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from ai_operations_automation.auth.verifier import (
    AuthenticationFailure,
    KeyDiscoveryFailure,
    SupabaseJwtVerifier,
)

ISSUER = "https://example.supabase.co/auth/v1"
AUDIENCE = "authenticated"


@pytest.fixture
def key_material():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    jwk["kid"] = "primary"
    return private_key, jwk


def token(private_key, **overrides):
    claims = {
        "sub": "supabase-user-1",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "nbf": datetime.now(UTC) - timedelta(seconds=1),
        "role": "Administrator",
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "primary"})


def verifier(jwk, loader=None):
    return SupabaseJwtVerifier(
        ISSUER,
        AUDIENCE,
        loader or (lambda: {"keys": [jwk]}),
        cache_seconds=300,
    )


def test_valid_token_returns_only_subject_and_ignores_role_claim(key_material) -> None:
    private_key, jwk = key_material
    assert verifier(jwk).verify(token(private_key)) == "supabase-user-1"


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": datetime.now(UTC) - timedelta(seconds=1)},
        {"nbf": datetime.now(UTC) + timedelta(minutes=1)},
        {"iss": "https://wrong.example/auth/v1"},
        {"aud": "wrong"},
        {"sub": ""},
    ],
)
def test_invalid_registered_claims_are_rejected(key_material, claims) -> None:
    private_key, jwk = key_material
    with pytest.raises(AuthenticationFailure):
        verifier(jwk).verify(token(private_key, **claims))


def test_non_rs256_token_is_rejected(key_material) -> None:
    _, jwk = key_material
    encoded = jwt.encode(
        {"sub": "user", "exp": datetime.now(UTC) + timedelta(minutes=1)},
        "local-test-secret-that-is-long-enough-for-hs256",
        algorithm="HS256",
        headers={"kid": "primary"},
    )
    with pytest.raises(AuthenticationFailure):
        verifier(jwk).verify(encoded)


def test_unknown_kid_forces_one_refresh(key_material) -> None:
    private_key, jwk = key_material
    calls = 0

    def loader():
        nonlocal calls
        calls += 1
        return {"keys": [] if calls == 1 else [jwk]}

    assert verifier(jwk, loader).verify(token(private_key)) == "supabase-user-1"
    assert calls == 2


def test_key_discovery_failure_is_distinct(key_material) -> None:
    private_key, jwk = key_material

    def loader():
        raise KeyDiscoveryFailure

    with pytest.raises(KeyDiscoveryFailure):
        verifier(jwk, loader).verify(token(private_key))


def test_unknown_kid_refreshes_once_then_authentication_fails(key_material) -> None:
    private_key, jwk = key_material
    calls = 0

    def loader():
        nonlocal calls
        calls += 1
        return {"keys": []}

    with pytest.raises(AuthenticationFailure):
        verifier(jwk, loader).verify(token(private_key))
    assert calls == 2


@pytest.mark.parametrize("document", [{}, {"keys": "invalid"}])
def test_malformed_jwks_is_discovery_failure(key_material, document) -> None:
    private_key, jwk = key_material
    with pytest.raises(KeyDiscoveryFailure):
        verifier(jwk, lambda: document).verify(token(private_key))


def test_invalid_jwk_is_discovery_failure(key_material) -> None:
    private_key, jwk = key_material
    invalid = {"kid": "primary", "kty": "RSA", "n": "invalid", "e": "invalid"}
    with pytest.raises(KeyDiscoveryFailure):
        verifier(jwk, lambda: {"keys": [invalid]}).verify(token(private_key))


def test_cached_known_key_avoids_repeated_discovery(key_material) -> None:
    private_key, jwk = key_material
    calls = 0

    def loader():
        nonlocal calls
        calls += 1
        return {"keys": [jwk]}

    subject_verifier = verifier(jwk, loader)
    assert subject_verifier.verify(token(private_key)) == "supabase-user-1"
    assert subject_verifier.verify(token(private_key)) == "supabase-user-1"
    assert calls == 1
