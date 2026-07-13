"""Closed contracts for deterministic triage and reviewed-fact recalculation."""

import uuid
from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)


class ClosedImmutableModel(BaseModel):
    """Policy values are closed and immutable after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ServiceCategory(StrEnum):
    CONSULTATION = "Consultation"
    INSTALLATION = "Installation"
    REPAIR = "Repair"
    ROUTINE_MAINTENANCE = "RoutineMaintenance"
    INSPECTION = "Inspection"
    OTHER_CUSTOM_REQUEST = "OtherCustomRequest"


class Priority(StrEnum):
    URGENT = "Urgent"
    HIGH = "High"
    LOW = "Low"
    NORMAL = "Normal"


class RequestStatus(StrEnum):
    HUMAN_REVIEW = "HumanReview"
    DUPLICATE_REVIEW = "DuplicateReview"
    READY_FOR_ACTION = "ReadyForAction"


class OperationalQueue(StrEnum):
    HUMAN_REVIEW = "HumanReview"
    DUPLICATE_REVIEW = "DuplicateReview"
    PRIORITY_REQUESTS = "PriorityRequests"
    STANDARD_REQUESTS = "StandardRequests"


class DecisionSource(StrEnum):
    INITIAL = "InitialDeterministicCalculation"
    REVIEWED_FACT_RECALCULATION = "ReviewedFactRecalculation"


class ServiceMode(StrEnum):
    ON_SITE = "OnSite"
    REMOTE = "Remote"
    UNSPECIFIED = "Unspecified"


class SafetyOrContinuityConcern(StrEnum):
    NONE = "None"
    REPORTED = "Reported"
    CRITICAL = "Critical"
    UNKNOWN = "Unknown"


class ServiceInterruption(StrEnum):
    NONE = "None"
    ACTIVE = "Active"
    UNKNOWN = "Unknown"


class DamageOrDeterioration(StrEnum):
    NONE = "None"
    ACTIVE = "Active"
    RAPID = "Rapid"
    UNKNOWN = "Unknown"


class MaterialImpact(StrEnum):
    NONE = "None"
    MINOR = "Minor"
    MAJOR = "Major"
    SEVERE = "Severe"
    UNKNOWN = "Unknown"


class CandidateKind(StrEnum):
    CONTACT = "Contact"
    SERVICE_REQUEST = "ServiceRequest"


class CandidateDisposition(StrEnum):
    PENDING = "Pending"
    NOT_DUPLICATE = "NotDuplicate"
    CONFIRMED_DUPLICATE = "ConfirmedDuplicate"


class UrgentReviewDisposition(StrEnum):
    CONFIRMED_AND_ACTIONABLE = "ConfirmedAndActionable"


class PolicyStatus(StrEnum):
    DRAFT = "Draft"
    ACTIVE = "Active"
    RETIRED = "Retired"


class CategoryReasonCode(StrEnum):
    REVIEWED_CORRECTION = "CATEGORY_REVIEWED_CORRECTION"
    EXPLICIT_SELECTION_ACCEPTED = "CATEGORY_EXPLICIT_SELECTION_ACCEPTED"
    NORMALIZED_EVIDENCE = "CATEGORY_NORMALIZED_EVIDENCE"
    AI_AGREES = "CATEGORY_AI_AGREES"
    AI_CONFLICT = "CATEGORY_AI_CONFLICT"
    CONFLICT = "CATEGORY_CONFLICT"
    MULTIPLE_PLAUSIBLE = "CATEGORY_MULTIPLE_PLAUSIBLE"
    EVIDENCE_UNUSABLE = "CATEGORY_EVIDENCE_UNUSABLE"
    OTHER_CUSTOM_SCOPE = "CATEGORY_OTHER_CUSTOM_SCOPE"


class MissingInformationCode(StrEnum):
    CONTACT_METHOD = "MISSING_CONTACT_METHOD"
    TIMING_PREFERENCE = "MISSING_TIMING_PREFERENCE"
    SERVICE_LOCATION = "MISSING_SERVICE_LOCATION"
    ACCESS_CONSTRAINTS = "MISSING_ACCESS_CONSTRAINTS"
    CONSULTATION_TOPIC = "MISSING_CONSULTATION_TOPIC"
    DESIRED_OUTCOME = "MISSING_DESIRED_OUTCOME"
    INSTALLATION_TARGET = "MISSING_INSTALLATION_TARGET"
    INSTALLATION_SCOPE = "MISSING_INSTALLATION_SCOPE"
    REPAIR_SYMPTOMS = "MISSING_REPAIR_SYMPTOMS"
    REPAIR_ASSET_CONTEXT = "MISSING_REPAIR_ASSET_CONTEXT"
    MAINTENANCE_ASSET_CONTEXT = "MISSING_MAINTENANCE_ASSET_CONTEXT"
    INSPECTION_SUBJECT = "MISSING_INSPECTION_SUBJECT"
    INSPECTION_PURPOSE = "MISSING_INSPECTION_PURPOSE"
    CUSTOM_SCOPE = "MISSING_CUSTOM_SCOPE"
    CUSTOM_SCOPE_CONFIRMATION = "MISSING_CUSTOM_SCOPE_CONFIRMATION"


class PriorityReasonCode(StrEnum):
    CRITICAL_SAFETY_OR_CONTINUITY = "PRIORITY_CRITICAL_SAFETY_OR_CONTINUITY"
    ACTIVE_INTERRUPTION_IMMEDIATE = "PRIORITY_ACTIVE_INTERRUPTION_IMMEDIATE"
    RAPID_DAMAGE_IMMEDIATE = "PRIORITY_RAPID_DAMAGE_IMMEDIATE"
    SEVERE_IMPACT_IMMEDIATE = "PRIORITY_SEVERE_IMPACT_IMMEDIATE"
    ACTIVE_INTERRUPTION = "PRIORITY_ACTIVE_INTERRUPTION"
    ACTIVE_DAMAGE_OR_DETERIORATION = "PRIORITY_ACTIVE_DAMAGE_OR_DETERIORATION"
    MAJOR_OR_SEVERE_IMPACT = "PRIORITY_MAJOR_OR_SEVERE_IMPACT"
    NEAR_TERM_DEADLINE = "PRIORITY_NEAR_TERM_DEADLINE"
    FLEXIBLE_ROUTINE_WORK = "PRIORITY_FLEXIBLE_ROUTINE_WORK"
    DEFAULT_NORMAL = "PRIORITY_DEFAULT_NORMAL"


class DuplicateReasonCode(StrEnum):
    EXACT_EMAIL = "DUPLICATE_EXACT_EMAIL"
    EXACT_PHONE = "DUPLICATE_EXACT_PHONE"
    EXISTING_CONTACT = "DUPLICATE_EXISTING_CONTACT"
    EXACT_DESCRIPTION = "DUPLICATE_EXACT_DESCRIPTION"
    DESCRIPTION_SIMILARITY = "DUPLICATE_DESCRIPTION_SIMILARITY"
    CATEGORY_MATCH = "DUPLICATE_CATEGORY_MATCH"
    LOCATION_MATCH = "DUPLICATE_LOCATION_MATCH"
    TIMING_PROXIMITY = "DUPLICATE_TIMING_PROXIMITY"


class ReviewReasonCode(StrEnum):
    ROUTING_EVIDENCE_UNAVAILABLE = "REVIEW_ROUTING_EVIDENCE_UNAVAILABLE"
    POSSIBLE_DUPLICATE = "REVIEW_POSSIBLE_DUPLICATE"
    URGENT_PRIORITY = "REVIEW_URGENT_PRIORITY"
    REPORTED_SAFETY_OR_CONTINUITY = "REVIEW_REPORTED_SAFETY_OR_CONTINUITY"
    MISSING_REQUIRED_INFORMATION = "REVIEW_MISSING_REQUIRED_INFORMATION"
    LOW_AI_CONFIDENCE = "REVIEW_LOW_AI_CONFIDENCE"
    AI_POSSIBLE_SAFETY_OR_CONTINUITY = "REVIEW_AI_POSSIBLE_SAFETY_OR_CONTINUITY"
    AI_MISSING_INFORMATION_CONFLICT = "REVIEW_AI_MISSING_INFORMATION_CONFLICT"
    CATEGORY_AMBIGUITY = "REVIEW_CATEGORY_AMBIGUITY"
    CATEGORY_CONFLICT = "REVIEW_CATEGORY_CONFLICT"
    OTHER_CUSTOM_SCOPE = "REVIEW_OTHER_CUSTOM_SCOPE"


class DecisionPolicyIdentity(ClosedImmutableModel):
    policy_id: uuid.UUID
    policy_key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9-]*$")
    semantic_version: str = Field(min_length=5, max_length=32, pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    revision: StrictInt = Field(gt=0)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class RequiredInformationRule(ClosedImmutableModel):
    category: ServiceCategory
    required_codes: tuple[MissingInformationCode, ...]


class DuplicateWeightRule(ClosedImmutableModel):
    reason_code: DuplicateReasonCode
    weight: StrictInt = Field(gt=0, le=100)


class QueueMappingRule(ClosedImmutableModel):
    status: RequestStatus
    priority: Priority | None
    queue: OperationalQueue


class DecisionPolicyThresholds(ClosedImmutableModel):
    ai_confidence_review: Decimal = Field(ge=0, le=1)
    duplicate_lookback_days: StrictInt = Field(gt=0)
    duplicate_retention_score: StrictInt = Field(gt=0, le=100)
    duplicate_review_score: StrictInt = Field(gt=0, le=100)
    description_similarity: Decimal = Field(ge=0, le=1)
    duplicate_timing_days: StrictInt = Field(gt=0)
    urgent_deadline_hours: StrictInt = Field(gt=0)
    high_deadline_hours: StrictInt = Field(gt=0)
    low_flexible_days: StrictInt = Field(gt=0)


class DecisionPolicyContent(ClosedImmutableModel):
    categories: tuple[ServiceCategory, ...]
    category_resolution_order: tuple[str, ...]
    required_information_rules: tuple[RequiredInformationRule, ...]
    priority_precedence: tuple[Priority, ...]
    thresholds: DecisionPolicyThresholds
    duplicate_weights: tuple[DuplicateWeightRule, ...]
    review_precedence_groups: tuple[tuple[ReviewReasonCode, ...], ...]
    queue_mapping: tuple[QueueMappingRule, ...]
    category_reason_catalog: tuple[CategoryReasonCode, ...]
    missing_information_catalog: tuple[MissingInformationCode, ...]
    priority_reason_catalog: tuple[PriorityReasonCode, ...]
    duplicate_reason_catalog: tuple[DuplicateReasonCode, ...]
    review_reason_catalog: tuple[ReviewReasonCode, ...]


class DecisionPolicyVersion(ClosedImmutableModel):
    id: uuid.UUID
    policy_key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9-]*$")
    semantic_version: str = Field(min_length=5, max_length=32, pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    revision: StrictInt = Field(gt=0)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_at: AwareDatetime
    status: PolicyStatus
    content: DecisionPolicyContent

    @property
    def identity(self) -> DecisionPolicyIdentity:
        return DecisionPolicyIdentity(
            policy_id=self.id,
            policy_key=self.policy_key,
            semantic_version=self.semantic_version,
            revision=self.revision,
            content_digest=self.content_digest,
        )


Digest = str


class NormalizedDecisionFacts(ClosedImmutableModel):
    source_request_id: uuid.UUID
    explicit_category: ServiceCategory | None = None
    contact_method_present: bool = False
    timing_preference_present: bool = False
    timing_is_flexible: bool = False
    requested_deadline: AwareDatetime | None = None
    requested_service_date: date | None = None
    service_mode: ServiceMode = ServiceMode.UNSPECIFIED
    location_or_service_context_present: bool = False
    access_constraints_known: bool = False
    consultation_topic_present: bool = False
    desired_outcome_present: bool = False
    installation_target_present: bool = False
    installation_scope_present: bool = False
    repair_symptoms_present: bool = False
    repair_asset_context_present: bool = False
    maintenance_asset_context_present: bool = False
    inspection_subject_present: bool = False
    inspection_purpose_present: bool = False
    custom_scope_present: bool = False
    safety_or_continuity_concern: SafetyOrContinuityConcern = SafetyOrContinuityConcern.NONE
    service_interruption: ServiceInterruption = ServiceInterruption.NONE
    damage_or_deterioration: DamageOrDeterioration = DamageOrDeterioration.NONE
    material_impact: MaterialImpact = MaterialImpact.NONE
    contact_id: uuid.UUID | None = None
    normalized_email_digest: Digest | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    normalized_phone_digest: Digest | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    description_fingerprint: Digest | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    description_token_digests: tuple[Digest, ...] = ()
    location_or_service_context_digest: Digest | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )

    @field_validator("description_token_digests")
    @classmethod
    def normalize_token_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            len(item) != 64 or any(char not in "0123456789abcdef" for char in item)
            for item in value
        ):
            raise ValueError("description token digests must be lowercase SHA-256 values")
        return tuple(sorted(set(value)))


class AIAdvisory(ClosedImmutableModel):
    confidence: Decimal = Field(ge=0, le=1)
    suggested_category: ServiceCategory | None = None
    missing_information_codes: tuple[MissingInformationCode, ...] = ()
    possible_safety_or_continuity: bool = False

    @field_validator("confidence")
    @classmethod
    def normalize_confidence(cls, value: Decimal) -> Decimal:
        return value.normalize()

    @field_validator("missing_information_codes")
    @classmethod
    def unique_missing_codes(
        cls, value: tuple[MissingInformationCode, ...]
    ) -> tuple[MissingInformationCode, ...]:
        selected = set(value)
        if len(selected) != len(value):
            raise ValueError("AI missing-information codes must be unique")
        return tuple(code for code in MissingInformationCode if code in selected)


class DuplicateCandidateInput(ClosedImmutableModel):
    observation_id: uuid.UUID | None = None
    candidate_kind: CandidateKind
    candidate_id: uuid.UUID
    candidate_activity_at: AwareDatetime
    candidate_evidence_hash: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    disposition: CandidateDisposition = CandidateDisposition.PENDING
    eligible_record: bool = True
    contact_id: uuid.UUID | None = None
    normalized_email_digest: Digest | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    normalized_phone_digest: Digest | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    description_fingerprint: Digest | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    description_token_digests: tuple[Digest, ...] = ()
    final_category: ServiceCategory | None = None
    location_or_service_context_digest: Digest | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    requested_service_date: date | None = None

    @field_validator("description_token_digests")
    @classmethod
    def normalize_token_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            len(item) != 64 or any(char not in "0123456789abcdef" for char in item)
            for item in value
        ):
            raise ValueError("description token digests must be lowercase SHA-256 values")
        return tuple(sorted(set(value)))


class ReviewedFacts(ClosedImmutableModel):
    fact_set_id: uuid.UUID
    corrected_category: ServiceCategory | None = None
    corrected_requested_deadline: AwareDatetime | None = None
    corrected_timing_preference_present: bool | None = None
    corrected_timing_is_flexible: bool | None = None
    corrected_safety_or_continuity_concern: SafetyOrContinuityConcern | None = None
    corrected_service_interruption: ServiceInterruption | None = None
    corrected_damage_or_deterioration: DamageOrDeterioration | None = None
    corrected_material_impact: MaterialImpact | None = None
    resolved_missing_information_codes: tuple[MissingInformationCode, ...] = ()
    custom_scope_confirmed: bool | None = None
    urgent_review_disposition: UrgentReviewDisposition | None = None
    rationale_reference: str = Field(min_length=1, max_length=200)
    supporting_evidence_references: tuple[str, ...] = Field(min_length=1, max_length=20)

    @field_validator("resolved_missing_information_codes")
    @classmethod
    def unique_resolved_codes(
        cls, value: tuple[MissingInformationCode, ...]
    ) -> tuple[MissingInformationCode, ...]:
        selected = set(value)
        if len(selected) != len(value):
            raise ValueError("resolved missing-information codes must be unique")
        return tuple(code for code in MissingInformationCode if code in selected)

    @field_validator("supporting_evidence_references")
    @classmethod
    def validate_evidence_references(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(not item or len(item) > 200 for item in value):
            raise ValueError("supporting evidence references must be unique and bounded")
        return value

    @model_validator(mode="after")
    def require_material_fact(self) -> "ReviewedFacts":
        values = (
            self.corrected_category,
            self.corrected_requested_deadline,
            self.corrected_timing_preference_present,
            self.corrected_timing_is_flexible,
            self.corrected_safety_or_continuity_concern,
            self.corrected_service_interruption,
            self.corrected_damage_or_deterioration,
            self.corrected_material_impact,
            self.custom_scope_confirmed,
            self.urgent_review_disposition,
        )
        if not self.resolved_missing_information_codes and all(value is None for value in values):
            raise ValueError("a reviewed fact set must contain at least one allowlisted fact")
        return self


class DecisionEvaluationInput(ClosedImmutableModel):
    evaluation_at: AwareDatetime
    normalized_facts: NormalizedDecisionFacts
    interpretation_id: uuid.UUID
    interpretation_version: StrictInt = Field(gt=0)
    interpretation_evidence_hash: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    ai_advisory: AIAdvisory
    duplicate_candidates: tuple[DuplicateCandidateInput, ...] = ()
    reviewed_fact_set_ids: tuple[uuid.UUID, ...] = ()
    reviewed_facts: ReviewedFacts | None = None
    routing_evidence_usable: bool = True
    source: DecisionSource = DecisionSource.INITIAL
    current_priority: Priority | None = None

    @model_validator(mode="after")
    def validate_review_and_candidate_identity(self) -> "DecisionEvaluationInput":
        identities = [
            (item.candidate_kind, item.candidate_id, item.candidate_evidence_hash)
            for item in self.duplicate_candidates
        ]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate candidate evidence identities must be unique")
        if len(set(self.reviewed_fact_set_ids)) != len(self.reviewed_fact_set_ids):
            raise ValueError("reviewed fact set identities must be unique")
        if self.source is DecisionSource.REVIEWED_FACT_RECALCULATION:
            if self.reviewed_facts is None:
                raise ValueError("reviewed-fact recalculation requires reviewed facts")
            if self.reviewed_facts.fact_set_id not in self.reviewed_fact_set_ids:
                raise ValueError(
                    "current reviewed fact set must be included in evidence identities"
                )
        elif self.reviewed_facts is not None:
            raise ValueError("initial calculation cannot apply reviewed facts")
        return self


class DuplicateCandidateResult(ClosedImmutableModel):
    observation_id: uuid.UUID | None
    candidate_kind: CandidateKind
    candidate_id: uuid.UUID
    candidate_activity_at: AwareDatetime
    candidate_evidence_hash: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    disposition: CandidateDisposition
    score: StrictInt = Field(ge=0, le=100)
    description_similarity: Decimal = Field(ge=0, le=1)
    reason_codes: tuple[DuplicateReasonCode, ...]


class DecisionEvaluation(ClosedImmutableModel):
    policy: DecisionPolicyIdentity
    evaluation_at: AwareDatetime
    canonical_input_hash: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    source: DecisionSource
    final_category: ServiceCategory
    final_priority: Priority
    final_status: RequestStatus
    final_queue: OperationalQueue
    review_required: bool
    category_reason_codes: tuple[CategoryReasonCode, ...]
    priority_reason_codes: tuple[PriorityReasonCode, ...]
    missing_information_codes: tuple[MissingInformationCode, ...]
    review_reason_codes: tuple[ReviewReasonCode, ...]
    duplicate_candidates: tuple[DuplicateCandidateResult, ...]
    requires_manager_or_administrator: bool
