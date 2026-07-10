# ADR 0004: Postgres Persistence and Transactional Outbox

- Status: Accepted
- Date: 2026-07-11
- Scope: MVP technical design

## Context

The approved lifecycle, API, event, and authorization contracts require durable enforcement for concurrency, replay protection, immutable approval attribution, provider attempts, audit evidence, and event publication. A conceptual domain model alone does not specify how concurrent intake, commands, callbacks, and publishers avoid duplicate or partial results.

The MVP targets one demonstration organization and uses Supabase Postgres as its canonical store. It needs credible relational and transactional boundaries without introducing application code, finalized SQL, microservices, or exactly-once delivery claims.

## Decision

1. Postgres is the canonical persistence and transaction boundary for operational state, security metadata, idempotency records, canonical audit evidence, and the transactional outbox.
2. Trusted application processes generate collision-safe UUIDv4 row/event identities; Postgres generates canonical UTC timestamps and enforces primary-key uniqueness.
3. Mutable aggregate roots use optimistic integer versions. FastAPI checks expected versions and performs atomic compare-and-update; Postgres enforces relational, uniqueness, and basic valid-combination constraints.
4. Accepted intake uses a dedicated `accepted_intake_keys` reservation, unique by source/channel scope and key digest. Only a valid new acceptance reserves it. Invalid deliveries remain evidence without blocking later corrected use.
5. Intake replay, non-intake command replay, machine nonce replay, attempt callback authorization/replacement, and outbound provider-side-effect idempotency are separate mechanisms with separate scopes and records.
6. Non-intake commands store actor/intent/route/target/key scope, canonical body hash, and safe original result in `command_idempotency_records`. The record commits atomically with domain, audit, and outbox changes.
7. Machine requests persist a nonce digest only after sufficient timestamp/signature validation. Uniqueness spans a machine identity/environment across credential versions; secrets and raw signatures are never stored.
8. Callback credentials are opaque, hashed at rest, versioned, expiring, and bound to one attempt, operation kind, WorkflowService identity, and environment. Plaintext is returned once after commit and is never persisted in command responses. Exact replay returns only a safe receipt; an authorized expected-version replacement command recovers lost delivery by invalidating the old version and returning one new plaintext value.
9. Proposal creator/editor history and frozen approval exclusions use normalized immutable rows. Self-approval compares immutable actor UUIDs; role changes cannot rewrite exclusion history.
10. Each AI input/configuration intent has its own `logical_operations` row created when interpretation starts. Each outbound proposal series gets one durable operation when its first draft is created; all proposal revisions and retries retain it. Every outbound attempt freezes the exact proposal ID/version/digest, approval ID, adapter intent, and stable backend-generated key it executes. Partial unique indexes permit at most one active and one successful attempt, and success blocks later series revisions/attempts.
11. Every material command writes canonical state, required `audit_events`, and immutable `outbox_messages` in one transaction. Failure rolls back all three.
12. A separate EventPublisher claims leased outbox rows and records durable one-way publication-attempt history. Delivery is at least once; consumers deduplicate `event_id` and use aggregate version for ordering/gap behavior.
13. FastAPI owns authorization, lifecycle/domain policy, deterministic decisions, retry eligibility, hashing, self-approval/exact-proposal validation, redaction, and audit/outbox content. Postgres owns structural integrity, uniqueness, compare-and-change support, atomicity, and durability.
14. Browser clients and WorkflowService receive no direct canonical-table write access. FastAPI uses a server-only database identity; EventPublisher has a separate minimum-privilege outbox role. Supabase service-role credentials are never exposed to the browser.
15. Core domain, approval, attempt, and audit evidence is not hard-deleted in the MVP. Bounded security records such as expired nonces may be purged after their protection window under controlled retention policy.

The detailed proposed representation and transaction patterns are in the [persistence design](../persistence-design.md). This ADR and that document are technical design only; no migration or implementation exists.

## Alternatives considered

### One generic idempotency table for every concern

