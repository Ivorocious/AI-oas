# Deterministic Triage and Human-Review Policy

## Status and scope

This is the approved, versioned decision policy for the Phase 1 MVP and its Phase 2 implementation. It defines how a successfully interpreted `ServiceRequest` is deterministically classified, prioritized, checked for possible duplicates, routed, and recorded in an immutable `RoutingDecision`. It refines the [domain model](domain-model.md), [state machines](state-machines.md), [API contracts](api-contracts.md), [authentication and authorization](authentication-and-authorization.md), and [persistence design](persistence-design.md).

The ordered evaluator, immutable policy persistence, duplicate-candidate evidence, routing decisions, bounded reviewed facts, trusted in-process `CompleteTriage`, and human-authenticated duplicate-resolution and complete-human-review commands are implemented. `CompleteTriage` is intentionally not a public HTTP route. There is no policy-management API or configuration UI. Failure classification, retry limits, backoff, dead-letter thresholds, and uncertain-side-effect reconciliation remain governed separately by the [failure and recovery policy](failure-and-recovery-policy.md).

## Authority and outcomes

FastAPI is the authoritative evaluator. It selects one immutable active policy version, canonicalizes allowlisted inputs, evaluates the ordered rules below, and atomically creates a `RoutingDecision` with the request's current summary. AI output, frontend input, and n8n requests are evidence or intent; none can directly set a final category, priority, queue, lifecycle state, approval, or duplicate resolution.

For one current request version and one policy version, a completed evaluation produces all of the following together:

- a final category;
- a final priority;
- zero or more separately inspectable duplicate candidates;
- a review-gate outcome and ordered reason codes;
- a request status and operational queue; and
- an immutable, reproducible `RoutingDecision`.

`TriagePending` remains a processing checkpoint with no active request queue. `InvalidSubmissions` remains an `InboundDelivery` view, not a result for an accepted `ServiceRequest`.

## Stable service categories

The following wire values are the only initial category values in API, audit, policy, persistence, fixtures, and integrations. Display labels may differ, but they must not substitute for these values.

| Wire value | Display label | Meaning and typical normalized evidence | Required category information before `ReadyForAction` | Deterministic fallback behavior |
| --- | --- | --- | --- | --- |
| `Consultation` | Consultation | Advice, evaluation, planning, or an expert discussion. Evidence includes a consultation topic and intended outcome. | Consultation topic and desired outcome. | If the evidence instead asserts a concrete installation, repair, maintenance, or inspection need, treat it as a category conflict. |
| `Installation` | Installation | New placement, setup, fitting, configuration, or replacement work. Evidence includes an installation target and scope. | Installation target and installation scope. | An explicit selection without target or scope remains `Installation` but has required-information review. |
| `Repair` | Repair | Restoring an existing asset, service, or work item with a reported symptom or fault. | Repair symptoms and affected asset or context. | If symptoms are absent and another category is not determinable, use `OtherCustomRequest` with review. |
| `RoutineMaintenance` | Routine maintenance | Preventive, scheduled, recurring, or upkeep work for an identified asset or context. | Maintenance asset or context. | A reported active fault or damage conflicts with a purely routine classification and requires review. |
| `Inspection` | Inspection | Examination, assessment, verification, or a report on a defined subject, without an asserted repair request. | Inspection subject and inspection purpose. | A request for corrective work in the same input is multiple plausible categories and requires review. |
| `OtherCustomRequest` | Other/custom request | A custom service need, no usable category evidence, or multiple plausible categories. | Custom scope and desired outcome. | It initially requires category/scope review. It may progress only after an allowlisted human-reviewed fact confirms the custom scope and all other required information is present. |

An explicit valid customer selection is authoritative category evidence, but it is not an unconditional output. A conflicting normalized fact, multiple category fact sets, or a human-reviewed correction is handled by the ordered algorithm below. AI's suggested category can corroborate, conflict with, or be irrelevant to that evidence; it never becomes the final category by itself.

## Immutable policy versions

The MVP proposes an immutable `decision_policy_versions` support record, seeded and deployment-controlled rather than exposed through a configuration UI or generic configuration API. A record contains:

| Field | Proposed purpose |
| --- | --- |
| `policy_id` | Stable UUID identity for one immutable policy record. |
| `policy_key` | Human-recognizable policy family, initially `general-service-demo`. |
| `semantic_version` and `revision` | `1.0.0` and monotonically increasing revision `1` for the initial demonstration policy. Both are retained so a semantic release and a storage-safe ordering are explicit. |
| `content_digest` | SHA-256 digest of the canonical policy content. |
| `effective_at` and `status` | UTC effective time and `Draft`, `Active`, or `Retired` status. |
| immutable content snapshot | Category definitions, confidence threshold, required-information rules, priority criteria, duplicate rules, review triggers, queue mapping, and the reason-code catalog. |

The initial active policy is `general-service-demo` semantic version `1.0.0`, revision `1`, effective at `2026-07-11T00:00:00Z`. Migration `0010_deterministic_triage_foundation` seeds this exact immutable snapshot, and the runtime verifies its identity and canonical content before evaluation. Only one policy is selected for a new calculation. A later version does not silently recalculate existing requests: it applies only to a later guarded triage or review calculation and is retained alongside its predecessor.

Every `RoutingDecision` stores the selected policy ID, semantic version, revision, and digest. It also stores a canonical, allowlisted decision-input hash; the evaluation timestamp used for relative timing; interpretation and duplicate evidence identities; results; source; and, when applicable, reviewed-fact actor and rationale references. This makes a historical result explainable even when a new policy becomes active.

## Allowlisted decision inputs

The policy consumes normalized fields, not unrestricted customer text. Normalization itself is a proposed backend concern and produces a schema-versioned input snapshot. The following classes make authority visible.

