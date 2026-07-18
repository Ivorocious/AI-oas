"""Closed operational service-request projection."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ServiceRequestView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: uuid.UUID
    status: str
    category: str | None
    priority: str | None
    current_queue: str | None
    description: str
    location_context: str | None
    timing_preference: str | None
    review_required: bool | None
    review_reason_codes: list[str] | None
    created_at: datetime
    updated_at: datetime
    version: int


class ContactView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: uuid.UUID
    display_name: str
    email: str | None
    phone: str | None
    preferred_channel: str | None
    version: int


class ActiveReferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_interpretation_id: uuid.UUID | None = None
    current_routing_decision_id: uuid.UUID | None = None
    active_proposed_action_id: uuid.UUID | None = None


class ServiceRequestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_request: ServiceRequestView
    contact: ContactView
    active_references: ActiveReferences


class ServiceRequestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    result: ServiceRequestResult


class WorkflowAiServiceRequestView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt_id: uuid.UUID
    logical_operation_id: uuid.UUID
    id: uuid.UUID
    status: str
    description: str
    location_context: str | None
    timing_preference: str | None
    current_interpretation_id: uuid.UUID | None
    version: int


class WorkflowAiServiceRequestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    result: WorkflowAiServiceRequestView


class WorkflowOutboundServiceRequestView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt_id: uuid.UUID
    logical_operation_id: uuid.UUID
    id: uuid.UUID
    status: str
    current_proposed_action_id: uuid.UUID | None
    version: int


class WorkflowOutboundServiceRequestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    result: WorkflowOutboundServiceRequestView
