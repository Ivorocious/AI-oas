# Project Status

## Current phase

**Phase 2 — Executable Foundation: underway.**

Phase 0 product definition and Phase 1 technical design are complete. Phase 2 has begun with a validated, runnable FastAPI foundation. No domain implementation task is currently active. Remaining detailed decisions continue to be resolved incrementally within focused implementation tasks.

## Completed work

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

None. Human authentication and the protected service-request detail query are complete.

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
- Concrete n8n workflows, event transport, callback implementation, and event-publisher retry/dead-letter policy
- Provider-specific AI and outbound adapter mechanics, timeouts, validation, and contract-test strategy
- Executable test architecture and scenario fixtures for every invariant and demo path
- Deployment, environment, observability, and recovery design

## Known limitations

- The implemented backend includes atomic public intake, eight-table PostgreSQL persistence, asymmetric human authentication, and one protected service-request detail query. No AI interpretation, triage, machine authentication, n8n, publisher, proposals/approvals, outbound integration, frontend, or deployment exists.
- Intake/query API behavior and eight-table SQL migrations are executable; remaining contract schemas, persistence representations, hosted Supabase setup, secret storage, and deployed database enforcement are not yet implemented.
- The demonstration policies define triage thresholds, failure taxonomy, retry budgets and delays, stale assessment, and uncertain-outcome reconciliation, but none is implemented. Real-world calibration remains deferred.
- No real email is sent; only a proposed mock adapter is approved for the MVP.
- The design targets one demonstration organization, one primary intake path, and modest operational scale.
- Billing, payments, multi-tenancy, mobile apps, full CRM behavior, autonomous communication, large-scale analytics, numerous real integrations, enterprise authentication, microservices, and Kubernetes remain outside scope.

## Next milestone

**Phase 2 — AI interpretation persistence and attempt foundation.**

Establish the smallest immutable AI-interpretation, logical-operation, attempt, and callback-credential persistence needed for a later start/callback flow, without implementing deterministic triage.
