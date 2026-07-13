"""Closed claim/start attempt command contracts."""

import uuid
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AttemptStartExpectedVersions(ClosedModel):
    integration_attempt: StrictInt = Field(gt=0)


class EmptyAttemptStartCommand(ClosedModel):
    pass


class AttemptStartRequest(ClosedModel):
    schema_version: Literal["1.0"]
    expected_versions: AttemptStartExpectedVersions
    command: EmptyAttemptStartCommand


class AttemptStartResult(ClosedModel):
    service_request_id: uuid.UUID
    logical_operation_id: uuid.UUID
    integration_attempt_id: uuid.UUID
    attempt_number: int = Field(gt=0)
    operation_kind: Literal["AIInterpretation"]
    attempt_state: Literal["Running"]
    started_at: AwareDatetime
    adapter_name: str
    adapter_version: str


class AttemptStartVersions(ClosedModel):
    integration_attempt: int = Field(gt=0)


class AttemptStartResponse(ClosedModel):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    command_id: uuid.UUID
    result: AttemptStartResult
    versions: AttemptStartVersions
