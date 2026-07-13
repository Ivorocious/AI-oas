import uuid
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from ai_operations_automation.deterministic_decision import (
    DEMO_DECISION_POLICY,
    DEMO_POLICY_CONTENT,
    DEMO_POLICY_CONTENT_DIGEST,
    DEMO_POLICY_EFFECTIVE_AT,
    DEMO_POLICY_ID,
    DEMO_POLICY_KEY,
    DEMO_POLICY_REVISION,
    DEMO_POLICY_SEMANTIC_VERSION,
    AIAdvisory,
    CandidateDisposition,
    CandidateKind,
    CategoryReasonCode,
    DamageOrDeterioration,
    DecisionEvaluationInput,
    DecisionPolicyError,
    DecisionSource,
    DuplicateCandidateInput,
    DuplicateReasonCode,
    MaterialImpact,
    MissingInformationCode,
    NormalizedDecisionFacts,
    OperationalQueue,
    Priority,
    PriorityReasonCode,
    RequestStatus,
    ReviewedFacts,
    ReviewReasonCode,
    SafetyOrContinuityConcern,
    ServiceCategory,
    ServiceInterruption,
    ServiceMode,
    UrgentReviewDisposition,
    calculate_priority,
    canonical_decision_input_bytes,
    canonical_decision_input_hash,
    canonical_policy_bytes,
    evaluate_decision,
    jaccard_similarity,
    policy_content_digest,
    policy_identities_equal,
    require_policy_identity,
    required_information_codes,
    resolve_category,
    score_duplicate_candidate,
    score_duplicate_candidates,
)

NOW = datetime(2026, 7, 13, 12, tzinfo=UTC)
SOURCE_REQUEST_ID = uuid.UUID("10000000-0000-4000-8000-000000000001")
INTERPRETATION_ID = uuid.UUID("20000000-0000-4000-8000-000000000001")
FACT_SET_ID = uuid.UUID("30000000-0000-4000-8000-000000000001")
HEX = {index: f"{index:064x}" for index in range(1, 40)}


def facts_for(category: ServiceCategory = ServiceCategory.REPAIR, **changes):
    values = {
        "source_request_id": SOURCE_REQUEST_ID,
        "explicit_category": category,
        "contact_method_present": True,
        "timing_preference_present": True,
        "requested_deadline": NOW + timedelta(days=10),
        "requested_service_date": (NOW + timedelta(days=10)).date(),
        "service_mode": ServiceMode.ON_SITE,
        "location_or_service_context_present": True,
        "access_constraints_known": True,
        "normalized_email_digest": HEX[1],
        "normalized_phone_digest": HEX[2],
        "description_fingerprint": HEX[3],
        "description_token_digests": (HEX[4], HEX[5], HEX[6], HEX[7]),
        "location_or_service_context_digest": HEX[8],
    }
    category_values = {
        ServiceCategory.CONSULTATION: {
            "consultation_topic_present": True,
            "desired_outcome_present": True,
            "service_mode": ServiceMode.REMOTE,
        },
        ServiceCategory.INSTALLATION: {
            "installation_target_present": True,
            "installation_scope_present": True,
        },
        ServiceCategory.REPAIR: {
            "repair_symptoms_present": True,
            "repair_asset_context_present": True,
        },
        ServiceCategory.ROUTINE_MAINTENANCE: {
            "maintenance_asset_context_present": True,
        },
        ServiceCategory.INSPECTION: {
            "inspection_subject_present": True,
            "inspection_purpose_present": True,
        },
        ServiceCategory.OTHER_CUSTOM_REQUEST: {
            "custom_scope_present": True,
            "desired_outcome_present": True,
        },
    }
    values.update(category_values[category])
    values.update(changes)
    return NormalizedDecisionFacts.model_validate(values)


def reviewed(**changes) -> ReviewedFacts:
    values = {
        "fact_set_id": FACT_SET_ID,
        "resolved_missing_information_codes": (MissingInformationCode.SERVICE_LOCATION,),
        "rationale_reference": "review-rationale:sha256:01",
        "supporting_evidence_references": ("contact-log:case-1042",),
        **changes,
    }
    return ReviewedFacts.model_validate(values)


def decision_input(
    *,
    facts: NormalizedDecisionFacts | None = None,
    ai: AIAdvisory | None = None,
    candidates: tuple[DuplicateCandidateInput, ...] = (),
    review: ReviewedFacts | None = None,
    **changes,
) -> DecisionEvaluationInput:
    values = {
        "evaluation_at": NOW,
        "normalized_facts": facts or facts_for(),
        "interpretation_id": INTERPRETATION_ID,
        "interpretation_version": 1,
        "interpretation_evidence_hash": HEX[9],
        "ai_advisory": ai or AIAdvisory(confidence=Decimal("0.90")),
        "duplicate_candidates": candidates,
        "routing_evidence_usable": True,
        "source": (
            DecisionSource.REVIEWED_FACT_RECALCULATION if review else DecisionSource.INITIAL
        ),
        "reviewed_facts": review,
        "reviewed_fact_set_ids": (review.fact_set_id,) if review else (),
        **changes,
    }
    return DecisionEvaluationInput.model_validate(values)


