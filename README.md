# AI Operations Automation Suite

The AI Operations Automation Suite is a portfolio-grade operations platform for a general service business. It is intended to demonstrate how service requests can move from customer intake through validation, AI-assisted interpretation, deterministic routing, human approval, workflow automation, and auditable operational follow-up.

The product remains configurable across service companies instead of assuming a single industry.

## Status

Phase 0 and Phase 1 are complete. Phase 2 implementation is locally validated through the Checkpoint 5 candidate and remains pending final publication and Orchestration acceptance. Accepted Checkpoint 4 commit `4735ce9d78f2f912d7ad93060a1589f138183052` supplies mock outbound execution/recovery; Checkpoint 5 adds the thirteen protected query contracts and the coherent twelve-scenario PostgreSQL acceptance suite. Migration `0012_mock_outbound_execution_foundation` retains 26 application tables. OpenAPI contains 32 distinct path templates and 33 operations: `/health`, thirteen protected queries, and nineteen external mutation operations.

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

No AI or outbound provider is invoked, and the mock outbound lifecycle never sends or claims to send email. AI callback success derives backend-owned prompt, provider, model, and adapter identity from frozen persistence while accepting only approved bounded evidence. The Checkpoint 5 candidate passed 584 offline, 375 PostgreSQL integration, and 959 unfiltered tests, including all twelve scenarios, but is still uncommitted and unpushed pending Orchestration acceptance. Real integrations, n8n workflows, EventPublisher execution, frontend, deployment, and all Phase 3 work remain deferred.
