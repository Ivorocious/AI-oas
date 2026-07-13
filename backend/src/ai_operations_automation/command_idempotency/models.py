"""Immutable trusted command scope and idempotency results."""

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session, SessionTransaction

ActorClass = Literal["HumanActor", "MachineService", "BackendService"]
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,99}$")
ROUTE_TEMPLATE = re.compile(r"^/[^\s\x00-\x1f\x7f?#]*$")


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CommandIdempotencyScope(FrozenModel):
    """Scope built only by an already-authenticated backend boundary."""

    actor_class: ActorClass
    actor_id: uuid.UUID
    command_intent: str = Field(max_length=100)
    route_template: str = Field(max_length=300)
    target_type: str = Field(max_length=100)
    target_id: uuid.UUID

    @field_validator("command_intent", "target_type")
    @classmethod
    def safe_identifier(cls, value: str) -> str:
        if SAFE_IDENTIFIER.fullmatch(value) is None:
            raise ValueError("must be a bounded safe identifier")
        return value

    @field_validator("route_template")
    @classmethod
    def normalized_route_template(cls, value: str) -> str:
        if ROUTE_TEMPLATE.fullmatch(value) is None:
            raise ValueError("must be a normalized backend route template")
        return value


@dataclass(frozen=True, slots=True)
class NewCommandReservation:
    record_id: uuid.UUID
    command_id: uuid.UUID
    correlation_id: uuid.UUID
    _session: Session = field(repr=False, compare=False)
    _outer_transaction: SessionTransaction = field(repr=False, compare=False)


class SecretDeliveryMetadata(FrozenModel):
    callback_credential_id: uuid.UUID
    callback_credential_version: int = Field(gt=0)
    callback_credential_expires_at: AwareDatetime


class CallbackAuthorizationMetadata(FrozenModel):
    callback_credential_id: uuid.UUID
    callback_credential_version: int = Field(gt=0)


class CompletedCommandReplay(FrozenModel):
    record_id: uuid.UUID
    command_id: uuid.UUID
    original_correlation_id: uuid.UUID
    logical_http_status: int
    safe_response_snapshot: dict[str, Any]
    completed_at: datetime
    callback_credential_id: uuid.UUID | None = None
    callback_credential_version: int | None = None
    callback_credential_expires_at: datetime | None = None
    callback_authorization_credential_id: uuid.UUID | None = None
    callback_authorization_credential_version: int | None = None
    credential_delivery: Literal["AlreadyIssued"] | None = None
