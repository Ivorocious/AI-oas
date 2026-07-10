# Project Status

## Current phase

**Phase 0 — Product Definition: complete.**

The required source-of-truth documents are present and internally aligned: the [product brief](product-brief.md), [proposed architecture](architecture.md), and repository [README](../README.md). Completion means the product direction is documented; it does not mean application implementation has started.

## Completed work

- Defined the business problem, product scope, users, responsibilities, and one primary operational lifecycle.
- Defined initial categories, priorities, queues, human-approval rules, MVP capabilities, non-goals, success criteria, assumptions, and risks.
- Defined 12 initial demo scenarios covering normal, exceptional, approval, idempotency, and retry behavior.
- Recorded the proposed component architecture and the separation between AI interpretation, backend policy, n8n orchestration, persistence, adapters, and audit logging.
- Established that the first outbound integration is a mock email provider that sends no real email.

## Active task

None. Phase 0 is closed, and Phase 1 has not started.

## Blockers

None known for Phase 0. Later implementation will require explicit technical design decisions before code is scaffolded.

## Approved decisions

- The product is an AI Operations Automation Suite for a general service business and must remain adaptable across service industries.
- Customer, operations agent, manager/approver, and administrator are the primary roles.
- The lifecycle and human-review triggers defined in the product brief are the MVP source of truth.
- AI is limited to structured interpretation: summary, category suggestion, missing-information detection, and confidence.
- Deterministic backend code owns final priority, routing, approval requirements, authorization, idempotency, retry eligibility, and state transitions.
- The proposed stack is Next.js/TypeScript, FastAPI/Python, Supabase Postgres, and n8n, with replaceable AI and outbound adapters plus audit/event logging.
- Supabase Postgres is proposed as the canonical operational store; n8n coordinates work but does not own business state or policy.
- Every customer-facing response or scheduling invitation requires human approval.
- The first outbound provider is mock-only, simulates success or failure, records attempts, and never sends real email.
- The MVP is a modular application; microservices and Kubernetes are out of scope without demonstrated need.

## Technical debt

No application technical debt exists because implementation has not started. The following design debt is intentionally deferred to the next milestone:

- Detailed domain model, state machine, database schema, indexes, and retention policy
- API and webhook contracts, idempotency-key scope, and error taxonomy
- Exact priority, routing, duplicate-candidate, confidence-threshold, and retry configuration
- Role-permission matrix and MVP authentication approach
- Audit event schema, redaction rules, and transactional guarantees
- n8n workflow contracts and backend callback security
- Adapter interfaces, timeout policy, and provider test strategy
- Deployment, environment, observability, and recovery design

## Known limitations

- The repository contains documentation only; no frontend, backend, database, workflow, integration, tests, or deployment exists.
- The architecture is conceptual and may change through recorded design decisions before implementation.
- No real email is sent; only a mock adapter is approved for the MVP.
- Exact business-rule values and permission details are not yet specified.
- The MVP targets a single demonstration organization, one primary intake path, and modest operational scale.
- Billing, payments, multi-tenancy, mobile apps, full CRM behavior, autonomous communication, large-scale analytics, numerous real integrations, enterprise authentication, microservices, and Kubernetes are outside scope.

## Next milestone

**Phase 1 — Technical design and implementation foundation.** Define the domain model and state machine, API and event contracts, role-permission matrix, deterministic rule configuration, adapter interfaces, audit schema, and test strategy. Record consequential choices as architecture decisions before scaffolding the minimum application foundation.
