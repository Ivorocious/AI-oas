# Project Status

## Current phase

**Phase 1 — Technical Design and Engineering Foundation: underway.**

Phase 0 product definition is complete. The domain model, lifecycle state machines, queue behavior, invariants, and canonical-state boundaries required for this Phase 1 task are now defined. Application implementation has not started.

## Completed work

- Defined the Phase 0 business problem, scope, users, lifecycle, approval rules, non-goals, success criteria, risks, and 12 demo scenarios in the [product brief](product-brief.md).
- Recorded the proposed component responsibilities in the [architecture](architecture.md).
- Defined all required records, ownership boundaries, relationships, sensitive-data considerations, authorities, versioning expectations, and transaction boundaries in the [domain model](domain-model.md).
- Defined inbound-delivery, service-request, proposed-action, and integration-attempt lifecycle states, guards, authorities, audit events, failures, queues, invariants, and recovery behavior in the [state-machine design](state-machines.md).
- Mapped all 12 approved demo scenarios to starting states, commands, guards, final states, queues, audit evidence, and outbound-attempt expectations.
- Accepted [ADR 0001](decisions/0001-canonical-state-and-lifecycle-boundaries.md) for canonical state and lifecycle boundaries.
- Preserved the requirement that the outbound provider is mock-only and sends no real email.

## Active task

None. The domain-model and lifecycle-state-machine design task is complete; the next focused Phase 1 task has not started.

## Blockers

None known for the completed design task. Implementation should not begin until the focused contract, permissions, persistence, and test-design decisions listed below are completed.

## Approved decisions

- Supabase Postgres is the proposed canonical operational store.
- Authorized FastAPI backend commands exclusively control canonical lifecycle transitions; the frontend, n8n, AI providers, and outbound adapters provide intent or evidence only.
- Invalid deliveries remain separate inspectable `InboundDelivery` records and do not become normal `ServiceRequest` records.
- Request status, priority, operational queue, proposed-action state, approval, and integration-attempt state are separate concepts.
- AI interpretations and deterministic routing decisions are immutable, versioned evidence with applicable prompt, schema, provider, and rule references.
- Approval binds to one exact proposed-action ID, version, and payload digest; material revision requires a new version and approval.
- Intake and outbound idempotency have independent identities and guards.
- Retrying outbound work creates a new attempt for the same logical operation; a successful logical operation cannot execute again.
- Important backend-controlled state transitions and audit events are transactionally consistent and append-oriented.
- Mutable aggregates use optimistic version checks or equivalent atomic conflict protection.
- Domain IDs use UUIDs, timestamps use UTC, and operational/audit records are not hard-deleted in the MVP.

## Technical debt and deferred design

The following focused decisions remain before implementation:

- API command, query, webhook, and event contracts, including error and concurrency responses
- Exact role-permission matrix and MVP authentication approach
- Database schema, constraints, indexes, transaction patterns, retention, and archival behavior
- Exact deterministic priority/routing criteria, duplicate-detection policy, confidence threshold, and operator-override policy
- Audit event schemas, redaction rules, access controls, and retention policy
- Failure taxonomy, retry limits/backoff, and uncertain-outcome reconciliation
- n8n workflow contracts, correlation behavior, and backend callback security
- AI and outbound adapter interfaces, timeouts, validation, and contract-test strategy
- Test architecture and executable scenario fixtures for every invariant and demo path
- Deployment, environment, observability, and recovery design

## Known limitations

- The repository contains documentation only; no frontend, backend, database, workflow, integration, automated test, or deployment exists.
- State machines are implementation-neutral and do not yet specify API payloads or database constraints.
- Exact numeric thresholds, retry counts, scoring formulas, and permission mappings remain intentionally undefined.
- No real email is sent; only a proposed mock adapter is approved for the MVP.
- The design targets one demonstration organization, one primary intake path, and modest operational scale.
- Billing, payments, multi-tenancy, mobile apps, full CRM behavior, autonomous communication, large-scale analytics, numerous real integrations, enterprise authentication, microservices, and Kubernetes remain outside scope.

## Next milestone

**Phase 1 focused design — API and event contracts.** Define implementation-neutral backend commands, queries, webhook responses, domain-event envelopes, concurrency/idempotency error semantics, and n8n callback boundaries against the approved state machines. Follow with the role-permission matrix and persistence design before scaffolding application code.
