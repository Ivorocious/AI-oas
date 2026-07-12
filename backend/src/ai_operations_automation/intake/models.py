"""Closed public-intake request and response schemas."""

import uuid
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from ai_operations_automation.intake.normalization import normalize_phone, optional_trimmed

TrimmedName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
TrimmedDescription = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=10, max_length=4000)
]


class ContactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: TrimmedName
    email: EmailStr | None = None
    phone: str | None = None
    preferred_channel: Literal["Email", "Phone"] | None = None

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone_field(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("phone must be a string")
        return normalize_phone(value)

    @model_validator(mode="after")
    def validate_contact_methods(self) -> "ContactInput":
        if self.email is None and self.phone is None:
            raise ValueError("at least one contact method is required")
        if self.preferred_channel == "Email" and self.email is None:
            raise ValueError("preferred Email channel requires email")
        if self.preferred_channel == "Phone" and self.phone is None:
            raise ValueError("preferred Phone channel requires phone")
        return self


class ServiceRequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: TrimmedDescription
    location_context: str | None = None
    timing_preference: str | None = None

    @field_validator("location_context", "timing_preference", mode="before")
    @classmethod
    def normalize_optional_context(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("context value must be a string")
        normalized = optional_trimmed(value)
        if normalized is not None and len(normalized) > 500:
            raise ValueError("context value exceeds maximum length")
        return normalized


class IntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    contact: ContactInput
    service_request: ServiceRequestInput


class IntakeResult(BaseModel):
    delivery_id: uuid.UUID
    service_request_id: uuid.UUID | None = None
    intake_outcome: Literal["New", "IdempotentReplay"]
    service_request_status: Literal["TriagePending"] | None = None
    original_delivery_id: uuid.UUID | None = None


class IntakeVersions(BaseModel):
    inbound_delivery: int
    service_request: int | None = None


class IntakeResponse(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    result: IntakeResult
    versions: IntakeVersions
