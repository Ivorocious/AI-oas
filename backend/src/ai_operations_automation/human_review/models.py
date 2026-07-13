"""Closed bounded-fact contracts for deterministic human review."""

import uuid
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

ServiceCategory = Literal[
    "Consultation",
    "Installation",
    "Repair",
    "RoutineMaintenance",
    "Inspection",
    "OtherCustomRequest",
]
Priority = Literal["Low", "Normal", "High", "Urgent"]
MissingInformationCode = Literal[
    "MISSING_CONTACT_METHOD",
    "MISSING_TIMING_PREFERENCE",
    "MISSING_SERVICE_LOCATION",
    "MISSING_ACCESS_CONSTRAINTS",
    "MISSING_CONSULTATION_TOPIC",
    "MISSING_DESIRED_OUTCOME",
    "MISSING_INSTALLATION_TARGET",
    "MISSING_INSTALLATION_SCOPE",
    "MISSING_REPAIR_SYMPTOMS",
    "MISSING_REPAIR_ASSET_CONTEXT",
    "MISSING_MAINTENANCE_ASSET_CONTEXT",
    "MISSING_INSPECTION_SUBJECT",
    "MISSING_INSPECTION_PURPOSE",
    "MISSING_CUSTOM_SCOPE",
    "MISSING_CUSTOM_SCOPE_CONFIRMATION",
]
ReviewReasonCode = Literal[
    "REVIEW_ROUTING_EVIDENCE_UNAVAILABLE",
    "REVIEW_POSSIBLE_DUPLICATE",
    "REVIEW_URGENT_PRIORITY",
    "REVIEW_REPORTED_SAFETY_OR_CONTINUITY",
    "REVIEW_MISSING_REQUIRED_INFORMATION",
    "REVIEW_LOW_AI_CONFIDENCE",
    "REVIEW_AI_POSSIBLE_SAFETY_OR_CONTINUITY",
    "REVIEW_AI_MISSING_INFORMATION_CONFLICT",
    "REVIEW_CATEGORY_AMBIGUITY",
    "REVIEW_CATEGORY_CONFLICT",
    "REVIEW_OTHER_CUSTOM_SCOPE",
]
PositiveVersion = Annotated[StrictInt, Field(gt=0)]
PolicyKey = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,99}$"),
]
SemanticVersion = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$", max_length=32),
]
ReviewRationale = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=20, max_length=1000),
]
EvidenceReference = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,199}$",
    ),
]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CompleteHumanReviewExpectedVersions(ClosedModel):
    service_request: PositiveVersion


class ExpectedDecisionPolicy(ClosedModel):
    policy_key: PolicyKey
    semantic_version: SemanticVersion
    revision: PositiveVersion


class ReviewedFacts(ClosedModel):
    resolved_missing_information_codes: list[MissingInformationCode] = Field(
        default_factory=list,
        max_length=32,
    )
    corrected_category: ServiceCategory | None = None
    custom_scope_confirmed: StrictBool | None = None
    corrected_timing_preference_present: StrictBool | None = None
    corrected_timing_is_flexible: StrictBool | None = None
    corrected_requested_deadline: AwareDatetime | None = None
    corrected_material_impact: Literal["None", "Minor", "Major", "Severe", "Unknown"] | None = None
    corrected_service_interruption: Literal["None", "Active", "Unknown"] | None = None
    corrected_damage_or_deterioration: Literal["None", "Active", "Rapid", "Unknown"] | None = None
    corrected_safety_or_continuity_concern: (
        Literal[
            "None",
            "Reported",
            "Critical",
            "Unknown",
        ]
        | None
    ) = None
    urgent_review_disposition: Literal["ConfirmedAndActionable"] | None = None

    @field_validator("resolved_missing_information_codes")
    @classmethod
    def missing_codes_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("resolved missing-information codes must be unique")
        return value

    @model_validator(mode="after")
    def require_one_reviewed_fact(self) -> "ReviewedFacts":
        scalar_names = (
            "corrected_category",
            "custom_scope_confirmed",
            "corrected_timing_preference_present",
            "corrected_timing_is_flexible",
            "corrected_requested_deadline",
            "corrected_material_impact",
            "corrected_service_interruption",
            "corrected_damage_or_deterioration",
            "corrected_safety_or_continuity_concern",
            "urgent_review_disposition",
        )
        if not self.resolved_missing_information_codes and all(
            getattr(self, name) is None for name in scalar_names
        ):
            raise ValueError("at least one reviewed fact is required")
        return self


class CompleteHumanReviewRequest(ClosedModel):
    schema_version: Literal["1.0"]
    expected_versions: CompleteHumanReviewExpectedVersions
    expected_policy: ExpectedDecisionPolicy | None = None
    reviewed_facts: ReviewedFacts
    addressed_review_reason_codes: list[ReviewReasonCode] = Field(
        min_length=1,
        max_length=32,
    )
    rationale: ReviewRationale
    supporting_evidence_references: list[EvidenceReference] = Field(
        min_length=1,
        max_length=16,
    )

    @field_validator("addressed_review_reason_codes", "supporting_evidence_references")
    @classmethod
    def ordered_values_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("ordered evidence values must be unique")
        return value


class DecisionPolicyIdentity(ClosedModel):
    policy_id: uuid.UUID
    policy_key: PolicyKey
    semantic_version: SemanticVersion
    revision: PositiveVersion
    content_digest: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class CompleteHumanReviewResult(ClosedModel):
    service_request_id: uuid.UUID
    reviewed_fact_set_id: uuid.UUID
    routing_decision_id: uuid.UUID
    routing_decision_version: PositiveVersion
    policy: DecisionPolicyIdentity
    category: ServiceCategory
    priority: Priority
    service_request_status: Literal["HumanReview", "ReadyForAction"]
    service_request_queue: Literal[
        "HumanReview",
        "StandardRequests",
        "PriorityRequests",
    ]
    review_required: bool
    outstanding_review_reason_codes: list[ReviewReasonCode] = Field(max_length=32)

    @field_validator("outstanding_review_reason_codes")
    @classmethod
    def outstanding_codes_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("outstanding review codes must be unique")
        return value

    @model_validator(mode="after")
    def validate_backend_derived_review_result(self) -> "CompleteHumanReviewResult":
        if self.review_required:
            if (
                self.service_request_status != "HumanReview"
                or self.service_request_queue != "HumanReview"
                or not self.outstanding_review_reason_codes
            ):
                raise ValueError("incomplete review result is inconsistent")
        elif (
            self.service_request_status != "ReadyForAction" or self.outstanding_review_reason_codes
        ):
            raise ValueError("completed review result is inconsistent")
        return self


class CompleteHumanReviewVersions(ClosedModel):
    service_request: PositiveVersion


class CompleteHumanReviewResponse(ClosedModel):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    command_id: uuid.UUID
    result: CompleteHumanReviewResult
    versions: CompleteHumanReviewVersions
