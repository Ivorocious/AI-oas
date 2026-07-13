"""Immutable demonstration decision policy and pure deterministic evaluator."""

import hashlib
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

from ai_operations_automation.deterministic_decision.models import (
    AIAdvisory,
    CandidateDisposition,
    CandidateKind,
    CategoryReasonCode,
    DamageOrDeterioration,
    DecisionEvaluation,
    DecisionEvaluationInput,
    DecisionPolicyContent,
    DecisionPolicyIdentity,
    DecisionPolicyThresholds,
    DecisionPolicyVersion,
    DuplicateCandidateInput,
    DuplicateCandidateResult,
    DuplicateReasonCode,
    DuplicateWeightRule,
    MaterialImpact,
    MissingInformationCode,
    NormalizedDecisionFacts,
    OperationalQueue,
    PolicyStatus,
    Priority,
    PriorityReasonCode,
    QueueMappingRule,
    RequestStatus,
    RequiredInformationRule,
    ReviewedFacts,
    ReviewReasonCode,
    SafetyOrContinuityConcern,
    ServiceCategory,
    ServiceInterruption,
    ServiceMode,
    UrgentReviewDisposition,
)

DEMO_POLICY_ID = uuid.UUID("2ddcb753-84a9-5186-bfab-f8b27e870cab")
DEMO_POLICY_KEY = "general-service-demo"
DEMO_POLICY_SEMANTIC_VERSION = "1.0.0"
DEMO_POLICY_REVISION = 1
DEMO_POLICY_EFFECTIVE_AT = datetime(2026, 7, 11, tzinfo=UTC)


