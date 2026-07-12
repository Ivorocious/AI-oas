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
    current_interpretation_id: uuid.UUID | None = None
    current_routing_decision_id: None = None
    active_proposed_action_id: None = None


class ServiceRequestResult(BaseModel):
    service_request: ServiceRequestView
    contact: ContactView
    active_references: ActiveReferences


class ServiceRequestResponse(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    correlation_id: uuid.UUID
    result: ServiceRequestResult