| Input class | Allowed examples | Authority and use |
| --- | --- | --- |
| Authoritative normalized facts | Valid explicit category selection; contact method presence; location/context presence; requested deadline or flexible timing; existing-work flag; category-specific facts; normalized interruption, damage, safety/continuity, and material-impact enums. | Created from validated intake or a permitted reviewed-fact set. These facts can determine category, priority, and required-information results. |
| Deterministically derived signals | Deadline interval measured from the recorded evaluation UTC instant; normalized description fingerprint; token-set similarity; category evidence set; location/timing match; duplicate score; required-information codes. | Produced by fixed policy operations over authoritative facts. They must be captured or reproducible from the input snapshot and policy. |
| AI advisory signals | Validated summary; suggested category; missing-information codes; confidence; AI potential safety/continuity signal. | May add corroboration, a conflict or review reason, and low-confidence review. They cannot satisfy a required fact or directly set category, priority, queue, state, approval, or duplicate resolution. |
| Human-reviewed facts | Corrected category, deadline/timing, impact, interruption, damage, safety/continuity, resolved missing-information references, custom-scope confirmation, urgent review disposition, rationale, and evidence references. | Immutable, allowlisted evidence accepted only through `complete-human-review`; it causes a deterministic recalculation rather than an output patch. |
| Untrusted free text | Raw description, notes, emails, response text, or a client-provided priority label. | Not read directly by routing. It may be normalized into bounded facts or a fingerprint, but is never an authoritative final-routing field. |

`safety_or_continuity_concern` is normalized as `None`, `Reported`, `Critical`, or `Unknown`. `service_interruption` is `None`, `Active`, or `Unknown`; `damage_or_deterioration` is `None`, `Active`, `Rapid`, or `Unknown`; and `material_impact` is `None`, `Minor`, `Major`, `Severe`, or `Unknown`. `Critical`, `Active`, `Rapid`, `Major`, and `Severe` are facts only when supported by explicit validated intake evidence or an authorized reviewed fact. An AI-detected possible safety or continuity concern adds `REVIEW_AI_POSSIBLE_SAFETY_OR_CONTINUITY`; it cannot itself create `Urgent`.

The canonical input hash includes the policy-independent normalized fact snapshot, current interpretation ID/version and accepted advisory fields, ordered current duplicate evidence IDs and evidence hashes, immutable reviewed-fact set IDs, and the UTC evaluation instant. It excludes raw customer text and display-only values. This is necessary because a deadline measured at different UTC instants can legitimately produce a different priority.

## Initial configurable demonstration defaults

These are concrete MVP demonstration defaults, not universal service-industry guidance. Equality is intentionally specified so future boundary tests are unambiguous.

| Setting | Initial value and equality behavior | Rationale and tradeoff |
| --- | --- | --- |
| AI-confidence review threshold | `0.75`; values strictly below `0.75` trigger `REVIEW_LOW_AI_CONFIDENCE`; exactly `0.75` does not. | Keeps routine demonstrations moving while exposing uncertain interpretation. It is not a quality guarantee. |
| Duplicate lookback | `90` calendar days ending at the evaluation instant; exactly 90 days old is included. | Balances a demonstrable repeat-request window against matching very old history. |
| Material candidate retention | Score `>= 40` out of 100 is retained as a separately inspectable candidate; exactly 40 is retained. | Preserves meaningful weak evidence without forcing all retained candidates to review. |
| Duplicate-review threshold | Score `>= 60` out of 100 triggers `REVIEW_POSSIBLE_DUPLICATE`; exactly 60 qualifies. | Strong identity matches qualify by themselves; multi-signal near matches need corroboration. |
| Description similarity | Jaccard similarity of normalized token sets `>= 0.80`; exactly `0.80` receives the similarity weight. Exact normalized fingerprint is a separate stronger signal. | Easy to reproduce and demonstrate, but intentionally not semantic matching. |
| Timing proximity for duplicate evidence | Requested dates within `14` calendar days inclusive. | Limits timing evidence to likely related requests. |
| Urgent timing window | Deadline `<= 24` hours from the recorded evaluation instant, when combined with the urgent evidence below; exactly 24 hours qualifies. | Demonstrates immediate handling without declaring all next-day work urgent. |
| High timing window | Deadline `> 24` and `<= 72` hours; exactly 72 hours qualifies. A deadline `<= 24` that lacks an urgent combination is still High. | Separates near-term handling from a fact-backed urgent condition. |
| Low flexible window | At least `21` days until deadline, or an explicitly flexible timing preference with no earlier deadline; exactly 21 days qualifies. | Keeps clearly routine, low-impact work visible without fabricating Low priority from missing data. |

Duplicate score is `min(100, sum of applicable weights)`. Each signal is counted once per candidate:

| Deterministic duplicate signal | Weight |
| --- | ---: |
| Exact normalized email | 70 |
| Exact normalized phone | 70 |
| Same existing contact ID | 65 |
| Exact normalized request-description fingerprint | 45 |
| Token-set similarity `>= 0.80` | 30 |
| Same final category | 10 |
| Exact normalized service location/context | 10 |
| Requested timing within 14 calendar days | 5 |

For example, same contact ID alone is review material at 65; an exact fingerprint plus category and location is 65; and a 0.80 similarity plus category, location, and timing is 55, retained but not a review trigger. The score is a demonstration policy, not an assertion that the numeric score is a probability.

## Stable reason-code catalog

The policy snapshot carries this catalog. `RoutingDecision` retains the applicable ordered result and review codes; audit/outbox projections include only the minimum safe subset. API error codes such as `REVIEW_FACT_NOT_ALLOWED` and `REVIEW_REQUIREMENTS_UNRESOLVED` are command errors, not policy review reason codes.

