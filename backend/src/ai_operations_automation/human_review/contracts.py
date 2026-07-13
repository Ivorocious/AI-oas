"""Injected domain-service boundary for complete-human-review transport."""

import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from ai_operations_automation.auth.models import AuthenticatedHuman
from ai_operations_automation.human_review.models import CompleteHumanReviewRequest


@dataclass(frozen=True, slots=True)
class HumanReviewOutcome:
    """Safe committed outcome returned by the future domain service."""

    logical_http_status: int
    command_id: uuid.UUID
    safe_snapshot: dict[str, Any]


class HumanReviewService(Protocol):
    """Interface required by the complete-human-review route."""

    def execute(
        self,
        *,
        request_id: uuid.UUID,
        command: CompleteHumanReviewRequest,
        raw_idempotency_key: str,
        canonical_body_hash: str,
        correlation_id: uuid.UUID,
        actor: AuthenticatedHuman,
    ) -> HumanReviewOutcome: ...