def candidate(
    candidate_id: int = 10,
    *,
    activity_at: datetime = NOW - timedelta(days=1),
    **changes,
) -> DuplicateCandidateInput:
    values = {
        "observation_id": uuid.UUID(int=10_000 + candidate_id),
        "candidate_kind": CandidateKind.SERVICE_REQUEST,
        "candidate_id": uuid.UUID(int=candidate_id),
        "candidate_activity_at": activity_at,
        "candidate_evidence_hash": f"{candidate_id:064x}",
        **changes,
    }
    return DuplicateCandidateInput.model_validate(values)


def test_policy_identity_snapshot_and_digest_are_exact_and_repeatable() -> None:
    assert DEMO_POLICY_ID == uuid.UUID("2ddcb753-84a9-5186-bfab-f8b27e870cab")
    assert DEMO_POLICY_KEY == "general-service-demo"
    assert DEMO_POLICY_SEMANTIC_VERSION == "1.0.0"
    assert DEMO_POLICY_REVISION == 1
    assert DEMO_POLICY_EFFECTIVE_AT == datetime(2026, 7, 11, tzinfo=UTC)
    assert DEMO_POLICY_CONTENT_DIGEST == (
        "45dd2f101bcf2a36842d942fe35a97c6103dfbeac2d4a689e4f1456fce78f41a"
    )
    assert len(canonical_policy_bytes(DEMO_POLICY_CONTENT)) == 4954
    assert policy_content_digest(DEMO_POLICY_CONTENT) == DEMO_POLICY_CONTENT_DIGEST
    assert DEMO_DECISION_POLICY.identity.content_digest == DEMO_POLICY_CONTENT_DIGEST
    assert b" " not in canonical_policy_bytes(DEMO_POLICY_CONTENT)


def test_policy_catalog_is_complete_closed_and_immutable() -> None:
    assert DEMO_POLICY_CONTENT.categories == tuple(ServiceCategory)
    assert DEMO_POLICY_CONTENT.category_reason_catalog == tuple(CategoryReasonCode)
    assert DEMO_POLICY_CONTENT.missing_information_catalog == tuple(MissingInformationCode)
    assert DEMO_POLICY_CONTENT.priority_reason_catalog == tuple(PriorityReasonCode)
    assert DEMO_POLICY_CONTENT.duplicate_reason_catalog == tuple(DuplicateReasonCode)
    assert DEMO_POLICY_CONTENT.review_reason_catalog == tuple(ReviewReasonCode)
    assert len(DEMO_POLICY_CONTENT.required_information_rules) == 6
    with pytest.raises(ValidationError):
        DEMO_DECISION_POLICY.revision = 2
    with pytest.raises(ValidationError):
        DecisionEvaluationInput.model_validate(
            {**decision_input().model_dump(), "final_priority": "Urgent"}
        )


def test_policy_identity_equality_requires_every_exact_field() -> None:
    identity = DEMO_DECISION_POLICY.identity
    assert policy_identities_equal(identity, identity.model_copy())
    require_policy_identity(identity, identity.model_copy())
    changed = identity.model_copy(update={"content_digest": "0" * 64})
    assert not policy_identities_equal(identity, changed)
    with pytest.raises(DecisionPolicyError) as captured:
        require_policy_identity(identity, changed)
    assert captured.value.code == "POLICY_VERSION_CONFLICT"


def test_category_resolution_accepts_explicit_selection_before_matching_normalized_evidence() -> (
    None
):
    category, reasons = resolve_category(
        facts_for(), AIAdvisory(confidence=Decimal("0.90"), suggested_category="Repair")
    )
    assert category is ServiceCategory.REPAIR
    assert reasons == (
        CategoryReasonCode.EXPLICIT_SELECTION_ACCEPTED,
        CategoryReasonCode.AI_AGREES,
    )


def test_category_resolution_uses_one_normalized_fact_set_without_explicit_selection() -> None:
    category, reasons = resolve_category(
        facts_for(explicit_category=None), AIAdvisory(confidence=Decimal("0.90"))
    )
    assert category is ServiceCategory.REPAIR
    assert reasons == (CategoryReasonCode.NORMALIZED_EVIDENCE,)


