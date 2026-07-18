"""Opaque, versioned, filter-bound cursors for protected list projections."""

import base64
import binascii
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from ai_operations_automation.intake.errors import IntakeError

_CURSOR_VERSION = 1
_PROJECTION_SCHEMA_VERSION = "1.0"


def _key(value: bytes | None) -> bytes:
    if value is None or len(value) < 32:
        raise IntakeError(
            503,
            "DEPENDENCY_UNAVAILABLE",
            "A required dependency is unavailable.",
            True,
        )
    return value


def encode_cursor(
    key: bytes | None,
    kind: str,
    filters: dict[str, str | None],
    stamp: datetime,
    row_id: str,
    *,
    ordering: str,
    principal_scope: str,
) -> str:
    """Create a signed, opaque cursor for a single fixed query ordering."""
    payload = {
        "v": _CURSOR_VERSION,
        "sv": _PROJECTION_SCHEMA_VERSION,
        "k": kind,
        "f": filters,
        "o": ordering,
        "p": principal_scope,
        "t": stamp.astimezone(UTC).isoformat(),
        "i": row_id,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(_key(key), raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + signature).decode().rstrip("=")


def decode_cursor(
    key: bytes | None,
    value: str | None,
    kind: str,
    filters: dict[str, str | None],
    *,
    ordering: str,
    principal_scope: str,
) -> tuple[datetime, str] | None:
    """Validate a cursor before it is applied; never expose parsing detail."""
    if value is None:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        encoded = base64.urlsafe_b64decode(padded.encode())
        if base64.urlsafe_b64encode(encoded).decode().rstrip("=") != value:
            raise ValueError
        raw, signature = encoded[:-32], encoded[-32:]
        if not hmac.compare_digest(hmac.new(_key(key), raw, hashlib.sha256).digest(), signature):
            raise ValueError
        payload: dict[str, Any] = json.loads(raw)
        if (
            not isinstance(payload, dict)
            or payload.get("v") != _CURSOR_VERSION
            or payload.get("sv") != _PROJECTION_SCHEMA_VERSION
            or payload.get("k") != kind
            or payload.get("f") != filters
            or payload.get("o") != ordering
            or payload.get("p") != principal_scope
            or not isinstance(payload.get("i"), str)
            or set(payload) != {"v", "sv", "k", "f", "o", "p", "t", "i"}
        ):
            raise ValueError
        stamp = datetime.fromisoformat(payload["t"])
        if stamp.tzinfo is None:
            raise ValueError
        uuid.UUID(payload["i"])
        return stamp.astimezone(UTC), payload["i"]
    except (
        binascii.Error,
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        raise IntakeError(400, "INVALID_CURSOR", "The cursor is invalid.") from None
