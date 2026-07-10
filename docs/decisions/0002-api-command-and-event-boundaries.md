# ADR 0002: API Command and Event Boundaries

- Status: Accepted
- Date: 2026-07-10
- Scope: MVP technical design

## Context

ADR 0001 established FastAPI and Postgres as the canonical lifecycle boundary and store. The MVP still needs contracts that let the frontend and n8n request work without introducing generic state mutation, lost updates, duplicate side effects, or a second source of truth.

HTTP retries and at-least-once event delivery are normal. Human actions can race workflow callbacks, and multiple aggregates may change in one lifecycle command. Audit evidence must remain canonical while integration messages need independent delivery and retry behavior. Customer PII should not be copied through event envelopes.

## Decision

1. The proposed HTTP API uses `/api/v1` and separates read-only queries from meaningful lifecycle commands.
2. FastAPI is the sole authoritative command boundary. There are no generic request status, queue, or priority patch endpoints.
3. Commands include expected versions for every caller-visible mutable aggregate they change. Any mismatch returns `409 CONCURRENCY_CONFLICT` without partial mutation.
4. Business-state guard failures return `409` with a specific stable error code. Transport, authorization, not-found, and validation failures use the common error envelope.
5. Every mutation command uses scoped command idempotency. Intake retains its independent accepted-key and canonical-payload-hash semantics.
6. The backend creates provider attempts, logical operations, and outbound idempotency keys. n8n and adapters can report evidence only for a backend-created attempt.
7. Duplicate identical commands and callbacks return the existing result. Contradictory key reuse or terminal callback results return explicit conflicts.
8. Integration events use a versioned, PII-minimized envelope with UUID `event_id`, correlation/causation IDs, aggregate identity/version, UTC occurrence time, and minimal data.
9. Event delivery is at least once. Consumers deduplicate by `event_id`; ordering is guaranteed only through aggregate version, not globally.
10. State, canonical `AuditEvent` evidence, and future outbox messages are written transactionally. Audit records and integration messages remain separate records with different responsibilities.
11. n8n consumes events and invokes guarded API commands/callbacks. It never writes canonical tables or supplies final state, priority, queue, routing, approval, or retry decisions.
12. `/api/v1`, body schemas, and event schemas evolve compatibly; breaking changes require explicit versioning.

The detailed contracts are defined in the [API contracts](../api-contracts.md) and [event/n8n contracts](../event-contracts.md).

## Alternatives considered

### Generic CRUD and status patch endpoints

Rejected. `PATCH` access to lifecycle fields would bypass transition guards, cross-aggregate atomicity, audit requirements, and deterministic policy.

### Direct n8n access to Postgres

Rejected. Workflow SQL or table updates would create a second command path without consistent authorization, expected-version checks, idempotency, or domain auditing.

### Provider callbacks that submit desired lifecycle state

Rejected. Providers and n8n return evidence, not decisions. The backend maps validated result evidence through the approved state machines.

### Callbacks that create attempts implicitly

Rejected. A callback must identify a backend-created attempt so approval, operation identity, concurrency, and duplicate-side-effect guards are established before provider invocation.

### Exactly-once event delivery

Rejected as an unsupported guarantee. At-least-once delivery with stable event IDs, idempotent consumers, and aggregate versions is explicit and testable.

### Use audit records directly as delivery messages

Rejected. Audit events are canonical operational evidence; delivery messages require publication status, retries, consumer-specific handling, schema minimization, and possible expiration without altering audit history.

### Publish events after commit without an outbox-equivalent

Rejected. A crash between state commit and publication could silently lose required workflow notification.

## Consequences

### Positive

- Every lifecycle mutation passes through one guarded, auditable command path.
- Optimistic concurrency makes races visible instead of overwriting newer decisions.
- Intake replay, command replay, callback replay, and outbound side-effect identity have explicit independent scopes.
- n8n can be retried safely without becoming a state authority.
- Event consumers can recover from duplicates and ordering gaps without assuming global order.
- Event payloads avoid unnecessary customer-data proliferation.
- A later transactional outbox can be implemented without changing the public event envelope.

### Costs and tradeoffs

- Clients must carry expected versions, correlation IDs, and idempotency keys.
- More intent-specific command routes and stable error codes require maintenance and contract tests.
- Consumers must persist deduplication state and handle aggregate-version gaps.
- Multi-aggregate commands require careful transaction and response design.
- Authorized detail often requires a follow-up query because integration events are intentionally minimal.

### Follow-up design work

- Define the exact role-permission matrix and authentication/service-identity mechanisms.
- Define OpenAPI component schemas, field constraints, and generated contract-test fixtures.
- Define Postgres constraints, command-idempotency storage, and transactional-outbox tables.
- Select event transport, publisher retry/dead-letter policy, and consumer retention windows.
- Define adapter failure taxonomy and uncertain-outcome reconciliation.

These choices may refine implementation details but cannot introduce direct state patching, direct n8n writes, provider-selected lifecycle state, or weaker idempotency/concurrency guarantees without a superseding ADR.