def test_category_resolution_explicit_conflict_falls_back_to_other() -> None:
    category, reasons = resolve_category(
        facts_for(
            ServiceCategory.INSTALLATION,
            repair_symptoms_present=True,
            repair_asset_context_present=True,
        ),
        AIAdvisory(confidence=Decimal("0.90")),
    )
    assert category is ServiceCategory.OTHER_CUSTOM_REQUEST
    assert reasons == (
        CategoryReasonCode.CONFLICT,
        CategoryReasonCode.MULTIPLE_PLAUSIBLE,
        CategoryReasonCode.OTHER_CUSTOM_SCOPE,
    )


def test_multiple_normalized_sets_without_explicit_category_are_ambiguous() -> None:
    category, reasons = resolve_category(
        facts_for(
            explicit_category=None,
            inspection_subject_present=True,
            inspection_purpose_present=True,
        ),
        AIAdvisory(confidence=Decimal("0.90")),
    )
    assert category is ServiceCategory.OTHER_CUSTOM_REQUEST
    assert CategoryReasonCode.MULTIPLE_PLAUSIBLE in reasons
    assert CategoryReasonCode.OTHER_CUSTOM_SCOPE in reasons


@pytest.mark.parametrize("explicit", [ServiceCategory.ROUTINE_MAINTENANCE, None])
def test_active_damage_conflicts_with_a_purely_routine_classification(explicit) -> None:
    maintenance = facts_for(
        ServiceCategory.ROUTINE_MAINTENANCE,
        explicit_category=explicit,
        damage_or_deterioration=DamageOrDeterioration.ACTIVE,
    )
    category, reasons = resolve_category(maintenance, AIAdvisory(confidence=Decimal("0.90")))
    assert category is ServiceCategory.OTHER_CUSTOM_REQUEST
    assert CategoryReasonCode.CONFLICT in reasons


def test_ai_suggestion_alone_cannot_select_a_category() -> None:
    empty = NormalizedDecisionFacts(source_request_id=SOURCE_REQUEST_ID)
    category, reasons = resolve_category(
        empty,
        AIAdvisory(confidence=Decimal("0.90"), suggested_category=ServiceCategory.INSPECTION),
    )
    assert category is ServiceCategory.OTHER_CUSTOM_REQUEST
    assert reasons == (
        CategoryReasonCode.AI_CONFLICT,
        CategoryReasonCode.EVIDENCE_UNUSABLE,
        CategoryReasonCode.OTHER_CUSTOM_SCOPE,
    )


def test_reviewed_category_wins_and_retains_prior_conflict_evidence_without_reopening_it() -> None:
    conflicting = facts_for(
        ServiceCategory.INSTALLATION,
        repair_symptoms_present=True,
        repair_asset_context_present=True,
    )
    review = reviewed(corrected_category=ServiceCategory.REPAIR)
    result = evaluate_decision(decision_input(facts=conflicting, review=review))
    assert result.final_category is ServiceCategory.REPAIR
    assert result.category_reason_codes[:2] == (
        CategoryReasonCode.REVIEWED_CORRECTION,
        CategoryReasonCode.CONFLICT,
    )
    assert ReviewReasonCode.CATEGORY_CONFLICT not in result.review_reason_codes


@pytest.mark.parametrize(
    ("category", "field", "missing_code"),
    [
        (
            ServiceCategory.CONSULTATION,
            "consultation_topic_present",
            MissingInformationCode.CONSULTATION_TOPIC,
        ),
        (
            ServiceCategory.CONSULTATION,
            "desired_outcome_present",
            MissingInformationCode.DESIRED_OUTCOME,
        ),
        (
            ServiceCategory.INSTALLATION,
            "installation_target_present",
            MissingInformationCode.INSTALLATION_TARGET,
        ),
        (
            ServiceCategory.INSTALLATION,
            "installation_scope_present",
            MissingInformationCode.INSTALLATION_SCOPE,
        ),
        (ServiceCategory.REPAIR, "repair_symptoms_present", MissingInformationCode.REPAIR_SYMPTOMS),
        (
            ServiceCategory.REPAIR,
            "repair_asset_context_present",
            MissingInformationCode.REPAIR_ASSET_CONTEXT,
        ),
        (
            ServiceCategory.ROUTINE_MAINTENANCE,
            "maintenance_asset_context_present",
            MissingInformationCode.MAINTENANCE_ASSET_CONTEXT,
        ),
        (
            ServiceCategory.INSPECTION,
            "inspection_subject_present",
            MissingInformationCode.INSPECTION_SUBJECT,
        ),
        (
            ServiceCategory.INSPECTION,
            "inspection_purpose_present",
            MissingInformationCode.INSPECTION_PURPOSE,
        ),
        (
            ServiceCategory.OTHER_CUSTOM_REQUEST,
            "custom_scope_present",
            MissingInformationCode.CUSTOM_SCOPE,
        ),
        (
            ServiceCategory.OTHER_CUSTOM_REQUEST,
            "desired_outcome_present",
            MissingInformationCode.DESIRED_OUTCOME,
        ),
    ],
)
def test_every_category_specific_required_information_cell(category, field, missing_code) -> None:
    review = (
        reviewed(custom_scope_confirmed=True)
        if category is ServiceCategory.OTHER_CUSTOM_REQUEST
        else None
    )
    complete = facts_for(category)
    assert missing_code not in required_information_codes(complete, category, review)
    incomplete = complete.model_copy(update={field: False})
    assert missing_code in required_information_codes(incomplete, category, review)


