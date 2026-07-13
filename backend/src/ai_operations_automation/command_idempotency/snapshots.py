"""Bounded safe completed-command response snapshots."""

import json
from typing import Any

MAX_SAFE_SNAPSHOT_BYTES = 16 * 1024
FORBIDDEN_KEYS = {
    "plaintext",
    "credential_plaintext",
    "callback_credential_plaintext",
    "credential_hash",
    "callback_credential_hash",
    "secret",
    "secret_value",
    "hmac_secret",
    "access_token",
    "refresh_token",
    "api_key",
    "authorization",
    "signature",
    "raw_nonce",
    "private_key",
    "password",
}


def validate_safe_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise ValueError("safe response snapshot must be a JSON object")
    encoded = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > MAX_SAFE_SNAPSHOT_BYTES:
        raise ValueError("safe response snapshot is too large")
    normalized = json.loads(encoded)
    if not isinstance(normalized, dict):
        raise ValueError("safe response snapshot must remain a JSON object")

    def inspect(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key.casefold() in FORBIDDEN_KEYS:
                    raise ValueError("safe response snapshot contains a forbidden key")
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)

    inspect(normalized)
    return normalized
