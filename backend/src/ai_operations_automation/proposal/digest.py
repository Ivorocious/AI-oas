"""Deterministic execution-relevant proposal payload hashing."""

import hashlib
import json
import math
from datetime import datetime
from typing import Any


def proposal_payload_digest(payload: dict[str, Any]) -> str:
    """Hash only the normalized, closed execution payload."""
    closed = {
        "action_type": payload["action_type"],
        "destination": {
            "kind": payload["destination"]["kind"],
            "value": payload["destination"]["value"],
        },
        "content": payload["content"],
        "scheduling": payload.get("scheduling"),
    }

    def normalize(value: Any) -> Any:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("proposal payload contains a non-finite value")
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    closed = normalize(closed)
    encoded = json.dumps(
        closed, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
