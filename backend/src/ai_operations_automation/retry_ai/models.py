"""Closed retry-AI command transport contracts."""

import uuid
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, model_validator


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetryAiExpectedVersions(ClosedModel):
    service_request: StrictInt = Field(gt=0)


class ExpectedFailurePolicyIdentity(ClosedModel):
    policy_id: uuid.UUID
    semantic_version: str = Field(
        min_length=5,
        max_length=32,
        pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$",
    )
    revision: StrictInt = Field(gt=0)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class RetryAiCommand(ClosedModel):
    failed_attempt_id: uuid.UUID
    expected_failure_policy: ExpectedFailurePolicyIdentity


class RetryAiRequest(ClosedModel):
    schema_version: Literal["1.0"]
    expected_versions: RetryAiExpectedVersions
    command: RetryAiCommand


class RetryAiResult(ClosedModel):
    service_request_id: uuid.UUID
    logical_operation_id: uuid.UUID
    failed_attempt_id: uuid.UUID
    integration_attempt_id: uuid.UUID
    attempt_number: StrictInt = Field(ge=2, le=3)
    attempt_state: Literal["Pending"]
    service_request_status: Literal["TriagePending"]
    failure_policy_id: uuid.UUID
    callback_credential_id: uuid.UUID
    callback_credential_version: Literal[1]
    callback_credential_expires_at: AwareDatetime
    credential_delivery: Literal["PlaintextIssued", "AlreadyIssued", "ReplacementRequired"]
    callback_credential: str | None = Field(
        default=None,
        min_length=43,
        max_length=256,
        pattern=r"^[A-Za-z0-9_-]+$",
    )

    @model_validator(mode="after")
    def one_time_plaintext_is_consistent(self) -> "RetryAiResult":
        if (self.credential_delivery == "PlaintextIssued") != (
            self.callback_credential is not None
        ):
            raise ValueError("credential delivery fields are inconsistent")
        return self


class RetryAiVersions(ClosedModel):
    service_request: StrictInt = Field(gt=0)
    logical_operation: StrictInt = Field(gt=0)
    integration_attempt: Literal[1]


class RetryAiResponse(ClosedModel):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    command_id: uuid.UUID
    result: RetryAiResult
    versions: RetryAiVersions
