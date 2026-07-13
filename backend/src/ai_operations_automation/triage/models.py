"""Closed in-process command contracts for authoritative triage."""

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictInt

from ai_operations_automation.deterministic_decision.models import (
    DamageOrDeterioration,
    MaterialImpact,
    SafetyOrContinuityConcern,
    ServiceCategory,
    ServiceInterruption,
    ServiceMode,
)


class ClosedImmutableModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExpectedDecisionPolicy(ClosedImmutableModel):
    policy_key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9-]*$")
    semantic_version: str = Field(
        min_length=5,
        max_length=32,
        pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$",
    )
    revision: StrictInt = Field(gt=0)


class AuthoritativeDecisionFacts(ClosedImmutableModel):
    """Allowlisted facts produced inside the trusted backend normalization seam."""

    explicit_category: ServiceCategory | None = None
    timing_is_flexible: StrictBool = False
    requested_deadline: AwareDatetime | None = None
    requested_service_date: date | None = None
    service_mode: ServiceMode = ServiceMode.UNSPECIFIED
    access_constraints_known: StrictBool = False
    consultation_topic_present: StrictBool = False
    desired_outcome_present: StrictBool = False
    installation_target_present: StrictBool = False
    installation_scope_present: StrictBool = False
    repair_symptoms_present: StrictBool = False
    repair_asset_context_present: StrictBool = False
    maintenance_asset_context_present: StrictBool = False
    inspection_subject_present: StrictBool = False
    inspection_purpose_present: StrictBool = False
    custom_scope_present: StrictBool = False
    safety_or_continuity_concern: SafetyOrContinuityConcern = SafetyOrContinuityConcern.NONE
    service_interruption: ServiceInterruption = ServiceInterruption.NONE
    damage_or_deterioration: DamageOrDeterioration = DamageOrDeterioration.NONE
    material_impact: MaterialImpact = MaterialImpact.NONE
    routing_evidence_usable: StrictBool = True


class CompleteTriageCommand(ClosedImmutableModel):
    expected_service_request_version: StrictInt = Field(gt=0)
    facts: AuthoritativeDecisionFacts
    expected_policy: ExpectedDecisionPolicy | None = None


@dataclass(frozen=True, slots=True)
class CompleteTriageOutcome:
    logical_http_status: int
    command_id: uuid.UUID
    safe_snapshot: dict[str, Any]
    is_replay: bool
