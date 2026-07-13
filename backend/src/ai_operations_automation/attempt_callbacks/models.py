"""Closed AI integration-attempt callback transport contracts."""

import uuid
from decimal import Decimal
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

ServiceCategory = Literal[
    "Consultation",
    "Installation",
    "Repair",
    "RoutineMaintenance",
    "Inspection",
    "OtherCustomRequest",
]
RetryableAiFailureCode = Literal[
    "WORKFLOW_FAILED_BEFORE_PROVIDER_INVOCATION",
    "PROVIDER_CONNECTION_FAILED",
    "PROVIDER_TIMEOUT",
    "PROVIDER_RATE_LIMITED",
    "PROVIDER_TEMPORARILY_UNAVAILABLE",
    "PROVIDER_RESPONSE_SCHEMA_INVALID",
]
TerminalAiFailureCode = Literal[
    "PROVIDER_AUTHENTICATION_FAILED",
    "PROVIDER_AUTHORIZATION_FAILED",
    "PROVIDER_CONFIGURATION_INVALID",
    "PROVIDER_REQUEST_REJECTED",
]

BoundedLabel = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]
SafeProviderCorrelation = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]
InterpretationSummary = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
]
StableEvidenceCode = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,99}$"),
]
PositiveVersion = Annotated[StrictInt, Field(gt=0)]


class ClosedModel(BaseModel):
    """Forbid fields that are not part of the reviewed transport contract."""

    model_config = ConfigDict(extra="forbid")


class AiCallbackExpectedVersions(ClosedModel):
    integration_attempt: PositiveVersion


class AiTokenUsageEvidence(ClosedModel):
    """Allowlisted aggregate token counts; no provider-specific metadata."""

    input_tokens: StrictInt = Field(ge=0, le=10_000_000)
    output_tokens: StrictInt = Field(ge=0, le=10_000_000)


class AiInterpretationEvidence(ClosedModel):
    """Validated advisory evidence; none of these fields is canonical routing state."""

    summary: InterpretationSummary
    suggested_category: ServiceCategory
    missing_information: list[StableEvidenceCode] = Field(default_factory=list, max_length=32)
    confidence: Decimal = Field(ge=0, le=1, max_digits=5, decimal_places=4)
    warning_codes: list[StableEvidenceCode] = Field(default_factory=list, max_length=32)


class AiSuccessEvidence(ClosedModel):
    result_schema_version: BoundedLabel
    prompt_version: BoundedLabel
    provider_name: BoundedLabel
    model_name: BoundedLabel
    adapter_name: BoundedLabel
    adapter_version: BoundedLabel
    safe_provider_correlation: SafeProviderCorrelation | None = None
    latency_ms: StrictInt | None = Field(default=None, ge=0, le=3_600_000)
    token_usage: AiTokenUsageEvidence | None = None
    interpretation: AiInterpretationEvidence


class AiSuccessCallbackRequest(ClosedModel):
    schema_version: Literal["1.0"]
    expected_versions: AiCallbackExpectedVersions
    evidence: AiSuccessEvidence


class AiRetryableFailureEvidence(ClosedModel):
    failure_code: RetryableAiFailureCode
    adapter_version: BoundedLabel
    safe_provider_correlation: SafeProviderCorrelation | None = None
    safe_reason_codes: list[StableEvidenceCode] = Field(default_factory=list, max_length=16)
    provider_status_code: StrictInt | None = Field(default=None, ge=400, le=599)
    duration_ms: StrictInt | None = Field(default=None, ge=0, le=3_600_000)
    retry_after_seconds: StrictInt | None = Field(default=None, ge=1, le=86_400)
    response_schema_version: BoundedLabel | None = None


class AiRetryableFailureCallbackRequest(ClosedModel):
    schema_version: Literal["1.0"]
    expected_versions: AiCallbackExpectedVersions
    evidence: AiRetryableFailureEvidence


