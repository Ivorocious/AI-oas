"""Closed request and response contracts for proposal commands."""

import uuid
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    model_validator,
)

PositiveVersion = Annotated[StrictInt, Field(gt=0)]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Destination(ClosedModel):
    kind: Literal["Email", "Phone"]
    value: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=320)]


class Scheduling(ClosedModel):
    window_start: AwareDatetime
    window_end: AwareDatetime
    notes: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
        | None
    ) = None

    @model_validator(mode="after")
    def ordered(self) -> "Scheduling":
        if self.window_end <= self.window_start:
            raise ValueError("scheduling window end must be after start")
        return self


class ProposalPayload(ClosedModel):
    action_type: Literal["CustomerMessage", "SchedulingInvitation"]
    destination: Destination
    content: Annotated[str, StringConstraints(min_length=1, max_length=10000)]
    scheduling: Scheduling | None = None


class RequestExpectedVersion(ClosedModel):
    service_request: PositiveVersion


class ProposalExpectedVersion(ClosedModel):
    proposed_action: PositiveVersion


class BothExpectedVersions(ClosedModel):
    service_request: PositiveVersion
    proposed_action: PositiveVersion


class CreateDraftRequest(ClosedModel):
    schema_version: Literal["1.0"]
    expected_versions: RequestExpectedVersion
    proposal: ProposalPayload


class EditDraftRequest(ClosedModel):
    schema_version: Literal["1.0"]
    expected_versions: ProposalExpectedVersion
    proposal: ProposalPayload


class SubmitProposalRequest(ClosedModel):
    schema_version: Literal["1.0"]
    expected_versions: BothExpectedVersions


class DecideProposalRequest(ClosedModel):
    schema_version: Literal["1.0"]
    expected_versions: BothExpectedVersions
    expected_payload_digest: Digest


class RejectProposalRequest(DecideProposalRequest):
    rationale: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=20, max_length=1000)
    ]


class MaterialRevisionRequest(ClosedModel):
    schema_version: Literal["1.0"]
    expected_versions: BothExpectedVersions
    proposal: ProposalPayload


class ProposalResult(ClosedModel):
    service_request_id: uuid.UUID
    proposed_action_id: uuid.UUID
    proposal_series_id: uuid.UUID
    logical_operation_id: uuid.UUID
    proposal_number: PositiveVersion
    proposal_state: str
    payload_digest: Digest
    service_request_status: str
    service_request_queue: str | None


class ProposalVersions(ClosedModel):
    service_request: PositiveVersion
    proposed_action: PositiveVersion


class ProposalCommandResponse(ClosedModel):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    command_id: uuid.UUID
    result: ProposalResult
    versions: ProposalVersions
