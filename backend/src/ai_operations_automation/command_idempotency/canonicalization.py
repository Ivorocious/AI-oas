"""Focused canonical binding for already validated closed command models."""

import hashlib
import json

from pydantic import BaseModel


def canonical_command_bytes(command: BaseModel) -> bytes:
    """Serialize the complete validated command, retaining explicit null fields."""
    dumped = command.model_dump(mode="json", exclude_none=False)
    return json.dumps(
        dumped,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_command_hash(command: BaseModel) -> str:
    return hashlib.sha256(canonical_command_bytes(command)).hexdigest()