def test_common_required_information_and_on_site_access_are_calculated_in_catalog_order() -> None:
    incomplete = facts_for(
        contact_method_present=False,
        timing_preference_present=False,
        requested_deadline=None,
        location_or_service_context_present=False,
        access_constraints_known=False,
    )
    assert required_information_codes(incomplete, ServiceCategory.REPAIR)[:4] == (
        MissingInformationCode.CONTACT_METHOD,
        MissingInformationCode.TIMING_PREFERENCE,
        MissingInformationCode.SERVICE_LOCATION,
        MissingInformationCode.ACCESS_CONSTRAINTS,
    )
    remote = incomplete.model_copy(
        update={"service_mode": ServiceMode.REMOTE, "location_or_service_context_present": True}
    )
    assert MissingInformationCode.ACCESS_CONSTRAINTS not in required_information_codes(
        remote, ServiceCategory.REPAIR
    )


def test_custom_scope_confirmation_is_required_and_must_be_reviewed() -> None:
    custom = facts_for(ServiceCategory.OTHER_CUSTOM_REQUEST)
    assert required_information_codes(custom, ServiceCategory.OTHER_CUSTOM_REQUEST) == (
        MissingInformationCode.CUSTOM_SCOPE_CONFIRMATION,
    )
    confirmation = reviewed(custom_scope_confirmed=True)
    assert (
        required_information_codes(custom, ServiceCategory.OTHER_CUSTOM_REQUEST, confirmation) == ()
    )


def test_other_custom_request_does_not_invent_an_access_constraint_requirement() -> None:
    custom = facts_for(
        ServiceCategory.OTHER_CUSTOM_REQUEST,
        service_mode=ServiceMode.ON_SITE,
        access_constraints_known=False,
    )
    missing = required_information_codes(
        custom,
        ServiceCategory.OTHER_CUSTOM_REQUEST,
        reviewed(custom_scope_confirmed=True),
    )
    assert MissingInformationCode.ACCESS_CONSTRAINTS not in missing


def test_reviewed_resolution_can_satisfy_a_missing_reference_but_ai_cannot() -> None:
    missing_location = facts_for(location_or_service_context_present=False)
    assert MissingInformationCode.SERVICE_LOCATION in required_information_codes(
        missing_location, ServiceCategory.REPAIR
    )
    assert MissingInformationCode.SERVICE_LOCATION not in required_information_codes(
        missing_location,
        ServiceCategory.REPAIR,
        reviewed(resolved_missing_information_codes=(MissingInformationCode.SERVICE_LOCATION,)),
    )
    result = evaluate_decision(
        decision_input(
            facts=missing_location,
            ai=AIAdvisory(
                confidence=Decimal("0.90"),
                missing_information_codes=(MissingInformationCode.REPAIR_SYMPTOMS,),
            ),
        )
    )
    assert MissingInformationCode.SERVICE_LOCATION in result.missing_information_codes


@pytest.mark.parametrize(
    ("delta", "interruption", "expected"),
    [
        (timedelta(hours=24, microseconds=-1), ServiceInterruption.ACTIVE, Priority.URGENT),
        (timedelta(hours=24), ServiceInterruption.ACTIVE, Priority.URGENT),
        (timedelta(hours=24, microseconds=1), ServiceInterruption.ACTIVE, Priority.HIGH),
        (timedelta(hours=72), ServiceInterruption.NONE, Priority.HIGH),
        (timedelta(hours=72, microseconds=1), ServiceInterruption.NONE, Priority.NORMAL),
    ],
)
def test_priority_exact_24_and_72_hour_boundaries(delta, interruption, expected) -> None:
    facts = facts_for(requested_deadline=NOW + delta, service_interruption=interruption)
    priority, _ = calculate_priority(facts, ServiceCategory.REPAIR, NOW)
    assert priority is expected