| Family | Stable codes |
| --- | --- |
| Category evidence | `CATEGORY_REVIEWED_CORRECTION`, `CATEGORY_EXPLICIT_SELECTION_ACCEPTED`, `CATEGORY_NORMALIZED_EVIDENCE`, `CATEGORY_AI_AGREES`, `CATEGORY_AI_CONFLICT`, `CATEGORY_CONFLICT`, `CATEGORY_MULTIPLE_PLAUSIBLE`, `CATEGORY_EVIDENCE_UNUSABLE`, `CATEGORY_OTHER_CUSTOM_SCOPE` |
| Missing information | `MISSING_CONTACT_METHOD`, `MISSING_TIMING_PREFERENCE`, `MISSING_SERVICE_LOCATION`, `MISSING_ACCESS_CONSTRAINTS`, `MISSING_CONSULTATION_TOPIC`, `MISSING_DESIRED_OUTCOME`, `MISSING_INSTALLATION_TARGET`, `MISSING_INSTALLATION_SCOPE`, `MISSING_REPAIR_SYMPTOMS`, `MISSING_REPAIR_ASSET_CONTEXT`, `MISSING_MAINTENANCE_ASSET_CONTEXT`, `MISSING_INSPECTION_SUBJECT`, `MISSING_INSPECTION_PURPOSE`, `MISSING_CUSTOM_SCOPE`, `MISSING_CUSTOM_SCOPE_CONFIRMATION` |
| Priority | `PRIORITY_CRITICAL_SAFETY_OR_CONTINUITY`, `PRIORITY_ACTIVE_INTERRUPTION_IMMEDIATE`, `PRIORITY_RAPID_DAMAGE_IMMEDIATE`, `PRIORITY_SEVERE_IMPACT_IMMEDIATE`, `PRIORITY_ACTIVE_INTERRUPTION`, `PRIORITY_ACTIVE_DAMAGE_OR_DETERIORATION`, `PRIORITY_MAJOR_OR_SEVERE_IMPACT`, `PRIORITY_NEAR_TERM_DEADLINE`, `PRIORITY_FLEXIBLE_ROUTINE_WORK`, `PRIORITY_DEFAULT_NORMAL` |
| Duplicate evidence | `DUPLICATE_EXACT_EMAIL`, `DUPLICATE_EXACT_PHONE`, `DUPLICATE_EXISTING_CONTACT`, `DUPLICATE_EXACT_DESCRIPTION`, `DUPLICATE_DESCRIPTION_SIMILARITY`, `DUPLICATE_CATEGORY_MATCH`, `DUPLICATE_LOCATION_MATCH`, `DUPLICATE_TIMING_PROXIMITY` |
| Review gate | `REVIEW_ROUTING_EVIDENCE_UNAVAILABLE`, `REVIEW_POSSIBLE_DUPLICATE`, `REVIEW_URGENT_PRIORITY`, `REVIEW_REPORTED_SAFETY_OR_CONTINUITY`, `REVIEW_MISSING_REQUIRED_INFORMATION`, `REVIEW_LOW_AI_CONFIDENCE`, `REVIEW_AI_POSSIBLE_SAFETY_OR_CONTINUITY`, `REVIEW_AI_MISSING_INFORMATION_CONFLICT`, `REVIEW_CATEGORY_AMBIGUITY`, `REVIEW_CATEGORY_CONFLICT`, `REVIEW_OTHER_CUSTOM_SCOPE` |

## Ordered category resolution

The policy builds a deterministic category-evidence set from bounded normalized features. A category fact set is present only when its category-specific evidence is present: consultation topic/outcome; installation target/scope; repair symptoms/affected context; maintenance asset/context; inspection subject/purpose. The algorithm is ordered and records all applicable reason codes.

| Order | Condition | Final category | Reason codes and review effect |
| ---: | --- | --- | --- |
| 1 | An immutable reviewed fact contains a valid corrected category and supporting evidence. | Reviewed category. | `CATEGORY_REVIEWED_CORRECTION`; retain conflict evidence if it existed. |
| 2 | One valid explicit customer category and no conflicting normalized category fact set. | Explicit category. | `CATEGORY_EXPLICIT_SELECTION_ACCEPTED`; add `CATEGORY_AI_AGREES` if applicable. |
| 3 | One normalized category fact set, with no valid explicit selection. | That evidence-backed category. | `CATEGORY_NORMALIZED_EVIDENCE`; add `CATEGORY_AI_AGREES` if applicable. |
| 4 | Valid explicit selection conflicts with normalized fact evidence, or more than one normalized category fact set is present. | `OtherCustomRequest`. | `CATEGORY_CONFLICT` or `CATEGORY_MULTIPLE_PLAUSIBLE`; review is required. |
| 5 | No usable explicit or normalized category evidence. | `OtherCustomRequest`. | `CATEGORY_EVIDENCE_UNUSABLE`; review is required. |
| 6 | The resulting category is `OtherCustomRequest` without a reviewed `custom_scope_confirmed` fact. | `OtherCustomRequest`. | `CATEGORY_OTHER_CUSTOM_SCOPE`; review is required. |

An AI suggestion that disagrees with the final evidence-backed category adds `CATEGORY_AI_CONFLICT` and `REVIEW_CATEGORY_CONFLICT`; it does not replace the category. An AI suggestion alone falls through to `OtherCustomRequest` with review. An existing category from an earlier decision is historical evidence only: it may be used as a comparison signal but is not carried forward as an output unless current reviewed or normalized facts independently produce it. A policy-version change uses the same algorithm under the newly selected policy and records the prior decision ID.

## Required-information matrix

All categories require a usable contact method and a timing preference or requested deadline before `ReadyForAction`. `location_or_service_context` is required when the proposed work is on-site; an explicitly remote consultation can satisfy it with `remote` context. A normalized intake fact may satisfy a cell marked "normalized", while a client cannot claim completion merely by sending a final state or a free-text note.

