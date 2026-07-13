"""Closed Start AI interpretation command contracts."""

import uuid
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartAiExpectedVersions(ClosedModel):
    service_request: StrictInt = Field(gt=0)


class EmptyStartAiCommand(ClosedModel):
    pass


class StartAiInterpretationRequest(ClosedModel):
    schema_version: Literal["1.0"]
    expected_versions: StartAiExpectedVersions
    command: EmptyStartAiCommand


class StartAiInterpretationResult(ClosedModel):
    service_request_id: uuid.UUID
    logical_operation_id: uuid.UUID
    integration_attempt_id: uuid.UUID
    attempt_number: Literal[1]
    attempt_state: Literal["Pending"]
    callback_credential_id: uuid.UUID
    callback_credential_version: Literal[1]
    callback_credential_expires_at: AwareDatetime
    credential_delivery: Literal["PlaintextIssued", "AlreadyIssued"]
    callback_credential: str | None = None


class StartAiInterpretationVersions(ClosedModel):
    service_request: int = Field(gt=0)
    logical_operation: Literal[1]
    integration_attempt: Literal[1]


class StartAiInterpretationResponse(ClosedModel):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    command_id: uuid.UUID
    result: StartAiInterpretationResult
    versions: StartAiInterpretationVersions