class AiTerminalFailureEvidence(ClosedModel):
    failure_code: TerminalAiFailureCode
    adapter_version: BoundedLabel
    safe_provider_correlation: SafeProviderCorrelation | None = None
    safe_reason_codes: list[StableEvidenceCode] = Field(default_factory=list, max_length=16)
    provider_status_code: StrictInt | None = Field(default=None, ge=400, le=599)


class AiTerminalFailureCallbackRequest(ClosedModel):
    schema_version: Literal["1.0"]
    expected_versions: AiCallbackExpectedVersions
    evidence: AiTerminalFailureEvidence


class AiCallbackVersions(ClosedModel):
    service_request: PositiveVersion
    logical_operation: PositiveVersion
    integration_attempt: PositiveVersion


class AiSuccessCallbackResult(ClosedModel):
    service_request_id: uuid.UUID
    logical_operation_id: uuid.UUID
    integration_attempt_id: uuid.UUID
    interpretation_id: uuid.UUID
    attempt_number: StrictInt = Field(ge=1, le=3)
    attempt_state: Literal["Succeeded"]
    service_request_status: Literal["TriagePending"]
    completed_at: AwareDatetime


class AiSuccessCallbackResponse(ClosedModel):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    command_id: uuid.UUID
    result: AiSuccessCallbackResult
    versions: AiCallbackVersions


class AiRetryableFailureCallbackResult(ClosedModel):
    service_request_id: uuid.UUID
    logical_operation_id: uuid.UUID
    integration_attempt_id: uuid.UUID
    attempt_state: Literal["RetryableFailure", "TerminalFailure"]
    service_request_status: Literal["RetryableFailure", "TerminalFailure"]
    service_request_queue: Literal["FailedRetryRequired"] | None
    failure_code: RetryableAiFailureCode
    recovery_disposition: Literal["RetrySameOperation", "Terminal"]
    attempt_number: StrictInt = Field(ge=1, le=3)
    maximum_attempts: Literal[3]
    remaining_attempts: StrictInt = Field(ge=0, le=2)
    next_eligible_at: AwareDatetime | None
    completed_at: AwareDatetime

    @model_validator(mode="after")
    def validate_derived_recovery_shape(self) -> "AiRetryableFailureCallbackResult":
        if self.attempt_state != self.service_request_status:
            raise ValueError("attempt and request failure states must match")
        if self.attempt_state == "RetryableFailure":
            if (
                self.service_request_queue != "FailedRetryRequired"
                or self.recovery_disposition != "RetrySameOperation"
                or self.remaining_attempts == 0
                or self.next_eligible_at is None
            ):
                raise ValueError("retryable result fields are inconsistent")
        elif (
            self.service_request_queue is not None
            or self.recovery_disposition != "Terminal"
            or self.remaining_attempts != 0
            or self.next_eligible_at is not None
        ):
            raise ValueError("exhausted terminal result fields are inconsistent")
        return self


class AiRetryableFailureCallbackResponse(ClosedModel):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    command_id: uuid.UUID
    result: AiRetryableFailureCallbackResult
    versions: AiCallbackVersions


class AiTerminalFailureCallbackResult(ClosedModel):
    service_request_id: uuid.UUID
    logical_operation_id: uuid.UUID
    integration_attempt_id: uuid.UUID
    attempt_state: Literal["TerminalFailure"]
    service_request_status: Literal["TerminalFailure"]
    service_request_queue: None = None
    failure_code: TerminalAiFailureCode
    recovery_disposition: Literal["Terminal"]
    attempt_number: StrictInt = Field(ge=1, le=3)
    maximum_attempts: Literal[3]
    remaining_attempts: StrictInt = Field(ge=0, le=2)
    next_eligible_at: None = None
    completed_at: AwareDatetime


class AiTerminalFailureCallbackResponse(ClosedModel):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    command_id: uuid.UUID
    result: AiTerminalFailureCallbackResult
    versions: AiCallbackVersions
