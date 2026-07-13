"""Proposal service transport boundary."""

import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ProposalOutcome:
    logical_http_status: int
    command_id: uuid.UUID
    safe_snapshot: dict[str, Any]
