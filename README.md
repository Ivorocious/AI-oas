# AI Operations Automation Suite

The AI Operations Automation Suite is a portfolio-grade operations platform for a general service business. It is intended to demonstrate how service requests can move from customer intake through validation, AI-assisted interpretation, deterministic routing, human approval, workflow automation, and auditable operational follow-up.

The product remains configurable across service companies instead of assuming a single industry.

## Status

Phase 0 and Phase 1 are complete; Phase 2 is underway through Checkpoint 4. The executable backend now includes intake, human access, the bounded AI attempt lifecycle, deterministic triage/review, proposal approval, and mock outbound execution/recovery. Migration `0012_mock_outbound_execution_foundation` keeps the schema at 26 application tables and production OpenAPI exposes 21 paths. Outbound attempts bind the exact proposal, approval, series operation, assigned workflow identity, adapter, callback authorization, and backend-owned stable key; retry and uncertain-outcome handling remain bounded and transactional.

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

No AI or outbound provider is invoked, and the mock outbound lifecycle never sends or claims to send email. AI callback success now derives backend-owned prompt, provider, model, and adapter identity from frozen persistence while accepting only the approved bounded callback evidence. Protected query expansion, final Phase 2 scenario acceptance, real integrations, n8n workflows, EventPublisher execution, frontend, and deployment remain unimplemented; these are not part of Checkpoint 4.
