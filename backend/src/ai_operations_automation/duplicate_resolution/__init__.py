"""Closed duplicate-resolution HTTP contracts."""

from ai_operations_automation.duplicate_resolution.contracts import (
    DuplicateResolutionOutcome,
    DuplicateResolutionService,
)
from ai_operations_automation.duplicate_resolution.models import (
    ResolveDuplicateRequest,
    ResolveDuplicateResponse,
)

__all__ = [
    "DuplicateResolutionOutcome",
    "DuplicateResolutionService",
    "ResolveDuplicateRequest",
    "ResolveDuplicateResponse",
]
