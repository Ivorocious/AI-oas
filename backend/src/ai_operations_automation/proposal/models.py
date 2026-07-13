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
    approval_decision_id: uuid.UUID | None = None
    source_proposed_action_id: uuid.UUID | None = None
    source_proposal_state: str | None = None
    replacement_proposed_action_id: uuid.UUID | None = None
    replacement_proposal_state: str | None = None
    recovery_cleared: bool | None = None

    @model_validator(mode="after")
    def command_specific_identity_is_complete(self) -> "ProposalResult":
        revision = (
            self.source_proposed_action_id,
            self.source_proposal_state,
            self.replacement_proposed_action_id,
            self.replacement_proposal_state,
            self.recovery_cleared,
        )
        if any(value is not None for value in revision):
            if any(value is None for value in revision):
                raise ValueError("material-revision response metadata must be complete")
            if (
                self.replacement_proposed_action_id != self.proposed_action_id
                or self.replacement_proposal_state != self.proposal_state
                or self.replacement_proposal_state != "Draft"
            ):
                raise ValueError("material-revision response metadata is inconsistent")
        if self.approval_decision_id is not None and self.proposal_state not in {
            "Approved",
            "Rejected",
        }:
            raise ValueError("approval decision identity is not valid for this result")
        return self


class ProposalVersions(ClosedModel):
    service_request: PositiveVersion
    proposed_action: PositiveVersion


class ProposalCommandResponse(ClosedModel):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    command_id: uuid.UUID
    result: ProposalResult
    versions: ProposalVersions
