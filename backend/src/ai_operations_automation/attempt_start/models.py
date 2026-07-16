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
    operation_kind: Literal["AIInterpretation", "OutboundAction"]
    attempt_state: Literal["Running"]
    started_at: AwareDatetime
    adapter_name: str
    adapter_version: str
    proposed_action_id: uuid.UUID | None = None
    proposal_series_id: uuid.UUID | None = None
    proposal_number: int | None = Field(default=None, gt=0)
    proposal_payload_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    approval_decision_id: uuid.UUID | None = None
    stable_outbound_key_scope: str | None = None
    stable_outbound_key_reference: str | None = None


class AttemptStartVersions(ClosedModel):
    integration_attempt: int = Field(gt=0)


class AttemptStartResponse(ClosedModel):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    command_id: uuid.UUID
    result: AttemptStartResult
    versions: AttemptStartVersions
