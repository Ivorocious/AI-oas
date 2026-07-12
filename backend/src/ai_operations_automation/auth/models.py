"""Trusted authentication context."""

import uuid
from dataclasses import dataclass
from typing import Literal

HumanRole = Literal["OperationsAgent", "ManagerApprover", "Administrator"]


@dataclass(frozen=True, slots=True)
class AuthenticatedHuman:
    actor_id: uuid.UUID
    supabase_subject: str
    role: HumanRole