def test_deadline_inside_24_hours_without_hard_urgent_combination_is_high() -> None:
    priority, reasons = calculate_priority(
        facts_for(requested_deadline=NOW + timedelta(hours=12)),
        ServiceCategory.REPAIR,
        NOW,
    )
    assert priority is Priority.HIGH
    assert reasons == (PriorityReasonCode.NEAR_TERM_DEADLINE,)


def test_urgent_and_high_rows_retain_all_applicable_reasons_in_catalog_order() -> None:
    urgent, urgent_reasons = calculate_priority(
        facts_for(
            requested_deadline=NOW + timedelta(hours=24),
            safety_or_continuity_concern=SafetyOrContinuityConcern.CRITICAL,
            service_interruption=ServiceInterruption.ACTIVE,
            damage_or_deterioration=DamageOrDeterioration.RAPID,
            material_impact=MaterialImpact.SEVERE,
        ),
        ServiceCategory.REPAIR,
        NOW,
    )
    assert urgent is Priority.URGENT
    assert urgent_reasons == tuple(PriorityReasonCode)[:4]
    high, high_reasons = calculate_priority(
        facts_for(
            requested_deadline=NOW + timedelta(hours=48),
            service_interruption=ServiceInterruption.ACTIVE,
            damage_or_deterioration=DamageOrDeterioration.ACTIVE,
            material_impact=MaterialImpact.MAJOR,
        ),
        ServiceCategory.REPAIR,
        NOW,
    )
    assert high is Priority.HIGH
    assert high_reasons == tuple(PriorityReasonCode)[4:8]


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(days=21, microseconds=-1), Priority.NORMAL),
        (timedelta(days=21), Priority.LOW),
        (timedelta(days=21, microseconds=1), Priority.LOW),
    ],
)
def test_flexible_routine_exact_21_day_low_boundary(delta, expected) -> None:
    priority, _ = calculate_priority(
        facts_for(
            ServiceCategory.ROUTINE_MAINTENANCE,
            requested_deadline=NOW + delta,
            timing_is_flexible=True,
        ),
        ServiceCategory.ROUTINE_MAINTENANCE,
        NOW,
    )
    assert priority is expected


def test_flexible_without_deadline_is_low_but_unknown_risk_is_not() -> None:
    flexible = facts_for(requested_deadline=None, timing_is_flexible=True)
    assert calculate_priority(flexible, ServiceCategory.REPAIR, NOW)[0] is Priority.LOW
    unknown = flexible.model_copy(
        update={"safety_or_continuity_concern": SafetyOrContinuityConcern.UNKNOWN}
    )
    assert calculate_priority(unknown, ServiceCategory.REPAIR, NOW)[0] is Priority.NORMAL


@pytest.mark.parametrize(
    ("confidence", "review_required"),
    [
        (Decimal("0.749999"), True),
        (Decimal("0.75"), False),
        (Decimal("0.750001"), False),
    ],
)
def test_ai_confidence_exact_threshold(confidence, review_required) -> None:
    result = evaluate_decision(decision_input(ai=AIAdvisory(confidence=confidence)))
    assert (ReviewReasonCode.LOW_AI_CONFIDENCE in result.review_reason_codes) is review_required


def test_jaccard_similarity_is_exact_at_below_and_above_point_eight() -> None:
    assert jaccard_similarity(
        (HEX[1], HEX[2], HEX[3]), (HEX[1], HEX[2], HEX[3], HEX[4])
    ) == Decimal("0.75")
    assert jaccard_similarity(
        (HEX[1], HEX[2], HEX[3], HEX[4]),
        (HEX[1], HEX[2], HEX[3], HEX[4], HEX[5]),
    ) == Decimal("0.8")
    assert jaccard_similarity(
        (HEX[1], HEX[2], HEX[3], HEX[4], HEX[5]),
        (HEX[1], HEX[2], HEX[3], HEX[4], HEX[5], HEX[6]),
    ) > Decimal("0.8")


@pytest.mark.parametrize(
    ("candidate_changes", "expected_score", "retained"),
    [
        (
            {
                "description_token_digests": (HEX[4], HEX[5], HEX[6], HEX[7], HEX[10]),
                "requested_service_date": (NOW + timedelta(days=24)).date(),
            },
            35,
            False,
        ),
        (
            {
                "description_token_digests": (HEX[4], HEX[5], HEX[6], HEX[7], HEX[10]),
                "final_category": ServiceCategory.REPAIR,
            },
            40,
            True,
        ),
        (
            {
                "description_fingerprint": HEX[3],
                "final_category": ServiceCategory.REPAIR,
                "requested_service_date": (NOW + timedelta(days=10)).date(),
            },
            60,
            True,
        ),
        ({"contact_id": uuid.UUID(int=99)}, 0, False),
    ],
)
def test_duplicate_retention_score_boundaries(candidate_changes, expected_score, retained) -> None:
    item = candidate(**candidate_changes)
    source = decision_input(candidates=(item,))
    raw = score_duplicate_candidate(
        source.normalized_facts, ServiceCategory.REPAIR, item, DEMO_DECISION_POLICY
    )
    assert raw.score == expected_score
    assert bool(score_duplicate_candidates(source, ServiceCategory.REPAIR)) is retained


