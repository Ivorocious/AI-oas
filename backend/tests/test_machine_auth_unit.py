from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ai_operations_automation.app import create_app
from ai_operations_automation.config import Settings
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.machine_auth.authenticator import (
    calculate_signature,
    signatures_match,
    validate_nonce,
    validate_signature,
    validate_timestamp,
)
from ai_operations_automation.machine_auth.canonicalization import (
    canonical_path,
    canonical_path_and_query,
    canonical_query,
    canonical_signing_bytes,
)


def test_fixed_empty_and_json_body_signing_vectors() -> None:
    nonce = "nonce-0123456789abcdef"
    empty = canonical_signing_bytes("post", b"/api/%2fwork", b"b=2&a=1", "1700000000", nonce, b"")
    json_body = canonical_signing_bytes(
        "POST", b"/api/%2Fwork", b"a=1&b=2", "1700000000", nonce, b'{"ok":true}'
    )
    assert empty.decode() == (
        "POST\n/api/%2Fwork?a=1&b=2\n1700000000\nnonce-0123456789abcdef\n"
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    assert json_body.decode().endswith(
        "4062edaf750fb8074e7e83e0c9028c94e32468a8b6f1614774328ef045150f93"
    )
    assert calculate_signature(b"synthetic-test-key", empty) == (
        "fb0327352fcf4f786a9df0523e161cad0ea80a6649cd99db04e49ab9aef0f97c"
    )


def test_path_and_query_canonicalization_vectors() -> None:
    assert canonical_path(b"/a%2fb/%7e") == "/a%2Fb/%7E"
    assert canonical_path(b"/a%2Fb") != canonical_path(b"/a/b")
    assert canonical_query(b"z=&a=2&a=1&space=hello+world") == ("a=1&a=2&space=hello%20world&z=")
    assert canonical_path_and_query(b"/path", b"b=&a=x") == "/path?a=x&b="


@pytest.mark.parametrize("value", [b"/bad%2", b"/bad%GG"])
def test_malformed_percent_encoding_is_rejected(value) -> None:
    with pytest.raises(IntakeError):
        canonical_path(value)


def test_malformed_utf8_query_is_rejected() -> None:
    with pytest.raises(IntakeError):
        canonical_query(b"value=%FF")


def test_timestamp_inclusive_boundaries() -> None:
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    lower = int((now - timedelta(seconds=300)).timestamp())
    upper = int((now + timedelta(seconds=300)).timestamp())
    assert validate_timestamp(str(lower), now, 300).timestamp() == lower
    assert validate_timestamp(str(upper), now, 300).timestamp() == upper
    with pytest.raises(IntakeError):
        validate_timestamp(str(lower - 1), now, 300)
    with pytest.raises(IntakeError):
        validate_timestamp(str(upper + 1), now, 300)


@pytest.mark.parametrize(
    "value",
    ["short", "x" * 129, "contains whitespace", "control\x01character"],
)
def test_invalid_nonce_shapes(value) -> None:
    with pytest.raises(IntakeError):
        validate_nonce(value)


def test_valid_nonce_and_signature_shapes() -> None:
    assert validate_nonce("nonce-0123456789abcdef")
    assert validate_signature("a" * 64)


@pytest.mark.parametrize("value", ["A" * 64, "a" * 63, "a" * 65, "g" * 64])
def test_invalid_signature_shapes(value) -> None:
    with pytest.raises(IntakeError):
        validate_signature(value)


def test_signature_comparison_uses_compare_digest(monkeypatch) -> None:
    calls = []

    def compare(left, right):
        calls.append((left, right))
        return True

    monkeypatch.setattr(
        "ai_operations_automation.machine_auth.authenticator.hmac.compare_digest", compare
    )
    assert signatures_match("expected", "supplied") is True
    assert calls == [("expected", "supplied")]


def test_app_health_public_and_human_edges_do_not_resolve_machine_secret() -> None:
    class Resolver:
        def resolve(self, _reference):
            raise AssertionError("unrelated routes must not resolve machine secrets")

    client = TestClient(create_app(Settings(_env_file=None), machine_secret_resolver=Resolver()))
    assert client.get("/health").status_code == 200
    assert client.post("/api/v1/intake/service-requests").status_code == 400
    assert (
        client.get("/api/v1/service-requests/00000000-0000-0000-0000-000000000001").status_code
        == 401
    )
