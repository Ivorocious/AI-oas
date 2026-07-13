"""Closed duplicate-resolution request and response models."""

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, model_validator

PositiveVersion = Annotated[StrictInt, Field(gt=0)]
DuplicateDecision = Literal["ConfirmedDuplicate", "NotDuplicate"]
DuplicateRationale = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=20, max_length=1000),
]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResolveDuplicateExpectedVersions(ClosedModel):
    service_request: PositiveVersion


class ResolveDuplicateCommand(ClosedModel):
    decision: DuplicateDecision
    rationale: DuplicateRationale | None = None


class ResolveDuplicateRequest(ClosedModel):
    schema_version: Literal["1.0"]
    expected_versions: ResolveDuplicateExpectedVersions
    command: ResolveDuplicateCommand


class ResolveDuplicateResult(ClosedModel):
    service_request_id: uuid.UUID
    duplicate_candidate_id: uuid.UUID
    candidate_resolution: DuplicateDecision
    service_request_status: Literal[
        "TriagePending",
        "DuplicateReview",
        "ClosedDuplicate",
    ]
    service_request_queue: Literal["DuplicateReview"] | None

    @model_validator(mode="after")
    def validate_backend_derived_transition(self) -> "ResolveDuplicateResult":
        if self.candidate_resolution == "ConfirmedDuplicate":
            if self.service_request_status != "ClosedDuplicate" or self.service_request_queue:
                raise ValueError("confirmed duplicate result is inconsistent")
        elif (
            self.service_request_status,
            self.service_request_queue,
        ) not in (("TriagePending", None), ("DuplicateReview", "DuplicateReview")):
            raise ValueError("not-duplicate result is inconsistent")
        return self


class ResolveDuplicateVersions(ClosedModel):
    service_request: PositiveVersion


class ResolveDuplicateResponse(ClosedModel):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    command_id: uuid.UUID
    result: ResolveDuplicateResult
    versions: ResolveDuplicateVersions
