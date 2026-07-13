# Project Status

## Current phase

**Phase 2 — Executable Foundation: underway.**

Phase 0 product definition and Phase 1 technical design are complete. Phase 2 now has a runnable foundation, atomic intake, the complete bounded AI-attempt execution/recovery lifecycle, deterministic triage/duplicate/human review, and proposal approval. Remaining detailed decisions continue to be resolved incrementally within focused implementation tasks.

## Completed work

- Implemented migration `0011_proposal_approval_foundation`, bringing the application inventory to 26 tables. It adds the four approved proposal, contributor, frozen-exclusion, and exact-decision tables; generalizes logical operations without weakening AI constraints; and adds the request's same-request active-proposal reference.
- Implemented six human-authenticated proposal commands for draft creation/editing, submission, approval, rejection, and material revision. The lifecycle preserves one series-owned outbound logical operation, deterministic closed-payload digests, immutable contributor carry-forward, frozen actor-UUID self-approval exclusions, exact decision binding, optimistic versions, command idempotency, and transactional audit/outbox evidence.

- Implemented migration `0010_deterministic_triage_foundation`, bringing the application inventory to 22 tables. It adds immutable `decision_policy_versions`, append-oriented `duplicate_candidates`, immutable `reviewed_fact_sets`, immutable `routing_decisions`, and ordered routing-decision/candidate links, with current routing/review summary fields on service requests. The migration seeds `general-service-demo@1.0.0` revision 1 with a canonical digest and restrictive identity references.
- Implemented the complete ordered deterministic evaluator over allowlisted normalized facts, advisory interpretation evidence, duplicate evidence, reviewed facts, immutable policy content, and an explicit UTC evaluation instant. It reproduces category, priority, candidate scores, review precedence, status, queue, reason codes, and canonical input identity without letting AI output become canonical policy.
- Implemented trusted in-process BackendService `CompleteTriage` with no public complete-triage route. It selects and validates the immutable active policy using PostgreSQL time, evaluates current evidence, creates candidate observations and a complete immutable routing decision, updates the request summary/version, and commits audit, outbox, and command-idempotency evidence atomically.
- Implemented human-authenticated duplicate resolution and complete-human-review commands. Duplicate candidates remain separate evidence and never auto-merge records; confirmed duplicates close only through the explicit command. Human review stores bounded immutable fact sets and recalculates the complete decision, with OperationsAgent limited to non-Urgent results and ManagerApprover/Administrator authority required for Urgent or hard safety/continuity correction.
- Implemented and validated the three production AI result callbacks. WorkflowService HMAC, committed nonce protection, exact attempt-scoped credential proof, command idempotency, expected attempt version, frozen operation identity, and caller-owned transactions protect success, retryable-failure, and terminal-failure evidence. Success consumes the credential, closes the exact operation, inserts one immutable advisory interpretation, and updates only the request interpretation reference/version. Exact replay works with the consumed credential's durable authorization binding; a new key cannot reuse it.
- Implemented and validated immutable failure-recovery policy persistence in migration `0009_failure_recovery_foundation`, bringing the application inventory to 17 tables. The seeded deployment-controlled policy has exact identity/digest evidence, all 26 approved stable codes, three-attempt AI/outbound budgets, deterministic delays, stale thresholds, and reconciliation rules. Attempt assessments and request recovery summaries use named constraints and restrictive policy/attempt references.
- Implemented backend-derived AI retryable, exhaustion, and terminal assessment with PostgreSQL UTC time, safe evidence hashes/references, policy identity, remaining budget, and retry eligibility. The retry-AI command appends the next `Pending` attempt under the same logical operation at exact eligibility, never resets budget, and never gives callback plaintext to a human caller. Manager/administrator terminal disposition requires rationale; OperationsAgent remains denied.
- Implemented assigned-WorkflowService callback-credential replacement for unexpired `Pending` or `Running` attempts. The command replaces exactly one active version, preserves scope and fixed deadline, records old-credential authorization plus new-secret delivery metadata, creates security audit evidence without a lifecycle outbox event, and returns plaintext once. Trusted non-HTTP BackendService stale assessment handles exact two-minute Pending and five-minute Running AI boundaries and revokes the active callback credential.
- Implemented and validated migration `0008_callback_command_authorization_binding`, separating the exact callback credential used to authorize a command from one-time callback plaintext delivery metadata. Command completion/replay supports authorization-only, delivery-only, neither, or both independently with restrictive foreign keys, positive-version constraints, Processing-state exclusion, rollback safety, and no response-snapshot inference. No callback route or result processing was added.
- Implemented and validated reusable attempt-scoped callback authentication: strict single-header extraction, caller-owned explicit-transaction enforcement, exact session/transaction-bound immutable context, locked attempt/operation/request/credential verification, assignment concealment, frozen adapter and ownership guards, PostgreSQL expiry, append-oriented credential history, and constant-time hash proof without a supplied-hash SQL predicate. Test-only HTTP composition confirms HMAC/nonce ordering; no production callback route or mutation was added.
- Implemented and validated `POST /api/v1/integration-attempts/{attempt_id}/commands/start`: an HMAC-authenticated assigned WorkflowService resolves command idempotency before domain reads, locks the attempt/operation/request/callback context, validates owner input and expiry, moves only `Pending → Running` with PostgreSQL time, and commits one audit/outbox/idempotency result atomically. It returns no credential and invokes no provider.
- Implemented and validated the first production WorkflowService business command, `POST /api/v1/service-requests/{request_id}/commands/start-ai-interpretation`. It authenticates HMAC and commits the nonce, resolves command idempotency before domain reads, locks and version-checks the `TriagePending` request, creates one AI logical operation and `Pending` attempt, persists only the one-time callback credential hash, increments the request version, and commits safe audit/outbox/idempotency evidence atomically before returning plaintext once. Exact replay returns `AlreadyIssued` without plaintext; no AI provider is invoked.
- Implemented and validated the reusable non-intake command-idempotency foundation: migration `0007_command_idempotency_foundation`, trusted full-scope reservations, strict key digesting, canonical validated-body binding, savepoint race resolution, safe completion/replay/conflict behavior, and non-secret one-time-delivery receipt metadata. No production command was added.
- Implemented and validated reusable WorkflowService HMAC-SHA256 verification, application-controlled machine identity/external credential metadata, current/previous rotation overlap, and committed nonce replay protection. No production machine route or command was added.
- Implemented and validated the AI-only execution persistence foundation: immutable logical-operation intent, bounded integration attempts, hash-only callback-credential metadata, immutable interpretations, and the nullable service-request current-interpretation reference. No runtime AI command or callback was added.
- Implemented asymmetric Supabase-compatible human bearer-token verification with lazy bounded JWKS caching, per-request active actor/current-role resolution, and centralized fixed-role authorization. Added Alembic revision `0003_human_access_foundation` for two human-access tables, bringing the application inventory to eight tables.
- Implemented and validated protected read-only `GET /api/v1/service-requests/{request_id}` for all three approved human roles. It resolves the public-intake `Location` to a closed request/contact projection without writes; the UUID alone grants no access.
- Implemented and validated atomic public `POST /api/v1/intake/service-requests`: controlled transport/body parsing, closed normalized schemas, canonical SHA-256 hashing, reservation-first concurrency, new/replay/conflict/invalid/malformed outcomes, complete domain/audit/outbox transactions, safe errors, and PII-minimized events. Alembic revision `0002_atomic_intake_constraints` narrowly defers only the circular reservation foreign keys while keeping scope/key uniqueness immediate.
- Established the accepted-intake persistence foundation: explicit synchronous SQLAlchemy engine/session construction, typed `AI_OPS_DATABASE_URL`, a pinned local PostgreSQL 17 Compose service, deterministic metadata, Alembic revision `0001_intake_persistence`, and exactly six structural tables for delivery, reservation, contact, request, audit, and outbox evidence. Real PostgreSQL tests validate migration round trips, uniqueness, atomic rollback, timezone-aware timestamps, and restrictive evidence deletion.
- Established the first Phase 2 executable foundation under [`backend/`](../backend/README.md): a FastAPI application factory, typed project-prefixed nonsecret settings, `GET /health`, pytest and Ruff foundations, a reproducible `uv.lock`, and local PowerShell setup/start/check instructions. Validation passes on Python 3.12 without database, network-service, credential, or running-server dependencies.
- Defined immutable failure-policy versions, structured evidence and stable failure codes, 16 canonical audit reason codes, backend-owned retry eligibility, exact three-attempt AI/outbound budgets, AI delays of 30 seconds and 2 minutes, outbound delays of 1 minute and 5 minutes, proposal-revision behavior, callback replay, stale-attempt assessment, and the 15-minute uncertain-outcome reconciliation deadline in the approved [failure and recovery policy](failure-and-recovery-policy.md) and [ADR 0006](decisions/0006-failure-retry-and-reconciliation-policy.md).