def test_exact_description_supersedes_similarity_instead_of_double_counting() -> None:
    item = candidate(
        description_fingerprint=HEX[3],
        description_token_digests=(HEX[4], HEX[5], HEX[6], HEX[7]),
    )
    result = score_duplicate_candidates(decision_input(candidates=(item,)), ServiceCategory.REPAIR)[
        0
    ]
    assert result.score == 45
    assert result.reason_codes == (DuplicateReasonCode.EXACT_DESCRIPTION,)


@pytest.mark.parametrize(
    ("age", "eligible"),
    [
        (timedelta(days=90, microseconds=-1), True),
        (timedelta(days=90), True),
        (timedelta(days=90, microseconds=1), False),
    ],
)
def test_duplicate_lookback_exact_90_day_boundary(age, eligible) -> None:
    item = candidate(activity_at=NOW - age, normalized_email_digest=HEX[1])
    results = score_duplicate_candidates(decision_input(candidates=(item,)), ServiceCategory.REPAIR)
    assert bool(results) is eligible


@pytest.mark.parametrize(("day_offset", "has_timing"), [(13, True), (14, True), (15, False)])
def test_duplicate_timing_proximity_is_inclusive_at_14_calendar_days(
    day_offset, has_timing
) -> None:
    item = candidate(
        description_fingerprint=HEX[3],
        requested_service_date=(NOW + timedelta(days=10 + day_offset)).date(),
    )
    result = score_duplicate_candidates(decision_input(candidates=(item,)), ServiceCategory.REPAIR)[
        0
    ]
    assert (DuplicateReasonCode.TIMING_PROXIMITY in result.reason_codes) is has_timing


def test_duplicate_review_score_exact_60_triggers_and_resolved_observation_does_not() -> None:
    values = {
        "description_fingerprint": HEX[3],
        "final_category": ServiceCategory.REPAIR,
        "requested_service_date": (NOW + timedelta(days=10)).date(),
    }
    pending = candidate(**values)
    pending_result = evaluate_decision(decision_input(candidates=(pending,)))
    assert pending_result.duplicate_candidates[0].score == 60
    assert pending_result.final_status is RequestStatus.DUPLICATE_REVIEW
    resolved = candidate(disposition=CandidateDisposition.NOT_DUPLICATE, **values)
    resolved_result = evaluate_decision(decision_input(candidates=(resolved,)))
    assert resolved_result.final_status is RequestStatus.READY_FOR_ACTION


def test_candidate_order_is_score_then_newest_then_uuid_and_independent_of_input_order() -> None:
    low_id = candidate(1, normalized_email_digest=HEX[1], activity_at=NOW - timedelta(days=2))
    high_id = candidate(2, normalized_email_digest=HEX[1], activity_at=NOW - timedelta(days=2))
    newest = candidate(3, normalized_email_digest=HEX[1], activity_at=NOW - timedelta(days=1))
    higher_score = candidate(
        4,
        normalized_email_digest=HEX[1],
        normalized_phone_digest=HEX[2],
        activity_at=NOW - timedelta(days=3),
    )
    values = (low_id, newest, higher_score, high_id)
    forward = decision_input(candidates=values)
    reverse = decision_input(candidates=tuple(reversed(values)))
    expected = [4, 3, 1, 2]
    assert [
        item.candidate_id.int
        for item in score_duplicate_candidates(forward, ServiceCategory.REPAIR)
    ] == expected
    assert score_duplicate_candidates(
        forward, ServiceCategory.REPAIR
    ) == score_duplicate_candidates(reverse, ServiceCategory.REPAIR)
    assert canonical_decision_input_hash(forward) == canonical_decision_input_hash(reverse)


def test_review_precedence_evidence_then_duplicate_then_urgent_then_missing() -> None:
    duplicate = candidate(normalized_email_digest=HEX[1])
    urgent_facts = facts_for(
        requested_deadline=NOW + timedelta(hours=24),
        service_interruption=ServiceInterruption.ACTIVE,
        installation_target_present=False,
    )
    unavailable = evaluate_decision(
        decision_input(facts=urgent_facts, candidates=(duplicate,), routing_evidence_usable=False)
    )
    assert unavailable.review_reason_codes == (ReviewReasonCode.ROUTING_EVIDENCE_UNAVAILABLE,)
    duplicate_first = evaluate_decision(decision_input(facts=urgent_facts, candidates=(duplicate,)))
    assert duplicate_first.review_reason_codes == (ReviewReasonCode.POSSIBLE_DUPLICATE,)
    urgent_first = evaluate_decision(decision_input(facts=urgent_facts))
    assert urgent_first.review_reason_codes == (ReviewReasonCode.URGENT_PRIORITY,)