| Category | Required before `ReadyForAction` | Useful but not blocking | May be satisfied by normalized intake | Requires customer or human clarification | Stable missing-information codes |
| --- | --- | --- | --- | --- | --- |
| `Consultation` | Contact method; timing; consultation topic; desired outcome; applicable location/context. | Preferred channel; relevant existing-work context. | Contact, timing, topic, outcome, and `remote` context. | Ambiguous topic or desired outcome. | `MISSING_CONTACT_METHOD`, `MISSING_TIMING_PREFERENCE`, `MISSING_CONSULTATION_TOPIC`, `MISSING_DESIRED_OUTCOME`, `MISSING_SERVICE_LOCATION` |
| `Installation` | Contact method; timing; location/context; installation target; installation scope; access constraints when on-site. | Preferred date; existing-work context. | Contact, timing, location, target, scope, and stated access constraints. | Unclear target, scope, or access. | `MISSING_CONTACT_METHOD`, `MISSING_TIMING_PREFERENCE`, `MISSING_SERVICE_LOCATION`, `MISSING_INSTALLATION_TARGET`, `MISSING_INSTALLATION_SCOPE`, `MISSING_ACCESS_CONSTRAINTS` |
| `Repair` | Contact method; timing; location/context; repair symptoms; affected asset or context; access constraints when on-site. | Existing-work reference; preferred channel. | Contact, timing, location, symptom, asset/context, and stated access constraints. | Symptoms or affected context cannot be normalized. | `MISSING_CONTACT_METHOD`, `MISSING_TIMING_PREFERENCE`, `MISSING_SERVICE_LOCATION`, `MISSING_REPAIR_SYMPTOMS`, `MISSING_REPAIR_ASSET_CONTEXT`, `MISSING_ACCESS_CONSTRAINTS` |
| `RoutineMaintenance` | Contact method; timing; location/context; maintenance asset or context; access constraints when on-site. | Last service date; recurring cadence; existing-work reference. | Contact, timing, location, asset/context, and stated access constraints. | Asset/context or access is unclear. | `MISSING_CONTACT_METHOD`, `MISSING_TIMING_PREFERENCE`, `MISSING_SERVICE_LOCATION`, `MISSING_MAINTENANCE_ASSET_CONTEXT`, `MISSING_ACCESS_CONSTRAINTS` |
| `Inspection` | Contact method; timing; location/context; inspection subject; inspection purpose; access constraints when on-site. | Desired report format; existing-work reference. | Contact, timing, location, subject, purpose, and stated access constraints. | Subject or purpose is unclear. | `MISSING_CONTACT_METHOD`, `MISSING_TIMING_PREFERENCE`, `MISSING_SERVICE_LOCATION`, `MISSING_INSPECTION_SUBJECT`, `MISSING_INSPECTION_PURPOSE`, `MISSING_ACCESS_CONSTRAINTS` |
| `OtherCustomRequest` | Contact method; timing; custom scope; desired outcome; applicable location/context; reviewed custom-scope confirmation. | Preferred channel; existing-work reference. | Contact, timing, custom scope, outcome, and location. | Every unconfirmed custom scope and category ambiguity. | `MISSING_CONTACT_METHOD`, `MISSING_TIMING_PREFERENCE`, `MISSING_CUSTOM_SCOPE`, `MISSING_DESIRED_OUTCOME`, `MISSING_SERVICE_LOCATION`, `MISSING_CUSTOM_SCOPE_CONFIRMATION` |

The backend computes missing codes from normalized facts first. It then compares a validated AI missing-information list against the same catalog:

- An AI code that agrees with a deterministic missing fact is retained as advisory corroboration but does not create a duplicate missing field.
- An AI code that asserts a field is missing when deterministic evidence satisfies it adds `REVIEW_AI_MISSING_INFORMATION_CONFLICT`.
- An AI code cannot mark a required fact as satisfied. A reviewed fact can satisfy a missing item only when it is allowlisted, supported, and persisted with actor and rationale.

## Priority policy

Priority is a deterministic result over current authoritative or reviewed facts. AI confidence never raises or lowers it, and a duplicate candidate never silently changes it. Missing required information does not manufacture a Low result; the policy still calculates a fact-based priority and separately requires review.

| Precedence | Priority | Exact deterministic criteria | Stable priority reason codes |
| ---: | --- | --- | --- |
| 1 | `Urgent` | `safety_or_continuity_concern=Critical`; or `service_interruption=Active` and deadline `<= 24h`; or `damage_or_deterioration=Rapid` and deadline `<= 24h`; or `material_impact=Severe` and deadline `<= 24h`. | `PRIORITY_CRITICAL_SAFETY_OR_CONTINUITY`, `PRIORITY_ACTIVE_INTERRUPTION_IMMEDIATE`, `PRIORITY_RAPID_DAMAGE_IMMEDIATE`, `PRIORITY_SEVERE_IMPACT_IMMEDIATE` |
| 2 | `High` | Any active interruption; active or rapid damage/deterioration; material impact `Major` or `Severe`; or deadline `<= 72h` after the Urgent rules have not matched. | `PRIORITY_ACTIVE_INTERRUPTION`, `PRIORITY_ACTIVE_DAMAGE_OR_DETERIORATION`, `PRIORITY_MAJOR_OR_SEVERE_IMPACT`, `PRIORITY_NEAR_TERM_DEADLINE` |
| 3 | `Low` | No Urgent or High criterion; no reported safety/continuity concern, interruption, or active damage; material impact `None` or `Minor`; and either `RoutineMaintenance` or `Inspection` with a flexible timing preference and deadline at least 21 days away, or an explicitly flexible timing preference with no earlier deadline. | `PRIORITY_FLEXIBLE_ROUTINE_WORK` |
| 4 | `Normal` | All remaining cases. | `PRIORITY_DEFAULT_NORMAL` |

The first matching row wins. At exact boundaries: an active interruption with a deadline exactly 24 hours away is `Urgent`; at 24 hours and one second it is `High`; a deadline exactly 72 hours away is `High`; and flexible routine work exactly 21 days away is `Low` if all Low conditions are met. A deadline within 24 hours with no fact-backed urgent combination is `High`, not `Urgent`. A `Reported` (not `Critical`) safety/continuity concern forces review but does not independently set `Urgent`; it may still coexist with another fact that does.

