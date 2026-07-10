# ADR 0001: Canonical State and Lifecycle Boundaries

- Status: Accepted
- Date: 2026-07-10
- Scope: MVP technical design

## Context

The product must coordinate intake, AI-assisted interpretation, deterministic routing, human approval, workflow automation, outbound retries, and audit history without allowing duplicate requests or repeated customer-facing actions. Next.js, FastAPI, Supabase Postgres, n8n, AI providers, and outbound adapters will participate, but they do not have equal authority.

A single broad status field would conflate request progress, priority, queue ownership, approval, and provider execution. Allowing n8n, the frontend, or AI output to update those values independently would make policy difficult to test and could split canonical state across systems. Reusing one idempotency mechanism for intake and outbound work would not protect both boundaries: a delivery replay and an outbound retry represent different logical operations.

The model also needs to preserve why historical decisions occurred and ensure approval covers the exact content that is executed.

## Decision

1. Supabase Postgres is the proposed canonical operational store.
2. Authorized FastAPI backend commands exclusively control canonical lifecycle transitions. The frontend and n8n may request commands; AI providers and adapters may return evidence; none may write or redefine state or policy directly.
3. `InboundDelivery`, `ServiceRequest`, `ProposedAction`, and `IntegrationAttempt` use separate state machines. Request status, priority, operational queue, proposal state, approval, and attempt state remain separate concepts.
4. Invalid inbound deliveries remain inspectable `InboundDelivery` records and never become normal `ServiceRequest` records.
5. AI interpretations and routing decisions are immutable, versioned evidence. AI is advisory; deterministic backend rules own final category, priority, queue, and review requirements.
6. Each approval is an immutable decision bound to one exact proposed-action ID, version, and payload digest. A material revision creates a new version, supersedes the prior proposal where appropriate, and requires new approval.
7. Intake uses an idempotency key plus canonical payload hash. Same key and same hash returns the original logical result; same key and different hash is a conflict.
8. Every AI or outbound provider invocation creates an `IntegrationAttempt`; a retry creates a new attempt under the same `logical_operation_id` rather than rewriting history. An outbound operation's identity and stable idempotency key are independent from intake identity. A successful outbound operation cannot execute again.
9. Backend-controlled state changes and their audit events are transactionally consistent. Historical records are append-oriented or versioned and are not hard-deleted in the MVP.
10. Mutable aggregates use optimistic version checks or equivalent atomic guards. IDs are UUIDs, persisted timestamps are UTC, and historical records retain applicable rule, prompt, schema, and adapter versions.

The detailed model is defined in [the domain model](../domain-model.md) and [state-machine design](../state-machines.md).

## Alternatives considered

### Store canonical lifecycle state in n8n

Rejected. n8n is useful for orchestration and visibility into workflow execution, but workflow definitions and execution history are not an appropriate transactional system of record for authorization, concurrency, idempotency, or reproducible business policy.

### Use one service-request status for every operational concern

Rejected. A combined field would create a state explosion and make it unclear whether `Urgent`, `HumanReview`, `Approved`, or `RetryableFailure` describes priority, work ownership, permission, or lifecycle progress.

### Let AI select final routing and approval behavior

Rejected. Provider and prompt changes can produce nondeterministic results. AI output remains evidence consumed by versioned, testable backend rules.

### Treat a request-level approval as permission for later revisions

Rejected. A generic approval could authorize content the approver never saw. Approval is bound to an exact immutable proposal version and digest.

### Reuse intake idempotency keys for outbound calls

Rejected. Intake delivery identity and outbound side-effect identity have different scopes, timing, and retry behavior. Independent keys and invariants are required.

### Update one integration-attempt record in place during retry

Rejected. In-place reuse would hide failure history and make concurrent or uncertain provider outcomes harder to reconcile. Every invocation receives a distinct attempt record under the same logical operation.

### Use pessimistic locking for all operator workflows

Not selected as the default. Long-lived locks are awkward across user review and external calls. Optimistic versions plus atomic transitions provide explicit conflicts without holding database locks across human or provider latency. Short transactional locks may still be an implementation detail for idempotency or uniqueness enforcement.

## Consequences

### Positive

- Business policy and lifecycle behavior can be versioned, tested, and reproduced independently of prompts and workflows.
- Invalid intake, replays, approval history, failures, and retries remain inspectable.
- Approval cannot accidentally authorize revised content.
- Intake replay and outbound retry protections address their actual logical boundaries.
- The system can prevent duplicate successful side effects even when multiple components request work concurrently.
- Operational queues remain understandable views instead of hidden alternative state machines.

### Costs and tradeoffs

- More records and explicit transitions are required than in a simple CRUD design.
- Backend commands must coordinate transactional updates across state summaries, immutable evidence, and audit events.
- Provider timeouts with unknown outcomes require adapter idempotency or reconciliation; they cannot always be retried blindly.
- Operators may encounter optimistic concurrency conflicts and need to refresh before retrying a decision.
- Version and correlation metadata must be carried through n8n and adapter calls.

### Follow-up design work

- Define API command and event contracts, including concurrency and idempotency responses.
- Define the exact role-permission matrix and authentication approach.
- Define database constraints and transaction patterns that enforce the approved invariants.
- Define audit event schemas, redaction/retention policy, failure taxonomy, and retry policy.
- Define adapter reconciliation behavior for uncertain outcomes.

These follow-ups may refine implementation details but must preserve this decision unless a later ADR explicitly supersedes it.
