# AI Operations Automation Suite

The AI Operations Automation Suite is a portfolio-grade operations platform for a general service business. It is intended to demonstrate how service requests can move from customer intake through validation, AI-assisted interpretation, deterministic routing, human approval, workflow automation, and auditable operational follow-up.

The product remains configurable across service companies instead of assuming a single industry.

## Status

Phase 0 and Phase 1 are complete; Phase 2 is underway. The executable backend now includes intake, human access, the bounded AI attempt lifecycle, and deterministic triage/review. Migration `0010_deterministic_triage_foundation` seeds the immutable `general-service-demo@1.0.0` decision policy and brings the schema to 22 application tables. A trusted in-process `CompleteTriage` service—not a public route—derives duplicate candidates, category, priority, review outcome, status, and queue from current evidence. Human-authenticated duplicate resolution and complete-human-review commands preserve expected-version, command-idempotency, role, audit, outbox, and atomic transaction boundaries.

- [Backend setup, startup, and validation](backend/README.md)

- [Product brief](docs/product-brief.md)
- [Proposed architecture](docs/architecture.md)
- [Proposed domain model](docs/domain-model.md)
- [Proposed lifecycle state machines](docs/state-machines.md)
- [Proposed API contracts](docs/api-contracts.md)
- [Proposed event and n8n contracts](docs/event-contracts.md)
- [Proposed authentication and authorization](docs/authentication-and-authorization.md)
- [Proposed Postgres persistence design](docs/persistence-design.md)
- [Deterministic triage and review policy](docs/deterministic-decision-policy.md)
- [Proposed failure and recovery policy](docs/failure-and-recovery-policy.md)
- [ADR 0001: canonical lifecycle state](docs/decisions/0001-canonical-state-and-lifecycle-boundaries.md)
- [ADR 0002: API and event boundaries](docs/decisions/0002-api-command-and-event-boundaries.md)
- [ADR 0003: authentication and role permissions](docs/decisions/0003-authentication-and-role-permissions.md)
- [ADR 0004: Postgres persistence and transactional outbox](docs/decisions/0004-postgres-persistence-and-transactional-outbox.md)
- [ADR 0005: deterministic triage and review policy](docs/decisions/0005-deterministic-triage-and-review-policy.md)
- [ADR 0006: failure, retry, and reconciliation policy](docs/decisions/0006-failure-retry-and-reconciliation-policy.md)
- [Project status](docs/project-status.md)

## Implementation honesty

No AI provider is invoked. AI callback evidence remains advisory: only the deterministic backend evaluator can derive category, priority, queue, routing, and review state. The current implementation has no public complete-triage endpoint; trusted backend code invokes that service in process. Proposal/approval behavior, mock outbound execution, real integrations, n8n workflows, event publication, frontend, and deployment remain unimplemented.