An authorized reviewed fact can change the priority input and causes recalculation. Reducing or dismissing a hard safety/continuity signal requires a `ManagerApprover` or `Administrator`, an explicit corrected enum fact, supporting evidence, and rationale. No client provides final priority as an unrestricted field.

## Duplicate-candidate policy

Candidate generation is separate from accepted-intake idempotency. A repeated delivery with an accepted intake key returns the original logical result and is never a `DuplicateCandidate`. Candidate generation examines eligible existing contacts and non-invalid service requests within the 90-day lookback, using the score table above.

| Topic | Proposed rule |
| --- | --- |
| Contact signals | Compare exact normalized email, exact normalized phone, and existing `contact_id` when known. A candidate may be a `Contact` or `ServiceRequest`; its type and ID are retained. |
| Request signals | Compare normalized request-description fingerprint first, then the deterministic normalized token-set similarity. Add category, location/context, and timing signals only when both values are available. |
| Candidate eligibility | Exclude the source request, invalid deliveries, intake replays, and records older than the inclusive 90-day window. A confirmed duplicate cannot be a later source for triage. |
| Materiality and review | Retain score `>= 40`; a current pending score `>= 60` requires `DuplicateReview` and `DuplicateReview` queue. |
| Reason codes | Retain ordered code(s) such as `DUPLICATE_EXACT_EMAIL`, `DUPLICATE_EXACT_PHONE`, `DUPLICATE_EXISTING_CONTACT`, `DUPLICATE_EXACT_DESCRIPTION`, `DUPLICATE_DESCRIPTION_SIMILARITY`, `DUPLICATE_CATEGORY_MATCH`, `DUPLICATE_LOCATION_MATCH`, and `DUPLICATE_TIMING_PROXIMITY`. |
| Uniqueness and ordering | An immutable observation is unique for source request, candidate kind/ID, policy version, and source/candidate evidence hashes. Sort current candidates by descending score, then newest candidate activity timestamp, then candidate UUID. |
| PII-safe display | General queues expose score tier, masked contact evidence, stable reason codes, and candidate reference. Full contact fields require an authorized detail view and are not copied into events. |

Candidates never automatically merge contacts, close a request, or change priority. The existing dedicated duplicate-resolution command is the only way to confirm `ConfirmedDuplicate` or `NotDuplicate`; only an authorized confirmed-duplicate resolution may close the source request. A `NotDuplicate` resolution remains historical evidence. When its exact source/candidate evidence hashes recur, it prevents that old observation from becoming a fresh pending review item; materially changed evidence can create a new observation. An observation outside the lookback or superseded by changed evidence is stale and no longer contributes a pending review trigger, but its ID and disposition remain in later decision evidence. A pending material candidate always remains separately inspectable.

## Review-trigger precedence and queue mapping

`complete-triage` first requires a current validated interpretation and a complete canonical input snapshot. If either is structurally absent or stale, the backend returns `409 TRIAGE_EVIDENCE_STALE`, leaves the request in `TriagePending`, and creates no routing decision. This is a command precondition, not a provider-failure policy.

Once that precondition is met, evaluate in this order. The ordered reason-code list follows the first applicable group, then the catalog order inside that group.

| Order | Condition | Outcome |
| ---: | --- | --- |
| 1 | Required routing evidence is unusable or cannot be normalized safely, while a deterministic decision can still be calculated. | `HumanReview`, `HumanReview`, `REVIEW_ROUTING_EVIDENCE_UNAVAILABLE`. |
| 2 | A current pending duplicate candidate has score `>= 60`. | `DuplicateReview`, `DuplicateReview`, `REVIEW_POSSIBLE_DUPLICATE`. This takes precedence over Urgent, missing, low-confidence, and category-review triggers. |
| 3 | Priority is `Urgent`. | `HumanReview`, `HumanReview`, `REVIEW_URGENT_PRIORITY`. |
| 4 | Authoritative safety/continuity concern is `Reported` but not `Critical`. | `HumanReview`, `HumanReview`, `REVIEW_REPORTED_SAFETY_OR_CONTINUITY`. |
| 5 | One or more required-information codes remain. | `HumanReview`, `HumanReview`, `REVIEW_MISSING_REQUIRED_INFORMATION`. |
| 6 | AI confidence is below 0.75, AI reports a possible safety/continuity signal, or AI missing-information evidence conflicts with deterministic facts. | `HumanReview`, `HumanReview`, `REVIEW_LOW_AI_CONFIDENCE`, `REVIEW_AI_POSSIBLE_SAFETY_OR_CONTINUITY`, and/or `REVIEW_AI_MISSING_INFORMATION_CONFLICT`. |
| 7 | Category ambiguity, category conflict, unconfirmed `OtherCustomRequest`, or AI category conflict is present. | `HumanReview`, `HumanReview`, `REVIEW_CATEGORY_AMBIGUITY`, `REVIEW_CATEGORY_CONFLICT`, or `REVIEW_OTHER_CUSTOM_SCOPE`. |
| 8 | Another approved policy exception applies. The initial policy defines only the evidence-unavailable case above. | `HumanReview`, `HumanReview`, the registered stable code. |
| 9 | No review condition remains and priority is `High`. | `ReadyForAction`, `PriorityRequests`. |
| 10 | No review condition remains and priority is `Low` or `Normal`. | `ReadyForAction`, `StandardRequests`. |

The following combinations illustrate precedence. Processing failures and recovery outcomes are intentionally not defined here.

| Pending material duplicate | Urgent | Missing required information | Low AI confidence or category conflict | Resulting status and queue |
| --- | --- | --- | --- | --- |
| Yes | Any | Any | Any | `DuplicateReview`, `DuplicateReview` |
| No | Yes | Any | Any | `HumanReview`, `HumanReview` |
| No | No | Yes | Any | `HumanReview`, `HumanReview` |
| No | No | No | Yes | `HumanReview`, `HumanReview` |
| No | No | No | No; priority High | `ReadyForAction`, `PriorityRequests` |
| No | No | No | No; priority Low or Normal | `ReadyForAction`, `StandardRequests` |

