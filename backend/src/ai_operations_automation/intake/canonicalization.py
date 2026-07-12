"""Deterministic canonical JSON and SHA-256 hashing."""

import hashlib
import json

from ai_operations_automation.intake.models import IntakeRequest


def canonical_json(payload: IntakeRequest) -> bytes:
    normalized = payload.model_dump(mode="json", exclude_none=True)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_payload_hash(payload: IntakeRequest) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def raw_body_fingerprint(raw_body: bytes) -> str:
    return hashlib.sha256(raw_body).hexdigest()


def idempotency_key_digest(key: str) -> str:
    return hashlib.sha256(key.encode("ascii")).hexdigest()
