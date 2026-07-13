# ADR 0005: Deterministic Triage and Review Policy

- Status: Accepted; implemented in Phase 2 Batch 2
- Date: 2026-07-11
- Decision makers: Project owner
- Related: [product brief](../product-brief.md), [domain model](../domain-model.md), [state machines](../state-machines.md), [API contracts](../api-contracts.md), [persistence design](../persistence-design.md), [ADR 0001](0001-canonical-state-and-lifecycle-boundaries.md), [ADR 0004](0004-postgres-persistence-and-transactional-outbox.md)

## Context

The service-request lifecycle already separates advisory AI interpretation from canonical request state, priority, queue, approval, and execution. It needed an explicit, reproducible policy for converting a successfully interpreted request into a final category, priority, duplicate-review outcome, lifecycle status, queue, and immutable `RoutingDecision` without permitting a client, AI provider, or workflow to patch those outputs.

The policy must be concrete enough for future boundary tests and demonstrations, while remaining configurable for service businesses and not pretending that the demonstration thresholds are universal safety or industry guidance.

## Decision

1. FastAPI is the authoritative evaluator of a single immutable decision-policy version. It selects canonical, allowlisted inputs and atomically writes the request summary, `RoutingDecision`, audit evidence, outbox messages, and command-idempotency result.
2. AI output is advisory. A validated AI summary, suggested category, missing-information list, confidence, or potential safety signal may corroborate evidence or require review; it cannot directly set final category, priority, queue, state, duplicate resolution, or approval.
3. The six initial stable category wire values are `Consultation`, `Installation`, `Repair`, `RoutineMaintenance`, `Inspection`, and `OtherCustomRequest`. Display labels may be human-friendly, but technical contracts use these stable values.
4. The MVP will retain an immutable `decision_policy_versions` configuration snapshot containing policy identity, semantic version/revision, digest, effective time/status, category/required-information rules, confidence threshold, priority rules, duplicate rules, review/queue mapping, and reason-code catalog. It is deployment-controlled; no configuration UI or generic policy API is introduced.
5. The initial configurable demonstration defaults are: AI confidence review below `0.75`; inclusive 90-day duplicate lookback; candidate retention at score `>= 40`; duplicate review at `>= 60`; normalized token-set similarity at `>= 0.80`; inclusive 14-day timing proximity; Urgent timing combination at `<= 24h`; High deadline at `<= 72h`; and Low flexible routine threshold at `>= 21d`. Equality behavior is explicit in the [policy](../deterministic-decision-policy.md#initial-configurable-demonstration-defaults).
6. Category is resolved in order from authorized reviewed correction, valid explicit selection without conflict, one normalized category fact set, then `OtherCustomRequest` for conflict, ambiguity, or unusable evidence. AI can never fill an evidence gap alone.
7. Priority precedence is `Urgent`, then `High`, then `Low`, then `Normal`. Urgent requires fact-backed critical safety/continuity, immediate active interruption, immediate rapid damage, or immediate severe impact; AI confidence does not affect priority. Missing information and duplicate evidence remain separate review outcomes.
8. Duplicate detection creates separately inspectable candidates only. Intake idempotency replay is not a candidate. Candidates never auto-merge contacts or auto-close requests; the existing dedicated resolution command remains required for a confirmed duplicate. Pending material candidates take precedence and route to `DuplicateReview`.
9. Review precedence is: safely unavailable routing evidence, pending material duplicate, Urgent, authoritative reported safety/continuity concern, missing required information, low AI confidence or AI possible safety signal, category ambiguity/conflict, another registered exception, then clear queue mapping. Clear High maps to `ReadyForAction`/`PriorityRequests`; clear Low/Normal maps to `ReadyForAction`/`StandardRequests`.
10. `complete-human-review` requires at least one bounded reviewed fact plus addressed codes, required rationale, and evidence references; the MVP has no note-only completion path. It does not patch state, queue, unrestricted priority, approval, duplicate resolution, retry eligibility, or arbitrary routing output. Every accepted submission creates a complete new deterministic decision, updates the request's current decision/category/priority/review summary/status/queue and optimistic version even when review remains required, and commits facts/state/decision/audit/command/applicable outbox atomically.
11. `OperationsAgent` may complete only a review whose current and recalculated priority are non-Urgent. `ManagerApprover` or `Administrator` is required when current evidence or recalculation is Urgent. Reducing or dismissing a hard safety/continuity fact additionally requires that authority, an explicit corrected fact, evidence, and rationale. This does not change the separate approver/self-approval policy.
12. Every `RoutingDecision` is immutable and retains policy ID/version/revision/digest, canonical input hash, evaluation time, current interpretation/candidate/reviewed-fact evidence, category/priority/status/queue, ordered reason codes, source, prior decision, and reviewed actor/rationale references when applicable. A new policy applies only to later guarded calculations; it does not silently rewrite old decisions.
13. Failure taxonomy, retry eligibility policy, retry counts, backoff, dead-letter thresholds, and uncertain-side-effect reconciliation remain a separate deferred task.

## Rationale

This keeps business handling reproducible when an AI model, prompt, or provider changes. It makes a policy's boundary conditions visible for portfolio demonstration and future tests, while preserving meaningful human oversight. It also prevents the common failure mode in which a workflow or UI "fixes" an exception by directly patching state and leaves no durable explanation of why the final result changed.

## Consequences

### Positive

- Identical canonical inputs and policy identity yield an explainable decision record.
- AI retains useful interpretation value without becoming an unreviewable authority.
- Human review corrects bounded facts and records an actor/rationale, rather than bypassing policy.
- Duplicate handling remains conservative: it informs review without silently merging customer records or closing work.
- The existing API surface, permission matrix, audit/outbox boundary, and optimistic concurrency model remain intact.

### Costs and tradeoffs

- The policy introduces configuration snapshots, reason-code maintenance, candidate evidence links, reviewed-fact evidence, and more explicit audit detail.
- Fixed demo thresholds will produce false positives and false negatives outside the demonstration context; they must be revisited for any real service domain.
- `OtherCustomRequest` deliberately creates review work until custom scope is confirmed; this favors safe classification over apparent automation.
- An Urgent request may require manager/admin review before action preparation and can retain the `HumanReview` queue for oversight even after its review gate is satisfied.
- A policy update is intentionally not retroactive, so operators can see historical decisions made under older rules.

## Rejected alternatives

### Let the AI category or urgency output become final

Rejected. Provider behavior and prompts can change, and AI claims cannot safely substitute for validated safety/continuity facts or deterministic policy. AI may recommend review but cannot own final outputs.

### Use generic request `PATCH` endpoints for category, priority, state, or queue

Rejected. It would bypass policy, lose reproducibility, permit unsafe self-service routing changes, and conflict with the established command/lifecycle boundary.

### Automatically merge or close likely duplicates

Rejected. The demonstration score is not proof of identity or intent. Candidates must remain inspectable and a confirmed duplicate requires the authorized resolution command.

### Put policy logic in n8n or the frontend

Rejected. Those surfaces may coordinate/display but cannot be the canonical, versioned, audited authority. They also cannot safely enforce permissions and transactional state changes.

### Make all human review a direct manager approval

Rejected. Operational fact clarification is distinct from proposal approval. Operations agents may handle bounded non-Urgent review, while Urgent and hard safety/continuity authority remains appropriately restricted.

### Make policy updates retroactively rewrite current requests

Rejected for the MVP. Silent recalculation would make earlier decisions difficult to explain and could unexpectedly move work. A future explicit bulk-retriage design would need separate authorization, audit, and operational controls.

## Implementation status

Migration `0010_deterministic_triage_foundation`, the SQLAlchemy models, immutable demonstration-policy seed, pure ordered evaluator, trusted in-process `CompleteTriage` service, public duplicate-resolution command, and public complete-human-review command implement this decision. `CompleteTriage` has no public route, and the human commands retain authentication, role, expected-version, idempotency, atomic audit/outbox, and immutable-evidence boundaries. Policy-management APIs/UI, proposals and approval, outbound execution, provider adapters, and frontend behavior remain unimplemented. The proposed mock email adapter remains mock-only and must not be represented as real email delivery.