For initial triage, `Urgent` always takes the `HumanReview` path. After an authorized manager/admin completes an urgent review, the immutable reviewed fact `urgent_review_disposition=ConfirmedAndActionable` can satisfy that review gate without lowering the `Urgent` priority. The recalculated decision has `review_required=false` and no outstanding `REVIEW_URGENT_PRIORITY`, while the reviewed-fact/audit history preserves why it was satisfied. The resulting request may become `ReadyForAction` while retaining `HumanReview` as its oversight queue, as already allowed by the separate status-and-queue model. That special post-review path cannot be selected by an OperationsAgent and does not approve a proposal.

## Bounded human-reviewed facts

The existing `POST /api/v1/service-requests/{request_id}/commands/complete-human-review` route is the sole MVP mechanism for resolving review facts. It is not a generic routing, status, queue, or priority patch. The command accepts only allowlisted facts and records an immutable reviewed-fact set before deterministic recalculation.

```json
{
  "schema_version": "1.0",
  "expected_versions": {
    "service_request": 7
  },
  "expected_policy": {
    "policy_key": "general-service-demo",
    "semantic_version": "1.0.0",
    "revision": 1
  },
  "reviewed_facts": {
    "resolved_missing_information_codes": [
      "MISSING_SERVICE_LOCATION"
    ],
    "corrected_category": "Repair",
    "corrected_requested_deadline": "2026-07-14T12:00:00Z",
    "corrected_service_interruption": "None",
    "corrected_damage_or_deterioration": "Active",
    "corrected_safety_or_continuity_concern": "None"
  },
  "addressed_review_reason_codes": [
    "REVIEW_MISSING_REQUIRED_INFORMATION",
    "REVIEW_CATEGORY_CONFLICT"
  ],
  "rationale": "Verified the location and repair symptoms with the customer.",
  "supporting_evidence_references": [
    "contact-log:case-1042"
  ]
}
```

An accepted body may contain only:

- resolved missing-information references;
- corrected category fact or custom-scope confirmation;
- corrected timing or deadline fact;
- corrected material-impact, interruption, damage/deterioration, or safety/continuity fact;
- an urgent-review disposition when current/recalculated priority is `Urgent`;
- addressed review codes, a required rationale, and supporting evidence references.

It must not contain final lifecycle state, final queue, unrestricted final priority, arbitrary routing output, approval state, duplicate resolution, retry eligibility, or an arbitrary status/reopen directive. Unsupported reviewed-fact names return `422 REVIEW_FACT_NOT_ALLOWED`; a stale active policy selected after the client read the request returns `409 POLICY_VERSION_CONFLICT`; an unresolved required fact returns `409 REVIEW_REQUIREMENTS_UNRESOLVED`.

On acceptance, FastAPI selects the current active policy version after checking the expected version, persists the facts and immutable actor/rationale attribution, reruns deterministic policy, creates a new `RoutingDecision`, and atomically updates the request summary. `OperationsAgent` may do this only when both current and recalculated priority are non-Urgent. `ManagerApprover` or `Administrator` is required whenever current evidence or recalculation is Urgent. A hard safety/continuity reduction or dismissal additionally requires one of those roles, the explicit corrected fact, evidence references, and rationale.

No actor can suppress a pending duplicate through this command; it remains `DuplicateReview` until the dedicated duplicate-resolution command is used. No review completion bypasses required information or directly approves a proposal. The command requires at least one accepted reviewed fact; there is no note-only completion path in the MVP.

Every accepted reviewed-fact submission persists one immutable `ReviewedFactSet`, reruns the complete deterministic policy, and inserts one complete immutable `RoutingDecision`. It atomically updates `current_routing_decision_id`, current category, priority, review summary, status, queue, and request version even when status and queue remain `HumanReview`. An incomplete result has `review_required=true`, contains the complete outstanding reason-code set, remains `HumanReview`/`HumanReview`, increments the request version, and returns the new decision ID, new version, and outstanding codes. A concurrent submission using the old version conflicts. Post-`ReadyForAction` manual rerouting or reopening is outside this MVP policy.

## Immutable `RoutingDecision`

`RoutingDecision` is append-only. A proposed row contains at least:

| Field group | Required record |
| --- | --- |
| Identity | Decision UUID, service-request UUID, monotonically increasing decision version, creation UTC timestamp, and optional prior decision UUID. |
| Policy and input | Policy ID, semantic version, revision, digest, evaluation UTC instant, canonical input hash, and a minimal sanitized input snapshot reference. |
| Evidence | Current AI interpretation ID/version, accepted AI-confidence value, ordered missing-information codes, ordered duplicate candidate observation IDs considered, and immutable reviewed-fact set ID when present. |
| Results | Final category, final priority, final status, final queue, `review_required` boolean, ordered review reason codes, and category/priority reason codes. |
| Provenance | `InitialDeterministicCalculation` or `ReviewedFactRecalculation`; reviewed actor UUID and rationale reference when applicable. |

The request's category, priority, status, current queue, and current routing-decision reference update in the same optimistic-version transaction that inserts this record. Old decisions remain historical and never become current through a direct client patch.

## API, permission, audit, and integration-event alignment

No endpoint is added. `complete-triage` remains BackendService-only, takes an expected request version and references only current stored interpretation/duplicate evidence, selects policy server-side, and returns routing-decision ID, policy identity, status, priority, queue, review codes, and updated request version. `complete-human-review` uses the bounded body above and returns the new routing-decision ID and updated request summary. The documented API remains 21 mutation intents over 20 route templates and 13 query endpoints.

`OperationsAgent` remains prohibited from approval/rejection and terminalization. It may complete only a non-Urgent review. `ManagerApprover` and `Administrator` remain the urgent-review authorities; their proposal approval/rejection separation-of-duties constraints are unchanged. WorkflowService and EventPublisher receive no triage, review, category, priority, queue, or lifecycle authority.

