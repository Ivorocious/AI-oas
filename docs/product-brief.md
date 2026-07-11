# Product Brief

## Product summary

The AI Operations Automation Suite is a portfolio-grade operations platform for a general service business. It coordinates customer service requests from intake through validation, persistence, AI-assisted interpretation, deterministic routing, human approval, workflow automation, and operational follow-up. The product is configurable across service companies and is not tied to one industry.

This brief is the approved product source of truth for the MVP. The corresponding [architecture](architecture.md) and [deterministic triage policy](deterministic-decision-policy.md) are proposed and have not been implemented.

## Business problem

General service businesses often receive incomplete or inconsistent requests through multiple delivery attempts, then coordinate triage, follow-up, scheduling, and approvals manually. This creates duplicate work, slow responses, unclear ownership, inconsistent prioritization, and limited evidence when an action fails or a customer decision is questioned.

The MVP will demonstrate a controlled operating flow in which AI helps interpret unstructured input, backend rules retain authority over business decisions, people approve sensitive actions, and every important transition remains visible and auditable.

## Target users and responsibilities

| User | Responsibilities |
| --- | --- |
| Customer | Submit a service request, provide requested details, and receive an approved response or scheduling invitation. |
| Operations agent (`OperationsAgent`) | Monitor queues, investigate incomplete or ambiguous requests, resolve likely duplicates, prepare or review proposed actions, retry failed work, and submit customer-facing actions for approval. |
| Manager/approver (`ManagerApprover`) | Review urgent or exceptional cases and approve or reject proposed customer-facing actions. |
| Administrator (`Administrator`) | Govern controlled policy activation, adapters, access, and operational settings; investigate audit and integration records. The initial policy is deployment-controlled, not a runtime configuration UI. |

Operations agents cannot approve or reject customer-facing actions. A `ManagerApprover` or `Administrator` must decide the exact proposal, and the approver cannot be a creator or material reviser of that proposal. Urgent review also requires manager or administrator authority. The fixed MVP decision policy is defined in the [deterministic triage policy](deterministic-decision-policy.md); identity and permission enforcement is defined in [authentication and authorization](authentication-and-authorization.md).

## Primary request lifecycle

The product has one primary operational lifecycle:

1. A customer submits a service request.
2. The system validates and normalizes the input before downstream processing.
3. Idempotency protection prevents a repeated webhook delivery from being processed twice.
4. The system checks for likely duplicate contacts or requests and flags candidates; it does not silently merge them.
5. A replaceable AI provider produces a structured summary, suggested category, missing-information list, and confidence value.
6. Backend rules—not the AI model—apply a versioned deterministic policy to calculate final category, priority, routing, and required review from validated inputs.
7. The request enters the appropriate operational queue.
8. Urgent, ambiguous, incomplete, low-confidence, or possible-duplicate cases wait for human review.
9. The system prepares a proposed customer response and scheduling invitation.
10. A distinct authorized `ManagerApprover` or `Administrator` approves or rejects the exact proposal; the decision-maker cannot be its creator or a material reviser.
11. Approval invokes a clearly labeled mock email provider; rejection records the decision and prevents the outbound attempt.
12. The system records important events, approvals, errors, retries, and integration attempts.
13. An operations dashboard exposes status, priority, routing, approvals, failures, and audit history.

Failures remain visible and recoverable: provider failures route work for review or retry without erasing prior state or re-running completed side effects.

## MVP capabilities

- Accept a service request through a defined intake contract.
- Validate, normalize, and persist request data and processing state.
- Enforce webhook idempotency and flag likely duplicate contacts or requests.
- Use a replaceable AI-provider adapter for summary, category suggestion, missing-information detection, and confidence scoring.
- Apply deterministic backend rules for priority, routing, approval requirements, and state transitions.
- Provide the initial operational queues and human-review workflow.
- Prepare a customer response and scheduling invitation for approval.
- Record approval and rejection decisions with actor, time, and rationale where required.
- Simulate outbound email success or failure through a replaceable mock adapter; the MVP sends no real email.
- Support controlled retry of failed AI and mock outbound attempts.
- Record append-oriented audit events and integration attempts.
- Expose an operations dashboard for request status, priority, routing, approvals, failures, and history.

## Initial service categories

The initial categories are configurable through controlled policy versions. Their stable wire values are defined in the [deterministic triage policy](deterministic-decision-policy.md): `Consultation`, `Installation`, `Repair`, `RoutineMaintenance`, `Inspection`, and `OtherCustomRequest`. Display labels remain Consultation, Installation, Repair, Routine maintenance, Inspection, and Other/custom request.

## Priority levels and operational queues

Priority is a business-rule result, independent from the queue that represents current handling needs.

| Priority | Intended use |
| --- | --- |
| Low | Non-urgent work with flexible timing and limited operational impact. |
| Normal | Complete, routine requests handled through the standard flow. |
| High | Time-sensitive or materially impactful work needing faster attention. |
| Urgent | Immediate or safety/continuity-sensitive work that always requires manager or administrator human review before action preparation. |

| Queue display label | Entry condition or purpose |
| --- | --- |
| Invalid submissions | Input fails the intake contract and cannot enter normal processing. |
| Standard requests | Valid Low or Normal requests with no exception requiring review. |
| Priority requests | Valid High requests requiring expedited handling. |
| Human review | Urgent, ambiguous, incomplete, or low-confidence requests and other policy exceptions. |
| Duplicate review | A likely duplicate contact or request needs a human resolution. |
| Failed/retry required | A provider, workflow, or integration attempt failed and requires controlled recovery. |

