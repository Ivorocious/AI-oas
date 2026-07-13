"""Closed manager/administrator terminal-disposition transport contracts."""

import uuid
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StringConstraints

from ai_operations_automation.failure_recovery import FailureCode

TerminalRationale = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=20, max_length=1000),
]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MarkTerminalFailureExpectedVersions(ClosedModel):
    service_request: StrictInt = Field(gt=0)


class MarkTerminalFailureCommand(ClosedModel):
    failed_attempt_id: uuid.UUID
    rationale: TerminalRationale


class MarkTerminalFailureRequest(ClosedModel):
    schema_version: Literal["1.0"]
    expected_versions: MarkTerminalFailureExpectedVersions
    command: MarkTerminalFailureCommand


class MarkTerminalFailureResult(ClosedModel):
    service_request_id: uuid.UUID
    failed_attempt_id: uuid.UUID
    service_request_status: Literal["TerminalFailure"]
    service_request_queue: None = None
    failure_code: FailureCode
    terminal_disposition_code: Literal[
        "MANAGER_TERMINAL_DISPOSITION",
        "ADMINISTRATOR_TERMINAL_DISPOSITION",
    ]
    terminal_at: AwareDatetime


class MarkTerminalFailureVersions(ClosedModel):
    service_request: StrictInt = Field(gt=0)


class MarkTerminalFailureResponse(ClosedModel):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    command_id: uuid.UUID
    result: MarkTerminalFailureResult
    versions: MarkTerminalFailureVersions