Canonical audit facts use stable codes and minimal sanitized metadata:

| Audit event | Required safe evidence |
| --- | --- |
| `routing_decision.created` | Decision ID/version, policy ID/version/digest, source, category, priority, status, queue, and ordered reason codes. |
| `duplicate_candidate.created` | Candidate ID/type, score tier, policy identity, and stable duplicate reason codes; no full PII evidence. |
| `service_request.human_review_required` | Status, queue, review reason codes, and routing-decision ID. |
| `service_request.duplicate_review_required` | Status, queue, candidate IDs, and routing-decision ID. |
| `reviewed_facts.recorded` | Reviewed-fact set ID, actor UUID, allowed fact names, addressed codes, and rationale reference. |
| `routing_decision.recalculated` | New/prior decision IDs, policy identity, source, and safe changed-output summary. |
| `service_request.human_review_completed` | Request ID, status, queue, decision ID, and actor UUID. |
| `service_request.human_review_incomplete` | Request ID, outstanding stable codes, and reviewed-fact set ID if recorded. |
| `service_request.queue_changed` | Old/new queue wire values, reason codes, actor, and correlation ID. Policy-caused changes include policy ID/version/digest; lifecycle-only changes retain their lifecycle reason and do not fabricate a routing decision or policy cause. |

PII-minimized integration events are emitted only for consumer-relevant request changes: `service_request.triage_completed`, `service_request.human_review_required`, `service_request.duplicate_review_required`, `service_request.ready_for_action`, and `service_request.queue_changed`. They include policy ID/version and routing-decision ID where useful, but never full decision-input snapshots, raw descriptions, unmasked duplicate evidence, or reviewed rationale. An incomplete reviewed-fact recalculation that leaves consumer-facing status/queue unchanged emits no integration event solely for the new internal decision.

## Atomic transaction patterns

All patterns use short transactions, row locks for the request and current evidence where necessary, optimistic request-version checks, uniqueness constraints, one command-idempotency result, append-only audit events, and transactional outbox writes. A command key replay returns its stored logical response before evaluating a later expected version.

| Pattern | Rows read or locked and guards | Writes, audit/outbox, and commit result | Conflict or rollback |
| --- | --- | --- | --- |
| 1. Initial triage with no review | Lock request in `TriagePending`; lock current valid interpretation and current candidate observations; require expected request version and active policy selection. | Insert eligible candidates and decision; update request to `ReadyForAction` plus `StandardRequests` or `PriorityRequests`; write `routing_decision.created`, `service_request.triage_completed`, optional queue audit, matching outbox, and command result. | Version, interpretation, policy, or uniqueness failure rolls back every row. |
| 2. Triage producing `HumanReview` | Same reads/locks; calculate priority and ordered review triggers with no pending material duplicate. | Insert decision; update request to `HumanReview` and `HumanReview`; audit triage, human-review-required, queue change; emit PII-minimized outbox events and command response. | Any guard failure leaves `TriagePending` unchanged. |
| 3. Triage producing `DuplicateReview` | Same reads/locks; generate or reuse current candidate observations; require a pending candidate score `>= 60`. | Insert decision; update request to `DuplicateReview`/`DuplicateReview`; audit candidate creation, triage, duplicate-review-required, queue change; outbox and command response commit together. | Candidate uniqueness or request-version race rolls back the complete result. |
| 4. Complete non-Urgent human review | Lock `HumanReview` request/current decision/current interpretation/current duplicate evidence; check expected request version and expected/current policy; require at least one allowlisted reviewed fact, rationale, and supporting evidence; authorize OperationsAgent only if current and recalculated priorities are non-Urgent. | Insert immutable reviewed-fact set and one complete recalculated decision; update current decision/category/priority/review summary/status/queue and increment request version; audit facts, recalculation, completion or incompletion, and queue change only when changed; store applicable PII-minimized outbox work and command result. | Unsupported fact, stale evidence/policy/version, pending duplicate, or authorization denial rolls back request, facts, decision, audit, command, and outbox together. |
| 5. Complete Urgent human review | Same as pattern 4; lock relevant reviewed safety facts; require ManagerApprover/Administrator and explicit urgent disposition or hard-signal correction evidence. | Insert facts and one complete decision; either remain `HumanReview`/`HumanReview` with a new current decision/version and complete outstanding codes, or move to `ReadyForAction` with `HumanReview` oversight queue when `ConfirmedAndActionable`; commit audit, applicable outbox, and command result atomically. | OperationsAgent denial, missing rationale/evidence, stale state, or policy conflict rolls back every write. |
| 6. Incomplete accepted human review | Lock current `HumanReview` request, current decision/interpretation/duplicate evidence, and policy; validate expected version and at least one accepted reviewed fact. | Insert `ReviewedFactSet`; rerun policy; insert complete decision with `review_required=true`; update current decision/category/priority/review summary and request version while status/queue remain `HumanReview`; audit facts, recalculation, and `human_review_incomplete`; emit no integration event when no consumer-facing status/queue outcome changed; store command result with decision ID/version/outstanding codes. | Stale version, invalid fact, or any write failure rolls back state, facts, decision, audit, command result, version increment, and outbox work. Concurrent old-version submission conflicts. |
| 7. Duplicate resolution then recalculation | `ResolveDuplicate` locks request and candidate, checks expected version and authorized disposition. A `NotDuplicate` update leaves the request `TriagePending` with queue cleared once all material candidates are resolved; a later `CompleteTriage` locks the new evidence and policy. | Resolution transaction audits the immutable disposition and state/queue change. The separate recalculation transaction inserts a new decision, updates current request summary, audits/outboxes it, and stores its command result. | A concurrent candidate resolution, stale request, or conflicting command leaves the prior state; neither command partly applies. |
| 8. Policy-version change for later decisions | Policy activation is controlled outside ordinary request commands. A later triage/review locks the selected active immutable policy record and checks client expected policy version when supplied. | Existing decision is untouched. A later calculation writes a new decision with new policy identity and prior decision ID. | An activation/read race returns `POLICY_VERSION_CONFLICT`; no silent recalculation occurs. |
| 9. Concurrent triage commands | Both read the same `TriagePending` request; only one obtains a matching request version/row lock and candidate uniqueness set. | Winner inserts candidates/decision, updates summary, audits/outboxes, completes its command record. | Loser receives an optimistic conflict or replay result and writes no second current decision. |
| 10. Concurrent human-review completions | Both lock/read the same review request and expected current decision; reviewed-fact and decision versions are guarded. | Winner persists facts and one recalculated decision/current summary. | Loser gets a version conflict or its command replay; no facts or routing output are silently overwritten. |