Rejected. Accepted intake, authenticated command replay, machine nonces, callback scope, and provider side effects have different authorities, conflict rules, lifetimes, and replay semantics. A shared table would blur security and business guarantees.

### Reserve intake keys for invalid deliveries

Rejected. It would prevent a customer from correcting an invalid request with the same key and would contradict the approved intake contract. Only accepted-new intake owns the durable reservation.

### Store proposal exclusions in JSON or an array

Rejected for the MVP. Normalized rows give foreign keys, uniqueness, direct membership checks, carried-forward provenance, and clearer immutable history.

### Store only integration attempts without logical operations

Rejected. Retry attempts need one stable AI input/configuration or outbound-side-effect identity, a stable outbound key, and database-enforceable active/success uniqueness across attempts.

### Bind an outbound operation to one exact proposal version

Rejected. Material revisions preserve the intended side effect and logical operation while requiring new exact approval. Exact execution authorization therefore belongs immutably on each attempt, not on the series-wide operation.

### Persist or reversibly encrypt callback plaintext for replay

Rejected. It would weaken the hash-only credential decision and expand secret exposure. A safe idempotent receipt plus guarded one-version replacement gives recoverability without storing recoverable plaintext.

### Publish events after the domain transaction

Rejected. A crash between state commit and event creation could permanently lose the notification. Domain state, audit evidence, and the outbox message must commit together.

### Treat successful transport publication as exactly once

Rejected. A publisher can crash after sending but before recording acknowledgment. At-least-once delivery plus consumer deduplication is honest and recoverable.

### Give n8n direct Postgres access

Rejected. It would bypass FastAPI authorization, state guards, idempotency, audit selection, and canonical transaction boundaries.

### Put all authorization in Postgres RLS

Rejected as the authoritative MVP model. Browser denial and database least privilege are useful defense in depth, but command permission, exact proposal checks, self-approval, and lifecycle policy remain FastAPI responsibilities and must execute coherently in multi-table transactions.

### Use broad serializable transactions or table locks for every command

Rejected. Targeted row locks, expected-version updates, deferred relationship checks, and unique/partial indexes provide narrower contention and clearer conflict behavior. Isolation may be chosen per pattern later.

## Consequences

### Positive

- Concurrent intake and commands have explicit serialization and replay outcomes.
- Database constraints backstop lifecycle and attempt invariants without moving policy out of FastAPI.
- Approval history preserves creator/editor provenance and separation of duties.
- Provider retries share one durable operation identity and cannot create a second success.
- Proposal revisions retain one outbound operation while each attempt remains explainable against its exact historical proposal and approval.
- Lost callback-credential delivery is recoverable without storing plaintext or allowing multiple active versions.
- Audit and integration notifications cannot be silently separated from committed state.
- Publisher crashes produce recoverable duplicate delivery rather than lost events.
- Machine replay and callback scope persist without storing reusable secrets.

### Costs and tradeoffs

- Multi-aggregate commands require careful lock ordering, deferred constraints, and transactional tests.
- Normalized attribution and separate replay-protection tables add joins and retention policies.
- Command response snapshots and sensitive hashes require restrictive projections and cleanup rules.
- Secret-bearing command replay is intentionally asymmetric: it confirms issuance but cannot reproduce plaintext, so callers must understand the replacement flow.
- Outbox publication needs leases, attempt history, monitoring, and consumer deduplication.
- Application-generated UUIDs and canonical hashes must be consistent across every producer.
- Append-oriented evidence increases storage and makes destructive rollback of later migrations unsafe.

### Follow-up work

- Finalize SQL types, names, encryption, canonical hash specifications, database roles, and migration tooling.
- Write migrations only after this design and later field/schema constraints are approved.
- Implement the listed concurrency, rollback, replay, and publisher-crash tests with the future database layer.
- Approve concrete retention windows, backup/restore, archival, and operational recovery procedures.

Future implementation may refine physical mechanics but cannot merge the distinct idempotency scopes, permit self-approval, mutate failed attempts back to pending, expose secrets, let n8n write canonical tables, or replace at-least-once outbox delivery with an unsupported exactly-once claim without a superseding ADR.
