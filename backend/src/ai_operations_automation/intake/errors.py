"""Stable safe intake error envelope."""

import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    issue_code: str


class ErrorBody(BaseModel):
    schema_version: str = "1.0"
    code: str
    message: str
    correlation_id: uuid.UUID
    delivery_id: uuid.UUID | None = None
    retryable: bool = False
    current_versions: dict[str, int] = Field(default_factory=dict)
    details: list[ErrorDetail] = Field(default_factory=list)


class ErrorEnvelope(BaseModel):
    error: ErrorBody


@dataclass(slots=True)
class IntakeError(Exception):
    status_code: int
    code: str
    message: str
    retryable: bool = False
    details: list[dict[str, str]] = field(default_factory=list)
    delivery_id: uuid.UUID | None = None

    def response(self, correlation_id: uuid.UUID) -> dict[str, Any]:
        return ErrorEnvelope(
            error=ErrorBody(
                code=self.code,
                message=self.message,
                correlation_id=correlation_id,
                delivery_id=self.delivery_id,
                retryable=self.retryable,
                details=[ErrorDetail(**detail) for detail in self.details],
            )
        ).model_dump(mode="json", exclude_none=True)


def safe_validation_details(errors: list[dict[str, Any]]) -> list[dict[str, str]]:
    details: list[dict[str, str]] = []
    for error in errors:
        error_type = str(error.get("type", ""))
        if error_type == "missing":
            issue_code = "REQUIRED_FIELD_MISSING"
        elif error_type == "extra_forbidden":
            issue_code = "UNKNOWN_FIELD"
        else:
            issue_code = "INVALID_FIELD"
        details.append(
            {
                "field": ".".join(str(part) for part in error.get("loc", ())) or "body",
                "issue_code": issue_code,
            }
        )
    return details
