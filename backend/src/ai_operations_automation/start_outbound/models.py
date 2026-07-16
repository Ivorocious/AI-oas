"""Closed Start Outbound contracts."""

import uuid
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, model_validator


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartOutboundExpectedVersions(ClosedModel):
    service_request: StrictInt = Field(gt=0)
    proposed_action: StrictInt = Field(gt=0)


class EmptyStartOutboundCommand(ClosedModel):
    pass


class StartOutboundRequest(ClosedModel):
    schema_version: Literal["1.0"]
    expected_versions: StartOutboundExpectedVersions
    command: EmptyStartOutboundCommand


class StartOutboundResult(ClosedModel):
    service_request_id: uuid.UUID
    proposed_action_id: uuid.UUID
    proposal_series_id: uuid.UUID
    proposal_number: StrictInt = Field(gt=0)
    proposal_payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_decision_id: uuid.UUID
    logical_operation_id: uuid.UUID
    integration_attempt_id: uuid.UUID
    attempt_number: StrictInt = Field(ge=1, le=3)
    attempt_state: Literal["Pending"]
    proposal_state: Literal["PendingExecution"]
    service_request_status: Literal["ActionPendingExecution"]
    adapter_name: Literal["MockOutboundAdapter"]
    adapter_version: Literal["1.0"]
    stable_outbound_key_scope: Literal["mock-outbound-operation-v1"]
    stable_outbound_key_reference: str = Field(min_length=1, max_length=200)
    callback_credential_id: uuid.UUID
    callback_credential_version: Literal[1]
    callback_credential_expires_at: AwareDatetime
    credential_delivery: Literal["PlaintextIssued", "AlreadyIssued"]
    callback_credential: str | None = Field(
        default=None, min_length=43, max_length=256, pattern=r"^[A-Za-z0-9_-]+$"
    )

    @model_validator(mode="after")
    def validate_secret_delivery(self) -> "StartOutboundResult":
        if (self.credential_delivery == "PlaintextIssued") != (
            self.callback_credential is not None
        ):
            raise ValueError("credential delivery fields are inconsistent")
        return self


class StartOutboundVersions(ClosedModel):
    service_request: StrictInt = Field(gt=0)
    proposed_action: StrictInt = Field(gt=0)
    logical_operation: StrictInt = Field(gt=0)
    integration_attempt: Literal[1]


class StartOutboundResponse(ClosedModel):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    command_id: uuid.UUID
    result: StartOutboundResult
    versions: StartOutboundVersions
