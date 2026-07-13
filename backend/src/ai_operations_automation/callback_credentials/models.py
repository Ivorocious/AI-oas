"""Closed callback-credential replacement contracts."""

import uuid
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, model_validator


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CallbackCredentialExpectedVersions(ClosedModel):
    integration_attempt: StrictInt = Field(gt=0)
    callback_credential: StrictInt = Field(gt=0)


class EmptyReplacementCommand(ClosedModel):
    pass


class ReplaceCallbackCredentialRequest(ClosedModel):
    schema_version: Literal["1.0"]
    expected_versions: CallbackCredentialExpectedVersions
    command: EmptyReplacementCommand


class ReplaceCallbackCredentialResult(ClosedModel):
    integration_attempt_id: uuid.UUID
    attempt_state: Literal["Pending", "Running"]
    callback_credential_id: uuid.UUID
    callback_credential_version: int = Field(gt=1)
    callback_credential_expires_at: AwareDatetime
    credential_delivery: Literal["PlaintextIssued", "AlreadyIssued"]
    callback_credential: str | None = Field(
        default=None,
        min_length=43,
        max_length=256,
        pattern=r"^[A-Za-z0-9_-]+$",
    )

    @model_validator(mode="after")
    def one_time_plaintext_is_consistent(self) -> "ReplaceCallbackCredentialResult":
        if (self.credential_delivery == "PlaintextIssued") != (
            self.callback_credential is not None
        ):
            raise ValueError("credential delivery fields are inconsistent")
        return self


class ReplaceCallbackCredentialVersions(ClosedModel):
    integration_attempt: int = Field(gt=0)
    callback_credential: int = Field(gt=1)


class ReplaceCallbackCredentialResponse(ClosedModel):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    command_id: uuid.UUID
    result: ReplaceCallbackCredentialResult
    versions: ReplaceCallbackCredentialVersions
