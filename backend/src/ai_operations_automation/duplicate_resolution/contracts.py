"""Injected domain-service boundary for duplicate resolution transport."""

import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from ai_operations_automation.auth.models import AuthenticatedHuman
from ai_operations_automation.duplicate_resolution.models import ResolveDuplicateRequest


@dataclass(frozen=True, slots=True)
class DuplicateResolutionOutcome:
    """Safe committed outcome returned by the future domain service."""

    logical_http_status: int
    command_id: uuid.UUID
    safe_snapshot: dict[str, Any]


class DuplicateResolutionService(Protocol):
    """Interface required by the duplicate-resolution route."""

    def execute(
        self,
        *,
        request_id: uuid.UUID,
        candidate_id: uuid.UUID,
        command: ResolveDuplicateRequest,
        raw_idempotency_key: str,
        canonical_body_hash: str,
        correlation_id: uuid.UUID,
        actor: AuthenticatedHuman,
    ) -> DuplicateResolutionOutcome: ...