- Defined the Phase 0 business problem, scope, users, lifecycle, approval rules, non-goals, success criteria, risks, and 12 demo scenarios in the [product brief](product-brief.md).
- Recorded the proposed component responsibilities in the [architecture](architecture.md).
- Defined all required records, ownership boundaries, relationships, sensitive-data considerations, authorities, versioning expectations, and transaction boundaries in the [domain model](domain-model.md).
- Defined inbound-delivery, service-request, proposed-action, and integration-attempt lifecycle states, guards, authorities, audit events, failures, queues, invariants, and recovery behavior in the [state-machine design](state-machines.md).
- Aligned every permitted material proposal revision with an atomic parent-request transition so no request remains executable, awaiting approval, or retryable for a superseded proposal.
- Defined `/api/v1` command/query boundaries, intake outcomes, expected-version and error semantics, command idempotency, guarded n8n callbacks, and read models in the [API contracts](api-contracts.md).
- Defined PII-minimized event envelopes, at-least-once delivery, aggregate-version ordering, consumer deduplication, transactional-outbox compatibility, and n8n authority limits in the [event contracts](event-contracts.md).
- Defined Supabase human authentication, fixed human/machine roles, HMAC workflow authentication, attempt-scoped callbacks, separation of duties, all endpoint permissions, field access, auth errors, and security auditing in [authentication and authorization](authentication-and-authorization.md).
- Defined 27 proposed persistence representations, lifecycle constraints, accepted-intake/command/nonce/callback/outbound idempotency, immutable decision and failure-recovery policy evidence, reviewed-fact evidence, normalized approval attribution, series-owned outbound operations with exact attempt bindings, callback-credential loss recovery, atomic command patterns, canonical audit storage, transactional outbox publication, retention, migration ordering, and future persistence tests in the [persistence design](persistence-design.md).
- Defined stable category wire values, an immutable policy-version model, concrete demonstration thresholds, deterministic category/priority/duplicate/review precedence, bounded reviewed-fact correction, immutable routing decisions, policy-aligned audit/events, atomic triage/review patterns, demo mappings, and future test requirements in the [deterministic triage policy](deterministic-decision-policy.md).
- Mapped all 12 approved demo scenarios to starting states, commands, guards, final states, queues, audit evidence, and outbound-attempt expectations.
- Accepted [ADR 0001](decisions/0001-canonical-state-and-lifecycle-boundaries.md) for canonical state and lifecycle boundaries.
- Accepted [ADR 0002](decisions/0002-api-command-and-event-boundaries.md) for HTTP commands, events, and orchestration boundaries.
- Accepted [ADR 0003](decisions/0003-authentication-and-role-permissions.md) for identity, machine authentication, fixed roles, and self-approval prevention.
- Accepted [ADR 0004](decisions/0004-postgres-persistence-and-transactional-outbox.md) for canonical Postgres persistence, distinct replay protection, immutable attribution, transactional audit/outbox writes, and at-least-once publication.
- Accepted [ADR 0005](decisions/0005-deterministic-triage-and-review-policy.md) for deterministic backend triage, advisory AI, duplicate candidates, bounded reviewed facts, Urgent review authority, and immutable policy/routing-decision versions.
- Accepted [ADR 0006](decisions/0006-failure-retry-and-reconciliation-policy.md) for immutable failure policy, bounded retry, callback replay, stale-attempt assessment, and uncertain-outcome reconciliation.
- Preserved the requirement that the outbound provider is mock-only and sends no real email.