def test_missing_information_precedes_all_ai_and_category_review_triggers() -> None:
    ambiguous = facts_for(
        contact_method_present=False,
        inspection_subject_present=True,
        inspection_purpose_present=True,
    )
    result = evaluate_decision(
        decision_input(
            facts=ambiguous,
            ai=AIAdvisory(
                confidence=Decimal("0.70"),
                suggested_category=ServiceCategory.CONSULTATION,
                possible_safety_or_continuity=True,
            ),
        )
    )
    assert result.review_reason_codes == (ReviewReasonCode.MISSING_REQUIRED_INFORMATION,)


def test_ai_review_group_uses_catalog_order_and_exact_conflict_semantics() -> None:
    result = evaluate_decision(
        decision_input(
            ai=AIAdvisory(
                confidence=Decimal("0.74"),
                possible_safety_or_continuity=True,
                missing_information_codes=(MissingInformationCode.REPAIR_SYMPTOMS,),
            )
        )
    )
    assert result.review_reason_codes == (
        ReviewReasonCode.LOW_AI_CONFIDENCE,
        ReviewReasonCode.AI_POSSIBLE_SAFETY_OR_CONTINUITY,
        ReviewReasonCode.AI_MISSING_INFORMATION_CONFLICT,
    )


def test_category_review_group_uses_catalog_order() -> None:
    empty = NormalizedDecisionFacts(
        source_request_id=SOURCE_REQUEST_ID,
        contact_method_present=True,
        timing_preference_present=True,
        location_or_service_context_present=True,
        custom_scope_present=True,
        desired_outcome_present=True,
    )
    result = evaluate_decision(
        decision_input(
            facts=empty,
            ai=AIAdvisory(confidence=Decimal("0.90"), suggested_category=ServiceCategory.REPAIR),
            review=reviewed(custom_scope_confirmed=True),
        )
    )
    assert result.review_reason_codes == (
        ReviewReasonCode.CATEGORY_AMBIGUITY,
        ReviewReasonCode.CATEGORY_CONFLICT,
    )


def test_queue_mapping_for_high_normal_and_low_is_exact() -> None:
    high = evaluate_decision(
        decision_input(
            facts=facts_for(
                requested_deadline=NOW + timedelta(hours=48),
                service_interruption=ServiceInterruption.ACTIVE,
            )
        )
    )
    normal = evaluate_decision(decision_input())
    low = evaluate_decision(
        decision_input(
            facts=facts_for(
                ServiceCategory.ROUTINE_MAINTENANCE,
                timing_is_flexible=True,
                requested_deadline=NOW + timedelta(days=28),
            )
        )
    )
    assert (high.final_status, high.final_queue) == (
        RequestStatus.READY_FOR_ACTION,
        OperationalQueue.PRIORITY_REQUESTS,
    )
    assert (normal.final_status, normal.final_queue) == (
        RequestStatus.READY_FOR_ACTION,
        OperationalQueue.STANDARD_REQUESTS,
    )
    assert low.final_priority is Priority.LOW
    assert low.final_queue is OperationalQueue.STANDARD_REQUESTS


def test_reviewed_urgent_confirmation_keeps_urgent_and_uses_oversight_queue() -> None:
    urgent = facts_for(
        requested_deadline=NOW + timedelta(hours=24),
        service_interruption=ServiceInterruption.ACTIVE,
    )
    result = evaluate_decision(
        decision_input(
            facts=urgent,
            review=reviewed(
                urgent_review_disposition=UrgentReviewDisposition.CONFIRMED_AND_ACTIONABLE
            ),
            current_priority=Priority.URGENT,
        )
    )
    assert result.final_priority is Priority.URGENT
    assert result.review_required is False
    assert result.final_status is RequestStatus.READY_FOR_ACTION
    assert result.final_queue is OperationalQueue.HUMAN_REVIEW
    assert result.requires_manager_or_administrator is True


def test_urgent_disposition_cannot_be_applied_to_a_nonurgent_review() -> None:
    with pytest.raises(DecisionPolicyError) as captured:
        evaluate_decision(
            decision_input(
                review=reviewed(
                    urgent_review_disposition=UrgentReviewDisposition.CONFIRMED_AND_ACTIONABLE
                )
            )
        )
    assert captured.value.code == "URGENT_REVIEW_DISPOSITION_INVALID"