## Approved demo-scenario mapping

All examples use policy `general-service-demo@1.0.0` revision `1` and record policy digest plus input hash in the resulting decision.

| Scenario | Normalized inputs | Category and priority | Review reasons | Status and queue | Routing-decision evidence |
| --- | --- | --- | --- | --- | --- |
| Valid standard request | Explicit `RoutineMaintenance`; asset/context, contact, timing, location, and access present; flexible date 28 days away; no impact, interruption, damage, or safety signal; confidence 0.88; no candidate >= 60. | `RoutineMaintenance`, `Low`. | None. | `ReadyForAction`, `StandardRequests`. | Initial decision with `CATEGORY_EXPLICIT_SELECTION_ACCEPTED`, `PRIORITY_FLEXIBLE_ROUTINE_WORK`, policy identity, input hash. |
| High-priority request | Explicit `Repair`; symptom/location/timing present; active interruption; deadline 48 hours away; confidence 0.91; no material candidate. | `Repair`, `High`. | None. | `ReadyForAction`, `PriorityRequests`. | Initial decision with `PRIORITY_ACTIVE_INTERRUPTION` and exact deadline evidence. |
| Urgent request | Explicit `Repair`; repair facts present; active interruption; deadline exactly 24 hours away; confidence 0.90; no material candidate. | `Repair`, `Urgent`. | `REVIEW_URGENT_PRIORITY`. | `HumanReview`, `HumanReview`. | Initial decision includes `PRIORITY_ACTIVE_INTERRUPTION_IMMEDIATE`, policy identity, and review gate. |
| Missing-information case | Explicit `Installation`; contact/location/timing present; installation target absent; no high-impact signal; confidence 0.83; no material candidate. | `Installation`, `Normal`. | `REVIEW_MISSING_REQUIRED_INFORMATION`; `MISSING_INSTALLATION_TARGET`. | `HumanReview`, `HumanReview`. | Initial decision records missing code and current interpretation version. |
| Low-confidence AI result | Normalized `Inspection` evidence complete; deadline 10 days; no impact; confidence 0.74; no material candidate. | `Inspection`, `Normal`. | `REVIEW_LOW_AI_CONFIDENCE`. | `HumanReview`, `HumanReview`. | Initial decision records confidence 0.74 and threshold 0.75. |
| Possible duplicate | Explicit `Repair`; all required facts; same normalized email as a request inside lookback; score 70; no other review trigger. | `Repair`, `Normal`. | `REVIEW_POSSIBLE_DUPLICATE`. | `DuplicateReview`, `DuplicateReview`. | Initial decision retains the candidate observation ID, score tier, and `DUPLICATE_EXACT_EMAIL`. |

## Executable test requirements

The Phase 2 evaluator and PostgreSQL suites exercise the ordered rules, stable identities, migration/schema constraints, atomic services, command guards, and redaction boundaries. The following list remains the required boundary coverage for this policy as the surrounding lifecycle grows:

- Every stable category and category-specific required-information row.
- Every priority level and every timing boundary: immediately below, equal to, and immediately above 24 hours, 72 hours, and 21 days.
- Confidence immediately below, equal to, and above 0.75.
- Missing required versus optional information; explicit category versus AI disagreement; AI-only category; and `OtherCustomRequest` with and without reviewed scope confirmation.
- Duplicate score immediately below, equal to, and above 40 and 60; lookback immediately inside, exactly at, and immediately outside 90 days; similarity immediately below, equal to, and above 0.80; timing proximity immediately below, equal to, and above 14 days; candidate uniqueness and ordering.
- Pending duplicate precedence over all Human-review triggers; Urgent precedence over nonduplicate review triggers; High and Low/Normal queue mapping.
- Deterministic repeatability from identical canonical inputs and policy version; policy-version reproducibility; and later-policy calculations retaining prior decision identity.
- Stale interpretation and stale request-version rejection.
- Non-Urgent review completed by OperationsAgent; Urgent review denied to OperationsAgent; Urgent review completed by ManagerApprover; and hard-signal de-escalation requiring manager/admin authority, reviewed correction, evidence, and rationale.
- Review unable to suppress a duplicate candidate, bypass required information, directly approve a proposal, or submit a generic routing/status/queue/priority patch.
- Concurrent triage and concurrent human-review completion conflicts.
- Incomplete accepted review creates one complete new routing decision.
- Incomplete accepted review increments request version even when status/queue remain `HumanReview`.
- Incomplete accepted review changes `current_routing_decision_id` to the new decision.
- Incomplete accepted review records the complete outstanding reason-code set with `review_required=true`.
- Two concurrent incomplete reviews using the same request version produce one success and one concurrency conflict.
- Atomic rollback together for request state/version, reviewed facts, routing decision, audit record, command-idempotency result, and applicable outbox message.

## Deferred and non-goal boundaries

This policy does not prescribe provider-failure taxonomy or recovery mechanics; those are separately defined by the [failure and recovery policy](failure-and-recovery-policy.md) without redefining deterministic routing or human-review authority. Migration `0010_deterministic_triage_foundation` and its SQLAlchemy models implement only this policy's current persistence boundary. Policy-management UI/API behavior, outbox publication, outbound adapters, and frontend behavior remain outside this policy. The outbound integration remains a proposed mock email adapter that must never be represented as sending real email.