This product-facing table and the demo scenarios below use display labels. Technical contracts use the stable queue wire values defined in the [API contracts](api-contracts.md#initial-wire-enums).

## Human-approval rules

Human review or approval is required when any of the following applies:

- AI confidence is below a configurable threshold.
- The calculated priority is Urgent.
- Required or operationally important information is missing.
- A possible duplicate needs resolution.
- A customer-facing response or scheduling invitation is ready to be sent.

AI output is advisory and cannot approve an action, set final priority, select final routing, merge duplicates, or send a response. Approval must record the actor, decision, timestamp, target action, and optional or policy-required rationale. A rejected action must not call the outbound adapter; it returns to an appropriate review state for revision or closure. Approval applies to a specific proposed action, so a materially changed response requires a new approval.

## Initial demo scenarios

| # | Scenario | Expected demonstration |
| --- | --- | --- |
| 1 | Valid standard request | A complete routine request is normalized, interpreted, assigned Low or Normal priority by rules, and placed in Standard requests. |
| 2 | High-priority request | Deterministic criteria assign High priority and route the request to Priority requests. |
| 3 | Urgent request requiring approval | Rules assign Urgent priority, route the request to Human review, and block action until an authorized human decides. |
| 4 | Invalid submission | Contract validation rejects or quarantines the input in Invalid submissions with an auditable reason. |
| 5 | Missing-information case | AI or validation identifies important missing details, and backend policy routes the request to Human review. |
| 6 | Low-confidence AI result | Confidence below the configured threshold triggers Human review without letting AI decide routing autonomously. |
| 7 | Possible duplicate | A likely match is placed in Duplicate review and remains separate until a human resolves it. |
| 8 | Repeated webhook delivery | The same idempotency key is acknowledged without creating a second request or repeating side effects. |
| 9 | AI-provider failure | The failed attempt is logged and the request moves to Failed/retry required for controlled retry or review. |
| 10 | Mock email failure followed by retry | The mock adapter records a simulated failure; a later controlled retry records a separate successful attempt without duplicate processing. |
| 11 | Approved outbound action | An authorized user approves the exact proposal, the mock email adapter simulates delivery, and the decision and attempt are audited. |
| 12 | Rejected outbound action | An authorized user rejects the proposal, no outbound adapter is called, and the decision and next state are audited. |

## MVP non-goals

- Full CRM replacement
- Billing and payment processing
- Multi-tenant organization management
- Mobile application
- Fully autonomous customer communication
- Large-scale analytics
- Numerous real integrations
- Enterprise-scale authentication
- Microservices without demonstrated need
- Kubernetes

## Success criteria

Phase 0 defines success for the future MVP as follows:

- All 12 demo scenarios produce the expected state, queue, audit evidence, and side-effect behavior.
- Repeated delivery of the same webhook does not create a duplicate request or repeat a completed outbound attempt.
- Every AI result is stored as advisory output, while final priority, routing, approval requirements, and transitions are reproducible from backend rules and configuration.
- Every customer-facing action is linked to an authorization decision; rejected actions produce no outbound attempt.
- The outbound integration is visibly labeled as mock behavior and can simulate both success and failure.
- Operators can identify requests awaiting review, approvals, failures, and retries and can inspect their histories from the dashboard.
- Provider failures preserve request state and can be retried safely.
- A reviewer can trace each important request transition to its time, cause, and responsible actor or system component.

## Assumptions

- The MVP serves one demonstration organization and one primary intake channel.
- Service categories, confidence thresholds, priority criteria, duplicate rules, and routing rules are versioned policy inputs that begin with a small approved set and are deployment-controlled in the MVP.
- Customer input may be free-form, but the persisted request uses a stable normalized schema.
- Duplicate detection produces candidates and confidence or reason signals; humans decide ambiguous matches.
- Users are identified sufficiently to attribute approvals and administrative changes, although enterprise authentication is out of scope.
- Scheduling is represented by a prepared invitation or link, not a full scheduling engine.
- n8n coordinates asynchronous workflow steps but does not own business policy or canonical state.
- The first outbound adapter is mock-only and never sends real email.

## Product and technical risks

| Risk | Planned treatment |
| --- | --- |
| AI output is incorrect, inconsistent, or prompt-sensitive | Validate structured output, store confidence and provider metadata, use thresholds, and require review for uncertain cases. |
| Business policy leaks into prompts or n8n | Keep priority, routing, approval, and transition rules in tested backend code. |
| Duplicate processing causes repeated actions | Use idempotency keys, persisted attempt records, and retry-safe state transitions. |
| Duplicate matching produces false positives or negatives | Treat matches as candidates, expose reasons, and require human resolution when uncertain. |
| Approval becomes superficial or bypassable | Bind approval to a specific action and enforce the check server-side before invoking any outbound adapter. |
| Audit records omit failure context or can be overwritten | Define append-oriented event records and capture sanitized request, response, actor, timing, and error metadata. |
| n8n and backend state drift | Make the backend and database authoritative; use correlation identifiers and explicit workflow callbacks. |
| Mock behavior is mistaken for production capability | Label the adapter and UI states as simulated and repeat the limitation in documentation and demo scripts. |
| Scope expands into CRM, billing, or infrastructure complexity | Enforce MVP non-goals and require a product decision before expanding scope. |
| Sensitive customer data appears in logs or AI requests | Minimize data, redact logs, define retention, and document provider boundaries before implementation. |

## Portfolio and hiring value

The suite is intended to show more than a happy-path AI demo. It provides evidence of product scoping, typed full-stack design, Python services, relational persistence, workflow orchestration, adapter boundaries, deterministic policy, human-in-the-loop controls, idempotency, retries, auditability, failure handling, and honest operational UX. The demo scenarios make these engineering decisions observable and discussable in portfolio reviews and technical interviews.