## Active task

None. Batch 2 deterministic triage and review passed its full acceptance gate; checkpoint publication is the remaining administrative step.

## Blockers

There is no approved design blocker preventing the next focused Phase 2 task. Implementation-time details remain intentionally incremental and are not all decided in advance.

## Approved decisions

- Supabase Postgres is the proposed canonical operational store.
- Authorized FastAPI backend commands exclusively control canonical lifecycle transitions; the frontend, n8n, AI providers, and outbound adapters provide intent or evidence only.
- Invalid deliveries remain separate inspectable `InboundDelivery` records and do not become normal `ServiceRequest` records.
- Request status, priority, operational queue, proposed-action state, approval, and integration-attempt state are separate concepts.
- AI interpretations and deterministic routing decisions are immutable, versioned evidence with applicable prompt, schema, provider, and policy references.
- The deterministic triage policy is immutable and versioned; final category, priority, duplicate candidates, review outcome, status, queue, and reason codes are reproducible from allowlisted facts and recorded policy identity.
- AI is advisory only; it cannot directly set category, priority, queue, state, approval, or duplicate resolution.
- The six stable category wire values are `Consultation`, `Installation`, `Repair`, `RoutineMaintenance`, `Inspection`, and `OtherCustomRequest`.
- Pending material duplicate candidates route to `DuplicateReview`; duplicates never auto-merge or auto-close a request.
- `complete-human-review` requires at least one bounded reviewed fact and always creates a complete new routing decision, updates the current request summary/reference and optimistic version even when review remains incomplete, and commits all evidence atomically. OperationsAgent is limited to current and recalculated non-Urgent review; manager/admin authority is required for Urgent and hard-signal correction.
- Approval binds to one exact proposed-action ID, version, and payload digest; material revision requires a new version and approval.
- Material revision atomically activates a replacement draft, moves the request to `ActionRevisionRequired` in `HumanReview`, clears obsolete execution recovery state, and preserves prior approvals and attempts as historical evidence without letting them authorize the replacement.
- Intake and outbound idempotency have independent identities and guards.
- Retrying outbound work creates a new attempt for the same logical operation; a successful logical operation cannot execute again.
- Important backend-controlled state transitions and audit events are transactionally consistent and append-oriented.
- Mutable aggregates use optimistic version checks or equivalent atomic conflict protection.
- Domain IDs use UUIDs, timestamps use UTC, and operational/audit records are not hard-deleted in the MVP.
- The proposed API prefix is `/api/v1`; meaningful commands mutate state, queries are read-only, and generic category/status/queue/priority patch endpoints are prohibited.
- Mutable commands carry expected aggregate versions; stale versions return `409 CONCURRENCY_CONFLICT`, and business guards return specific stable `409` codes.
- Integration events use at-least-once delivery with UUID event deduplication and aggregate-version ordering; audit events remain separate canonical evidence.
- n8n can request guarded commands and report evidence only for backend-created attempts; it cannot submit authoritative lifecycle decisions.
- Human authentication uses Supabase Auth tokens validated by FastAPI, while application-controlled Postgres roles are loaded for every protected request.
- Fixed human roles are `OperationsAgent`, `ManagerApprover`, and `Administrator`; fixed machine identities are `BackendService`, `WorkflowService`, and `EventPublisher`.
- WorkflowService uses HMAC-SHA256 request authentication, and result callbacks also require an opaque credential scoped to one backend-created attempt.
- Operations agents cannot approve/reject or complete Urgent review; no manager or administrator may approve/reject a proposal they created or materially revised.
- Accepted public intake has a dedicated reservation record; authenticated commands, machine nonces, callback credentials, and outbound side effects use separate replay/idempotency scopes.
- Proposal contributors and frozen approval exclusions are normalized immutable records; logical operations own one-way attempts, retries append new rows, and at most one attempt is active or successful.
- One outbound logical operation is created with a proposal series and retained by every revision; each attempt stores its exact proposal/digest/approval/adapter/key authorization, and any success blocks the whole operation.
- Callback plaintext is hash-only at rest and issued once. Lost delivery is recovered through an exact-attempt WorkflowService replacement command with expected credential version, one active credential, and non-secret idempotent replay.
- Canonical state, required audit evidence, and immutable outbox messages commit atomically in Postgres; EventPublisher uses leased at-least-once publication and durable one-way attempt history.
- Postgres enforces structural integrity/uniqueness/basic combinations, while FastAPI retains authorization, lifecycle policy, exact approval, retry eligibility, canonical hashing, redaction, and audit/outbox selection.
- `FailureRecoveryPolicyVersion` is immutable and deployment-controlled.
- AI and outbound logical operations have at most three attempts; manual or service retries cannot reset budgets or bypass delays.
- Material revision does not reset an outbound logical-operation budget.
- Unknown outbound side effects require reconciliation and cannot be blindly retried.
- Callback replay is not provider retry and creates no new attempt.
- Manager/administrator terminal disposition requires rationale; OperationsAgent cannot terminalize.
- Outbox publisher retry and dead-letter policy remains deferred.