def test_reviewed_fact_recalculation_updates_deadline_and_exposes_next_outstanding_group() -> None:
    initially_missing = facts_for(
        location_or_service_context_present=False,
        requested_deadline=NOW + timedelta(days=10),
    )
    result = evaluate_decision(
        decision_input(
            facts=initially_missing,
            ai=AIAdvisory(confidence=Decimal("0.74")),
            review=reviewed(
                corrected_requested_deadline=NOW + timedelta(days=30),
                resolved_missing_information_codes=(MissingInformationCode.SERVICE_LOCATION,),
            ),
        )
    )
    assert result.missing_information_codes == ()
    assert result.final_priority is Priority.NORMAL
    assert result.review_reason_codes == (ReviewReasonCode.LOW_AI_CONFIDENCE,)
    assert result.source is DecisionSource.REVIEWED_FACT_RECALCULATION


def test_hard_safety_reduction_and_current_or_recalculated_urgent_require_manager() -> None:
    critical = facts_for(safety_or_continuity_concern=SafetyOrContinuityConcern.CRITICAL)
    reduced = evaluate_decision(
        decision_input(
            facts=critical,
            review=reviewed(corrected_safety_or_continuity_concern=SafetyOrContinuityConcern.NONE),
        )
    )
    assert reduced.final_priority is Priority.NORMAL
    assert reduced.requires_manager_or_administrator is True
    prior_urgent = evaluate_decision(
        decision_input(
            review=reviewed(corrected_material_impact=MaterialImpact.MINOR),
            current_priority=Priority.URGENT,
        )
    )
    assert prior_urgent.requires_manager_or_administrator is True


def test_canonical_input_is_repeatable_order_independent_utc_normalized_and_safe() -> None:
    first_candidate = candidate(1, normalized_email_digest=HEX[1])
    second_candidate = candidate(2, normalized_phone_digest=HEX[2])
    first = decision_input(candidates=(first_candidate, second_candidate))
    second = decision_input(
        candidates=(second_candidate, first_candidate),
        evaluation_at=NOW.astimezone(timezone(timedelta(hours=8))),
    )
    assert canonical_decision_input_hash(first) == canonical_decision_input_hash(second)
    assert evaluate_decision(first) == evaluate_decision(first)
    material = canonical_decision_input_bytes(first)
    assert b"raw" not in material
    assert b"contact-log" not in material
    assert b"review-rationale" not in material


def test_canonical_input_normalizes_equivalent_decimal_and_reason_code_order() -> None:
    first = decision_input(
        ai=AIAdvisory(
            confidence=Decimal("0.90"),
            missing_information_codes=(
                MissingInformationCode.REPAIR_SYMPTOMS,
                MissingInformationCode.CONTACT_METHOD,
            ),
        )
    )
    second = decision_input(
        ai=AIAdvisory(
            confidence=Decimal("0.9"),
            missing_information_codes=(
                MissingInformationCode.CONTACT_METHOD,
                MissingInformationCode.REPAIR_SYMPTOMS,
            ),
        )
    )
    assert first.ai_advisory.confidence == Decimal("0.9")
    assert canonical_decision_input_hash(first) == canonical_decision_input_hash(second)


def test_review_rationale_and_evidence_references_do_not_enter_decision_hash() -> None:
    first_review = reviewed(corrected_material_impact=MaterialImpact.MINOR)
    second_review = first_review.model_copy(
        update={
            "rationale_reference": "different-rationale-reference",
            "supporting_evidence_references": ("different-safe-evidence",),
        }
    )
    first = decision_input(review=first_review)
    second = decision_input(review=second_review)
    assert canonical_decision_input_hash(first) == canonical_decision_input_hash(second)


def test_naive_times_extra_fields_and_duplicate_evidence_identities_are_rejected() -> None:
    with pytest.raises(ValidationError):
        decision_input(evaluation_at=datetime(2026, 7, 13, 12))
    duplicate = candidate()
    with pytest.raises(ValidationError, match="identities must be unique"):
        decision_input(candidates=(duplicate, duplicate))
    with pytest.raises(ValidationError):
        AIAdvisory.model_validate({"confidence": "0.9", "final_priority": "Urgent"})


def test_self_candidate_future_candidate_and_ineligible_candidate_are_excluded() -> None:
    self_candidate = candidate(
        candidate_id=SOURCE_REQUEST_ID.int,
        normalized_email_digest=HEX[1],
    )
    future = candidate(
        11, activity_at=NOW + timedelta(microseconds=1), normalized_email_digest=HEX[1]
    )
    ineligible = candidate(12, eligible_record=False, normalized_email_digest=HEX[1])
    assert (
        score_duplicate_candidates(
            decision_input(candidates=(self_candidate, future, ineligible)), ServiceCategory.REPAIR
        )
        == ()
    )