class DecisionPolicyError(ValueError):
    """A stable policy-boundary rejection without unsafe input material."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


CATEGORY_CATALOG = tuple(ServiceCategory)
CATEGORY_REASON_CATALOG = tuple(CategoryReasonCode)
MISSING_INFORMATION_CATALOG = tuple(MissingInformationCode)
PRIORITY_REASON_CATALOG = tuple(PriorityReasonCode)
DUPLICATE_REASON_CATALOG = tuple(DuplicateReasonCode)
REVIEW_REASON_CATALOG = tuple(ReviewReasonCode)

REQUIRED_INFORMATION_RULES = (
    RequiredInformationRule(
        category=ServiceCategory.CONSULTATION,
        required_codes=(
            MissingInformationCode.CONTACT_METHOD,
            MissingInformationCode.TIMING_PREFERENCE,
            MissingInformationCode.SERVICE_LOCATION,
            MissingInformationCode.CONSULTATION_TOPIC,
            MissingInformationCode.DESIRED_OUTCOME,
        ),
    ),
    RequiredInformationRule(
        category=ServiceCategory.INSTALLATION,
        required_codes=(
            MissingInformationCode.CONTACT_METHOD,
            MissingInformationCode.TIMING_PREFERENCE,
            MissingInformationCode.SERVICE_LOCATION,
            MissingInformationCode.ACCESS_CONSTRAINTS,
            MissingInformationCode.INSTALLATION_TARGET,
            MissingInformationCode.INSTALLATION_SCOPE,
        ),
    ),
    RequiredInformationRule(
        category=ServiceCategory.REPAIR,
        required_codes=(
            MissingInformationCode.CONTACT_METHOD,
            MissingInformationCode.TIMING_PREFERENCE,
            MissingInformationCode.SERVICE_LOCATION,
            MissingInformationCode.ACCESS_CONSTRAINTS,
            MissingInformationCode.REPAIR_SYMPTOMS,
            MissingInformationCode.REPAIR_ASSET_CONTEXT,
        ),
    ),
    RequiredInformationRule(
        category=ServiceCategory.ROUTINE_MAINTENANCE,
        required_codes=(
            MissingInformationCode.CONTACT_METHOD,
            MissingInformationCode.TIMING_PREFERENCE,
            MissingInformationCode.SERVICE_LOCATION,
            MissingInformationCode.ACCESS_CONSTRAINTS,
            MissingInformationCode.MAINTENANCE_ASSET_CONTEXT,
        ),
    ),
    RequiredInformationRule(
        category=ServiceCategory.INSPECTION,
        required_codes=(
            MissingInformationCode.CONTACT_METHOD,
            MissingInformationCode.TIMING_PREFERENCE,
            MissingInformationCode.SERVICE_LOCATION,
            MissingInformationCode.ACCESS_CONSTRAINTS,
            MissingInformationCode.INSPECTION_SUBJECT,
            MissingInformationCode.INSPECTION_PURPOSE,
        ),
    ),
    RequiredInformationRule(
        category=ServiceCategory.OTHER_CUSTOM_REQUEST,
        required_codes=(
            MissingInformationCode.CONTACT_METHOD,
            MissingInformationCode.TIMING_PREFERENCE,
            MissingInformationCode.SERVICE_LOCATION,
            MissingInformationCode.CUSTOM_SCOPE,
            MissingInformationCode.DESIRED_OUTCOME,
            MissingInformationCode.CUSTOM_SCOPE_CONFIRMATION,
        ),
    ),
)

DUPLICATE_WEIGHTS = (
    DuplicateWeightRule(reason_code=DuplicateReasonCode.EXACT_EMAIL, weight=70),
    DuplicateWeightRule(reason_code=DuplicateReasonCode.EXACT_PHONE, weight=70),
    DuplicateWeightRule(reason_code=DuplicateReasonCode.EXISTING_CONTACT, weight=65),
    DuplicateWeightRule(reason_code=DuplicateReasonCode.EXACT_DESCRIPTION, weight=45),
    DuplicateWeightRule(reason_code=DuplicateReasonCode.DESCRIPTION_SIMILARITY, weight=30),
    DuplicateWeightRule(reason_code=DuplicateReasonCode.CATEGORY_MATCH, weight=10),
    DuplicateWeightRule(reason_code=DuplicateReasonCode.LOCATION_MATCH, weight=10),
    DuplicateWeightRule(reason_code=DuplicateReasonCode.TIMING_PROXIMITY, weight=5),
)

REVIEW_PRECEDENCE_GROUPS = (
    (ReviewReasonCode.ROUTING_EVIDENCE_UNAVAILABLE,),
    (ReviewReasonCode.POSSIBLE_DUPLICATE,),
    (ReviewReasonCode.URGENT_PRIORITY,),
    (ReviewReasonCode.REPORTED_SAFETY_OR_CONTINUITY,),
    (ReviewReasonCode.MISSING_REQUIRED_INFORMATION,),
    (
        ReviewReasonCode.LOW_AI_CONFIDENCE,
        ReviewReasonCode.AI_POSSIBLE_SAFETY_OR_CONTINUITY,
        ReviewReasonCode.AI_MISSING_INFORMATION_CONFLICT,
    ),
    (
        ReviewReasonCode.CATEGORY_AMBIGUITY,
        ReviewReasonCode.CATEGORY_CONFLICT,
        ReviewReasonCode.OTHER_CUSTOM_SCOPE,
    ),
)

DEMO_POLICY_CONTENT = DecisionPolicyContent(
    categories=CATEGORY_CATALOG,
    category_resolution_order=(
        "ReviewedCorrection",
        "ExplicitSelection",
        "SingleNormalizedEvidenceSet",
        "ConflictOrMultiplePlausible",
        "NoUsableEvidence",
        "UnconfirmedOtherCustomScope",
    ),
    required_information_rules=REQUIRED_INFORMATION_RULES,
    priority_precedence=(Priority.URGENT, Priority.HIGH, Priority.LOW, Priority.NORMAL),
    thresholds=DecisionPolicyThresholds(
        ai_confidence_review=Decimal("0.75"),
        duplicate_lookback_days=90,
        duplicate_retention_score=40,
        duplicate_review_score=60,
        description_similarity=Decimal("0.80"),
        duplicate_timing_days=14,
        urgent_deadline_hours=24,
        high_deadline_hours=72,
        low_flexible_days=21,
    ),
    duplicate_weights=DUPLICATE_WEIGHTS,
    review_precedence_groups=REVIEW_PRECEDENCE_GROUPS,
    queue_mapping=(
        QueueMappingRule(
            status=RequestStatus.DUPLICATE_REVIEW,
            priority=None,
            queue=OperationalQueue.DUPLICATE_REVIEW,
        ),
        QueueMappingRule(
            status=RequestStatus.HUMAN_REVIEW,
            priority=None,
            queue=OperationalQueue.HUMAN_REVIEW,
        ),
        QueueMappingRule(
            status=RequestStatus.READY_FOR_ACTION,
            priority=Priority.URGENT,
            queue=OperationalQueue.HUMAN_REVIEW,
        ),
        QueueMappingRule(
            status=RequestStatus.READY_FOR_ACTION,
            priority=Priority.HIGH,
            queue=OperationalQueue.PRIORITY_REQUESTS,
        ),
        QueueMappingRule(
            status=RequestStatus.READY_FOR_ACTION,
            priority=Priority.LOW,
            queue=OperationalQueue.STANDARD_REQUESTS,
        ),
        QueueMappingRule(
            status=RequestStatus.READY_FOR_ACTION,
            priority=Priority.NORMAL,
            queue=OperationalQueue.STANDARD_REQUESTS,
        ),
    ),
    category_reason_catalog=CATEGORY_REASON_CATALOG,
    missing_information_catalog=MISSING_INFORMATION_CATALOG,
    priority_reason_catalog=PRIORITY_REASON_CATALOG,
    duplicate_reason_catalog=DUPLICATE_REASON_CATALOG,
    review_reason_catalog=REVIEW_REASON_CATALOG,
)


def _canonical_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _canonical_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


def canonical_policy_bytes(content: DecisionPolicyContent) -> bytes:
    """Serialize a policy snapshot identically for migration and runtime checks."""
    material = _canonical_value(content.model_dump(mode="python"))
    return json.dumps(material, sort_keys=True, separators=(",", ":")).encode()


def policy_content_digest(content: DecisionPolicyContent) -> str:
    return hashlib.sha256(canonical_policy_bytes(content)).hexdigest()


DEMO_POLICY_CONTENT_DIGEST = policy_content_digest(DEMO_POLICY_CONTENT)
DEMO_DECISION_POLICY = DecisionPolicyVersion(
    id=DEMO_POLICY_ID,
    policy_key=DEMO_POLICY_KEY,
    semantic_version=DEMO_POLICY_SEMANTIC_VERSION,
    revision=DEMO_POLICY_REVISION,
    content_digest=DEMO_POLICY_CONTENT_DIGEST,
    effective_at=DEMO_POLICY_EFFECTIVE_AT,
    status=PolicyStatus.ACTIVE,
    content=DEMO_POLICY_CONTENT,
)


def policy_identities_equal(left: DecisionPolicyIdentity, right: DecisionPolicyIdentity) -> bool:
    return left == right


def require_policy_identity(
    expected: DecisionPolicyIdentity, actual: DecisionPolicyIdentity
) -> None:
    if not policy_identities_equal(expected, actual):
        raise DecisionPolicyError(
            "POLICY_VERSION_CONFLICT",
            "the selected immutable decision policy does not match the expected identity",
        )


def _ordered[T](values: set[T], catalog: tuple[T, ...]) -> tuple[T, ...]:
    return tuple(item for item in catalog if item in values)


def _category_evidence(facts: NormalizedDecisionFacts) -> tuple[ServiceCategory, ...]:
    evidence: set[ServiceCategory] = set()
    if facts.consultation_topic_present and facts.desired_outcome_present:
        evidence.add(ServiceCategory.CONSULTATION)
    if facts.installation_target_present and facts.installation_scope_present:
        evidence.add(ServiceCategory.INSTALLATION)
    if facts.repair_symptoms_present and facts.repair_asset_context_present:
        evidence.add(ServiceCategory.REPAIR)
    if facts.maintenance_asset_context_present:
        evidence.add(ServiceCategory.ROUTINE_MAINTENANCE)
    if facts.inspection_subject_present and facts.inspection_purpose_present:
        evidence.add(ServiceCategory.INSPECTION)
    return _ordered(evidence, CATEGORY_CATALOG)


def resolve_category(
    facts: NormalizedDecisionFacts,
    ai_advisory: AIAdvisory,
    reviewed_facts: ReviewedFacts | None = None,
    policy: DecisionPolicyVersion = DEMO_DECISION_POLICY,
) -> tuple[ServiceCategory, tuple[CategoryReasonCode, ...]]:
    """Resolve category in the approved order and retain all applicable evidence codes."""
    evidence = _category_evidence(facts)
    explicit = facts.explicit_category
    reviewed_category = reviewed_facts.corrected_category if reviewed_facts else None
    conflicts_with_explicit = explicit is not None and any(
        item is not explicit for item in evidence
    )
    multiple_evidence_sets = len(evidence) > 1
    routine_basis = explicit is ServiceCategory.ROUTINE_MAINTENANCE or (
        explicit is None and evidence == (ServiceCategory.ROUTINE_MAINTENANCE,)
    )
    routine_fault_conflict = routine_basis and (
        facts.repair_symptoms_present
        or facts.damage_or_deterioration
        in {DamageOrDeterioration.ACTIVE, DamageOrDeterioration.RAPID}
    )
    normalized_conflict = conflicts_with_explicit or routine_fault_conflict
    reasons: set[CategoryReasonCode] = set()

    if reviewed_category is not None:
        category = reviewed_category
        reasons.add(CategoryReasonCode.REVIEWED_CORRECTION)
        if normalized_conflict:
            reasons.add(CategoryReasonCode.CONFLICT)
        if multiple_evidence_sets:
            reasons.add(CategoryReasonCode.MULTIPLE_PLAUSIBLE)
    elif explicit is not None and not normalized_conflict and not multiple_evidence_sets:
        category = explicit
        reasons.add(CategoryReasonCode.EXPLICIT_SELECTION_ACCEPTED)
    elif explicit is None and len(evidence) == 1 and not normalized_conflict:
        category = evidence[0]
        reasons.add(CategoryReasonCode.NORMALIZED_EVIDENCE)
    elif normalized_conflict or multiple_evidence_sets:
        category = ServiceCategory.OTHER_CUSTOM_REQUEST
        if normalized_conflict:
            reasons.add(CategoryReasonCode.CONFLICT)
        if multiple_evidence_sets:
            reasons.add(CategoryReasonCode.MULTIPLE_PLAUSIBLE)
    else:
        category = ServiceCategory.OTHER_CUSTOM_REQUEST
        reasons.add(CategoryReasonCode.EVIDENCE_UNUSABLE)

    authoritative_basis = bool(
        reviewed_category is not None
        or explicit is not None
        or CategoryReasonCode.NORMALIZED_EVIDENCE in reasons
    )
    if ai_advisory.suggested_category is not None:
        if ai_advisory.suggested_category is category and authoritative_basis:
            reasons.add(CategoryReasonCode.AI_AGREES)
        elif ai_advisory.suggested_category is not category:
            reasons.add(CategoryReasonCode.AI_CONFLICT)

    custom_scope_confirmed = bool(reviewed_facts and reviewed_facts.custom_scope_confirmed)
    if category is ServiceCategory.OTHER_CUSTOM_REQUEST and not custom_scope_confirmed:
        reasons.add(CategoryReasonCode.OTHER_CUSTOM_SCOPE)
    return category, _ordered(reasons, policy.content.category_reason_catalog)


def _effective_facts(
    facts: NormalizedDecisionFacts, reviewed: ReviewedFacts | None
) -> NormalizedDecisionFacts:
    if reviewed is None:
        return facts
    updates: dict[str, Any] = {}
    correction_map = {
        "requested_deadline": reviewed.corrected_requested_deadline,
        "timing_preference_present": reviewed.corrected_timing_preference_present,
        "timing_is_flexible": reviewed.corrected_timing_is_flexible,
        "safety_or_continuity_concern": reviewed.corrected_safety_or_continuity_concern,
        "service_interruption": reviewed.corrected_service_interruption,
        "damage_or_deterioration": reviewed.corrected_damage_or_deterioration,
        "material_impact": reviewed.corrected_material_impact,
    }
    updates.update({key: value for key, value in correction_map.items() if value is not None})
    if reviewed.corrected_requested_deadline is not None:
        updates["timing_preference_present"] = True
        updates["requested_service_date"] = reviewed.corrected_requested_deadline.astimezone(
            UTC
        ).date()
    return facts.model_copy(update=updates)


def required_information_codes(
    facts: NormalizedDecisionFacts,
    category: ServiceCategory,
    reviewed_facts: ReviewedFacts | None = None,
    policy: DecisionPolicyVersion = DEMO_DECISION_POLICY,
) -> tuple[MissingInformationCode, ...]:
    """Calculate required information from facts; advisory AI cannot satisfy a field."""
    facts = _effective_facts(facts, reviewed_facts)
    missing: set[MissingInformationCode] = set()
    if not facts.contact_method_present:
        missing.add(MissingInformationCode.CONTACT_METHOD)
    if not facts.timing_preference_present and facts.requested_deadline is None:
        missing.add(MissingInformationCode.TIMING_PREFERENCE)
    if not facts.location_or_service_context_present:
        missing.add(MissingInformationCode.SERVICE_LOCATION)
    if (
        facts.service_mode is ServiceMode.ON_SITE
        and category
        in {
            ServiceCategory.INSTALLATION,
            ServiceCategory.REPAIR,
            ServiceCategory.ROUTINE_MAINTENANCE,
            ServiceCategory.INSPECTION,
        }
        and not facts.access_constraints_known
    ):
        missing.add(MissingInformationCode.ACCESS_CONSTRAINTS)

    category_requirements = {
        ServiceCategory.CONSULTATION: (
            (facts.consultation_topic_present, MissingInformationCode.CONSULTATION_TOPIC),
            (facts.desired_outcome_present, MissingInformationCode.DESIRED_OUTCOME),
        ),
        ServiceCategory.INSTALLATION: (
            (facts.installation_target_present, MissingInformationCode.INSTALLATION_TARGET),
            (facts.installation_scope_present, MissingInformationCode.INSTALLATION_SCOPE),
        ),
        ServiceCategory.REPAIR: (
            (facts.repair_symptoms_present, MissingInformationCode.REPAIR_SYMPTOMS),
            (facts.repair_asset_context_present, MissingInformationCode.REPAIR_ASSET_CONTEXT),
        ),
        ServiceCategory.ROUTINE_MAINTENANCE: (
            (
                facts.maintenance_asset_context_present,
                MissingInformationCode.MAINTENANCE_ASSET_CONTEXT,
            ),
        ),
        ServiceCategory.INSPECTION: (
            (facts.inspection_subject_present, MissingInformationCode.INSPECTION_SUBJECT),
            (facts.inspection_purpose_present, MissingInformationCode.INSPECTION_PURPOSE),
        ),
        ServiceCategory.OTHER_CUSTOM_REQUEST: (
            (facts.custom_scope_present, MissingInformationCode.CUSTOM_SCOPE),
            (facts.desired_outcome_present, MissingInformationCode.DESIRED_OUTCOME),
            (
                bool(reviewed_facts and reviewed_facts.custom_scope_confirmed),
                MissingInformationCode.CUSTOM_SCOPE_CONFIRMATION,
            ),
        ),
    }
    missing.update(code for present, code in category_requirements[category] if not present)
    if reviewed_facts:
        missing.difference_update(reviewed_facts.resolved_missing_information_codes)
    return _ordered(missing, policy.content.missing_information_catalog)


def calculate_priority(
    facts: NormalizedDecisionFacts,
    category: ServiceCategory,
    evaluation_at: datetime,
    reviewed_facts: ReviewedFacts | None = None,
    policy: DecisionPolicyVersion = DEMO_DECISION_POLICY,
) -> tuple[Priority, tuple[PriorityReasonCode, ...]]:
    """Apply first-match priority precedence with explicit equality behavior."""
    facts = _effective_facts(facts, reviewed_facts)
    deadline_delta = (
        facts.requested_deadline - evaluation_at if facts.requested_deadline is not None else None
    )
    urgent_window = timedelta(hours=policy.content.thresholds.urgent_deadline_hours)
    high_window = timedelta(hours=policy.content.thresholds.high_deadline_hours)
    urgent: set[PriorityReasonCode] = set()
    if facts.safety_or_continuity_concern is SafetyOrContinuityConcern.CRITICAL:
        urgent.add(PriorityReasonCode.CRITICAL_SAFETY_OR_CONTINUITY)
    if deadline_delta is not None and deadline_delta <= urgent_window:
        if facts.service_interruption is ServiceInterruption.ACTIVE:
            urgent.add(PriorityReasonCode.ACTIVE_INTERRUPTION_IMMEDIATE)
        if facts.damage_or_deterioration is DamageOrDeterioration.RAPID:
            urgent.add(PriorityReasonCode.RAPID_DAMAGE_IMMEDIATE)
        if facts.material_impact is MaterialImpact.SEVERE:
            urgent.add(PriorityReasonCode.SEVERE_IMPACT_IMMEDIATE)
    if urgent:
        return Priority.URGENT, _ordered(urgent, PRIORITY_REASON_CATALOG)

    high: set[PriorityReasonCode] = set()
    if facts.service_interruption is ServiceInterruption.ACTIVE:
        high.add(PriorityReasonCode.ACTIVE_INTERRUPTION)
    if facts.damage_or_deterioration in {
        DamageOrDeterioration.ACTIVE,
        DamageOrDeterioration.RAPID,
    }:
        high.add(PriorityReasonCode.ACTIVE_DAMAGE_OR_DETERIORATION)
    if facts.material_impact in {MaterialImpact.MAJOR, MaterialImpact.SEVERE}:
        high.add(PriorityReasonCode.MAJOR_OR_SEVERE_IMPACT)
    if deadline_delta is not None and deadline_delta <= high_window:
        high.add(PriorityReasonCode.NEAR_TERM_DEADLINE)
    if high:
        return Priority.HIGH, _ordered(high, PRIORITY_REASON_CATALOG)

    safe_for_low = (
        facts.safety_or_continuity_concern is SafetyOrContinuityConcern.NONE
        and facts.service_interruption is ServiceInterruption.NONE
        and facts.damage_or_deterioration is DamageOrDeterioration.NONE
        and facts.material_impact in {MaterialImpact.NONE, MaterialImpact.MINOR}
    )
    flexible_without_deadline = facts.timing_is_flexible and deadline_delta is None
    flexible_routine_with_later_deadline = (
        facts.timing_is_flexible
        and category in {ServiceCategory.ROUTINE_MAINTENANCE, ServiceCategory.INSPECTION}
        and deadline_delta is not None
        and deadline_delta >= timedelta(days=policy.content.thresholds.low_flexible_days)
    )
    if safe_for_low and (flexible_without_deadline or flexible_routine_with_later_deadline):
        return Priority.LOW, (PriorityReasonCode.FLEXIBLE_ROUTINE_WORK,)
    return Priority.NORMAL, (PriorityReasonCode.DEFAULT_NORMAL,)


def jaccard_similarity(left: tuple[str, ...], right: tuple[str, ...]) -> Decimal:
    left_set = set(left)
    right_set = set(right)
    union = left_set | right_set
    if not union:
        return Decimal("0")
    return Decimal(len(left_set & right_set)) / Decimal(len(union))


def _requested_date(facts: NormalizedDecisionFacts) -> date | None:
    if facts.requested_service_date is not None:
        return facts.requested_service_date
    if facts.requested_deadline is not None:
        return facts.requested_deadline.astimezone(UTC).date()
    return None


def _candidate_is_eligible(
    source_request_id: uuid.UUID,
    candidate: DuplicateCandidateInput,
    evaluation_at: datetime,
    lookback_days: int,
) -> bool:
    if not candidate.eligible_record:
        return False
    if (
        candidate.candidate_kind is CandidateKind.SERVICE_REQUEST
        and candidate.candidate_id == source_request_id
    ):
        return False
    age = evaluation_at - candidate.candidate_activity_at
    return timedelta(0) <= age <= timedelta(days=lookback_days)


def score_duplicate_candidate(
    facts: NormalizedDecisionFacts,
    final_category: ServiceCategory,
    candidate: DuplicateCandidateInput,
    policy: DecisionPolicyVersion,
) -> DuplicateCandidateResult:
    reasons: set[DuplicateReasonCode] = set()
    if (
        facts.normalized_email_digest is not None
        and facts.normalized_email_digest == candidate.normalized_email_digest
    ):
        reasons.add(DuplicateReasonCode.EXACT_EMAIL)
    if (
        facts.normalized_phone_digest is not None
        and facts.normalized_phone_digest == candidate.normalized_phone_digest
    ):
        reasons.add(DuplicateReasonCode.EXACT_PHONE)
    if facts.contact_id is not None and facts.contact_id == candidate.contact_id:
        reasons.add(DuplicateReasonCode.EXISTING_CONTACT)
    similarity = jaccard_similarity(
        facts.description_token_digests, candidate.description_token_digests
    )
    exact_description = (
        facts.description_fingerprint is not None
        and facts.description_fingerprint == candidate.description_fingerprint
    )
    if exact_description:
        reasons.add(DuplicateReasonCode.EXACT_DESCRIPTION)
    elif similarity >= policy.content.thresholds.description_similarity:
        reasons.add(DuplicateReasonCode.DESCRIPTION_SIMILARITY)
    if candidate.final_category is final_category:
        reasons.add(DuplicateReasonCode.CATEGORY_MATCH)
    if (
        facts.location_or_service_context_digest is not None
        and facts.location_or_service_context_digest == candidate.location_or_service_context_digest
    ):
        reasons.add(DuplicateReasonCode.LOCATION_MATCH)
    source_date = _requested_date(facts)
    if (
        source_date is not None
        and candidate.requested_service_date is not None
        and abs((source_date - candidate.requested_service_date).days)
        <= policy.content.thresholds.duplicate_timing_days
    ):
        reasons.add(DuplicateReasonCode.TIMING_PROXIMITY)

    weights = {rule.reason_code: rule.weight for rule in policy.content.duplicate_weights}
    score = min(100, sum(weights[reason] for reason in reasons))
    return DuplicateCandidateResult(
        observation_id=candidate.observation_id,
        candidate_kind=candidate.candidate_kind,
        candidate_id=candidate.candidate_id,
        candidate_activity_at=candidate.candidate_activity_at,
        candidate_evidence_hash=candidate.candidate_evidence_hash,
        disposition=candidate.disposition,
        score=score,
        description_similarity=similarity,
        reason_codes=_ordered(reasons, policy.content.duplicate_reason_catalog),
    )


def score_duplicate_candidates(
    decision_input: DecisionEvaluationInput,
    final_category: ServiceCategory,
    policy: DecisionPolicyVersion = DEMO_DECISION_POLICY,
) -> tuple[DuplicateCandidateResult, ...]:
    """Retain material candidates and return their exact deterministic ordering."""
    facts = _effective_facts(decision_input.normalized_facts, decision_input.reviewed_facts)
    results = (
        score_duplicate_candidate(facts, final_category, candidate, policy)
        for candidate in decision_input.duplicate_candidates
        if _candidate_is_eligible(
            facts.source_request_id,
            candidate,
            decision_input.evaluation_at,
            policy.content.thresholds.duplicate_lookback_days,
        )
    )
    retained = [
        result
        for result in results
        if result.score >= policy.content.thresholds.duplicate_retention_score
    ]
    return tuple(
        sorted(
            retained,
            key=lambda item: (
                -item.score,
                -item.candidate_activity_at.timestamp(),
                item.candidate_id.int,
                item.candidate_kind.value,
            ),
        )
    )


def decision_input_material(decision_input: DecisionEvaluationInput) -> dict[str, Any]:
    """Return only allowlisted, policy-independent evidence used by calculation."""
    candidates = sorted(
        decision_input.duplicate_candidates,
        key=lambda item: (
            item.candidate_kind.value,
            item.candidate_id.int,
            item.candidate_evidence_hash,
            item.observation_id.int if item.observation_id else -1,
        ),
    )
    reviewed = decision_input.reviewed_facts
    reviewed_material = None
    if reviewed is not None:
        reviewed_material = reviewed.model_dump(
            mode="python",
            exclude={"rationale_reference", "supporting_evidence_references"},
        )
    return {
        "evaluation_at": decision_input.evaluation_at,
        "normalized_facts": decision_input.normalized_facts.model_dump(mode="python"),
        "interpretation": {
            "id": decision_input.interpretation_id,
            "version": decision_input.interpretation_version,
            "evidence_hash": decision_input.interpretation_evidence_hash,
            "advisory": decision_input.ai_advisory.model_dump(mode="python"),
        },
        "duplicate_evidence": [candidate.model_dump(mode="python") for candidate in candidates],
        "reviewed_fact_set_ids": sorted(
            decision_input.reviewed_fact_set_ids, key=lambda item: item.int
        ),
        "current_reviewed_facts": reviewed_material,
        "routing_evidence_usable": decision_input.routing_evidence_usable,
        "source": decision_input.source,
        "current_priority": decision_input.current_priority,
    }


def canonical_decision_input_bytes(decision_input: DecisionEvaluationInput) -> bytes:
    material = _canonical_value(decision_input_material(decision_input))
    return json.dumps(material, sort_keys=True, separators=(",", ":")).encode()


def canonical_decision_input_hash(decision_input: DecisionEvaluationInput) -> str:
    return hashlib.sha256(canonical_decision_input_bytes(decision_input)).hexdigest()


def _category_review_codes(
    category_reasons: tuple[CategoryReasonCode, ...],
    reviewed: ReviewedFacts | None,
    policy: DecisionPolicyVersion,
) -> tuple[ReviewReasonCode, ...]:
    values: set[ReviewReasonCode] = set()
    reviewed_category_resolves_conflict = bool(reviewed and reviewed.corrected_category is not None)
    if not reviewed_category_resolves_conflict:
        if (
            CategoryReasonCode.MULTIPLE_PLAUSIBLE in category_reasons
            or CategoryReasonCode.EVIDENCE_UNUSABLE in category_reasons
        ):
            values.add(ReviewReasonCode.CATEGORY_AMBIGUITY)
        if (
            CategoryReasonCode.CONFLICT in category_reasons
            or CategoryReasonCode.AI_CONFLICT in category_reasons
        ):
            values.add(ReviewReasonCode.CATEGORY_CONFLICT)
    if CategoryReasonCode.OTHER_CUSTOM_SCOPE in category_reasons:
        values.add(ReviewReasonCode.OTHER_CUSTOM_SCOPE)
    return _ordered(values, policy.content.review_reason_catalog)


def _mapped_queue(
    status: RequestStatus,
    priority: Priority,
    policy: DecisionPolicyVersion,
) -> OperationalQueue:
    matches = tuple(
        rule.queue
        for rule in policy.content.queue_mapping
        if rule.status is status and (rule.priority is None or rule.priority is priority)
    )
    if len(matches) != 1:
        raise DecisionPolicyError(
            "POLICY_QUEUE_MAPPING_CONFLICT",
            "the immutable policy must define exactly one queue for the calculated outcome",
        )
    return matches[0]


def _select_review_outcome(
    decision_input: DecisionEvaluationInput,
    priority: Priority,
    missing: tuple[MissingInformationCode, ...],
    category_reasons: tuple[CategoryReasonCode, ...],
    candidates: tuple[DuplicateCandidateResult, ...],
    effective_facts: NormalizedDecisionFacts,
    policy: DecisionPolicyVersion,
) -> tuple[RequestStatus, OperationalQueue, tuple[ReviewReasonCode, ...]]:
    if not decision_input.routing_evidence_usable:
        return (
            RequestStatus.HUMAN_REVIEW,
            _mapped_queue(RequestStatus.HUMAN_REVIEW, priority, policy),
            (ReviewReasonCode.ROUTING_EVIDENCE_UNAVAILABLE,),
        )
    if any(
        candidate.disposition is CandidateDisposition.PENDING
        and candidate.score >= policy.content.thresholds.duplicate_review_score
        for candidate in candidates
    ):
        return (
            RequestStatus.DUPLICATE_REVIEW,
            _mapped_queue(RequestStatus.DUPLICATE_REVIEW, priority, policy),
            (ReviewReasonCode.POSSIBLE_DUPLICATE,),
        )
    urgent_confirmed = bool(
        decision_input.reviewed_facts
        and decision_input.reviewed_facts.urgent_review_disposition
        is UrgentReviewDisposition.CONFIRMED_AND_ACTIONABLE
    )
    if priority is Priority.URGENT and not urgent_confirmed:
        return (
            RequestStatus.HUMAN_REVIEW,
            _mapped_queue(RequestStatus.HUMAN_REVIEW, priority, policy),
            (ReviewReasonCode.URGENT_PRIORITY,),
        )
    if effective_facts.safety_or_continuity_concern is SafetyOrContinuityConcern.REPORTED:
        return (
            RequestStatus.HUMAN_REVIEW,
            _mapped_queue(RequestStatus.HUMAN_REVIEW, priority, policy),
            (ReviewReasonCode.REPORTED_SAFETY_OR_CONTINUITY,),
        )
    if missing:
        return (
            RequestStatus.HUMAN_REVIEW,
            _mapped_queue(RequestStatus.HUMAN_REVIEW, priority, policy),
            (ReviewReasonCode.MISSING_REQUIRED_INFORMATION,),
        )

    ai_codes: set[ReviewReasonCode] = set()
    if decision_input.ai_advisory.confidence < policy.content.thresholds.ai_confidence_review:
        ai_codes.add(ReviewReasonCode.LOW_AI_CONFIDENCE)
    if decision_input.ai_advisory.possible_safety_or_continuity:
        ai_codes.add(ReviewReasonCode.AI_POSSIBLE_SAFETY_OR_CONTINUITY)
    if any(code not in missing for code in decision_input.ai_advisory.missing_information_codes):
        ai_codes.add(ReviewReasonCode.AI_MISSING_INFORMATION_CONFLICT)
    if ai_codes:
        return (
            RequestStatus.HUMAN_REVIEW,
            _mapped_queue(RequestStatus.HUMAN_REVIEW, priority, policy),
            _ordered(ai_codes, policy.content.review_reason_catalog),
        )

    category_review = _category_review_codes(
        category_reasons, decision_input.reviewed_facts, policy
    )
    if category_review:
        return (
            RequestStatus.HUMAN_REVIEW,
            _mapped_queue(RequestStatus.HUMAN_REVIEW, priority, policy),
            category_review,
        )
    if priority is Priority.HIGH:
        return (
            RequestStatus.READY_FOR_ACTION,
            _mapped_queue(RequestStatus.READY_FOR_ACTION, priority, policy),
            (),
        )
    if priority is Priority.URGENT and urgent_confirmed:
        return (
            RequestStatus.READY_FOR_ACTION,
            _mapped_queue(RequestStatus.READY_FOR_ACTION, priority, policy),
            (),
        )
    return (
        RequestStatus.READY_FOR_ACTION,
        _mapped_queue(RequestStatus.READY_FOR_ACTION, priority, policy),
        (),
    )


def _hard_safety_reduction(facts: NormalizedDecisionFacts, reviewed: ReviewedFacts | None) -> bool:
    return bool(
        reviewed
        and facts.safety_or_continuity_concern is SafetyOrContinuityConcern.CRITICAL
        and reviewed.corrected_safety_or_continuity_concern
        not in {None, SafetyOrContinuityConcern.CRITICAL}
    )


def evaluate_decision(
    decision_input: DecisionEvaluationInput,
    policy: DecisionPolicyVersion = DEMO_DECISION_POLICY,
) -> DecisionEvaluation:
    """Calculate one complete immutable routing result without I/O or clock reads."""
    category, category_reasons = resolve_category(
        decision_input.normalized_facts,
        decision_input.ai_advisory,
        decision_input.reviewed_facts,
        policy,
    )
    effective_facts = _effective_facts(
        decision_input.normalized_facts, decision_input.reviewed_facts
    )
    missing = required_information_codes(
        decision_input.normalized_facts,
        category,
        decision_input.reviewed_facts,
        policy,
    )
    priority, priority_reasons = calculate_priority(
        decision_input.normalized_facts,
        category,
        decision_input.evaluation_at,
        decision_input.reviewed_facts,
        policy,
    )
    if (
        decision_input.reviewed_facts
        and decision_input.reviewed_facts.urgent_review_disposition is not None
        and priority is not Priority.URGENT
        and decision_input.current_priority is not Priority.URGENT
    ):
        raise DecisionPolicyError(
            "URGENT_REVIEW_DISPOSITION_INVALID",
            "an urgent review disposition requires current or recalculated Urgent priority",
        )
    candidates = score_duplicate_candidates(decision_input, category, policy)
    status, queue, review_reasons = _select_review_outcome(
        decision_input,
        priority,
        missing,
        category_reasons,
        candidates,
        effective_facts,
        policy,
    )
    manager_required = bool(
        priority is Priority.URGENT
        or decision_input.current_priority is Priority.URGENT
        or _hard_safety_reduction(decision_input.normalized_facts, decision_input.reviewed_facts)
    )
    return DecisionEvaluation(
        policy=policy.identity,
        evaluation_at=decision_input.evaluation_at,
        canonical_input_hash=canonical_decision_input_hash(decision_input),
        source=decision_input.source,
        final_category=category,
        final_priority=priority,
        final_status=status,
        final_queue=queue,
        review_required=bool(review_reasons),
        category_reason_codes=category_reasons,
        priority_reason_codes=priority_reasons,
        missing_information_codes=missing,
        review_reason_codes=review_reasons,
        duplicate_candidates=candidates,
        requires_manager_or_administrator=manager_required,
    )