## Implementation-time decisions and later milestones

The following matters will be resolved incrementally within focused Phase 2 and later tasks:

- Exact OpenAPI component schemas, field constraints, generated examples, and contract-test fixtures for unimplemented routes
- Hosted Supabase project configuration, controlled demo-user setup, and remaining security contract tests
- Exact SQL types/migrations, encryption, canonical hash specifications, database roles, physical indexes, retention durations, archival jobs, backup/restore, and recovery operations
- Real-world calibration and governance of demonstration policy thresholds and controlled activation
- Final audit event field schemas, redaction projections, and approved retention durations
- Concrete n8n workflows, event transport, and event-publisher retry/dead-letter policy
- Provider-specific AI and outbound adapter mechanics, timeouts, validation, and contract-test strategy
- Executable test architecture and scenario fixtures for every invariant and demo path
- Deployment, environment, observability, and recovery design

## Known limitations

- The backend includes atomic intake, human authentication, protected request detail, 26-table persistence, WorkflowService HMAC/nonce authentication, command idempotency, the complete bounded AI attempt lifecycle, deterministic triage/review, and proposal approval. `CompleteTriage` remains trusted in-process functionality. No outbound attempt, outbound callback credential, provider invocation, email execution, real integration, n8n workflow, publisher, frontend, or deployment exists.
- Start AI generates one callback plaintext value in memory and issues it only after commit; only its SHA-256 hash and safe metadata are stored. No provider request/response body or real AI provider credential is created or stored.
- The immutable demonstration failure policy, AI assessment/retry delays, AI stale boundaries, deterministic decision policy, and proposal approval lifecycle are executable. Outbound execution/reconciliation and real-world policy calibration remain unimplemented.
- No real email is sent; only a proposed mock adapter is approved for the MVP.
- The design targets one demonstration organization, one primary intake path, and modest operational scale.
- Billing, payments, multi-tenancy, mobile apps, full CRM behavior, autonomous communication, large-scale analytics, numerous real integrations, enterprise authentication, microservices, and Kubernetes remain outside scope.

## Next milestone

**Checkpoint 4 — Mock outbound execution and recovery.**

Reconcile the AI success-callback transport contract before generalizing callbacks to `OutboundAction`: the executable AI success request currently requires echoed prompt/provider/model/adapter identity beyond the shorter API-contract summary. Outbound start/callback/retry/reconciliation must preserve the exact approved proposal binding and must never send real email.
