"""Trusted immutable machine authentication context."""

import uuid
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class AuthenticatedWorkflowService:
    machine_identity_id: uuid.UUID
    stable_service_id: str
    environment: str
    service_type: Literal["WorkflowService"]
    credential_id: uuid.UUID
    credential_version: int
