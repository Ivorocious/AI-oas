# Proposed API Contracts

## Status and scope

This document defines implementation-neutral HTTP contracts for the MVP. It applies the approved [domain model](domain-model.md), [lifecycle state machines](state-machines.md), [event contracts](event-contracts.md), [authentication and authorization model](authentication-and-authorization.md), [persistence design](persistence-design.md), and ADRs [0002](decisions/0002-api-command-and-event-boundaries.md), [0003](decisions/0003-authentication-and-role-permissions.md), and [0004](decisions/0004-postgres-persistence-and-transactional-outbox.md). No API, route, authentication middleware, database schema/migration, or workflow has been implemented.

The proposed prefix is `/api/v1`. Commands may change canonical state; queries are read-only. FastAPI is the only authoritative command boundary. The frontend and n8n call these contracts and never write canonical database state directly.

## Transport conventions

- Request and response media type is `application/json`, encoded as UTF-8.
- Domain identifiers and correlation identifiers are UUID strings.
- Persisted and returned timestamps are UTC RFC 3339 values such as `2026-07-10T08:15:30Z`.
- Every body includes an explicit `schema_version`; the initial proposed value is `1.0`.
- `X-Correlation-ID` is optional on requests. When present it must be a UUID; otherwise the backend generates one. The accepted value is echoed in the response and propagated to audit events, attempts, and integration events.
- `Idempotency-Key` is an opaque, non-PII client key. The proposed transport limit is 8–128 visible ASCII characters. Intake and command keys use independent namespaces and semantics.
- Human requests use validated Supabase Auth bearer tokens plus a per-request application-role lookup. Workflow requests use HMAC authentication, and result callbacks additionally require attempt scope. Actor identity/role is never accepted from request JSON; the complete policy is defined in [authentication and authorization](authentication-and-authorization.md).
- Stable lifecycle values use the established `PascalCase` names. Stable error codes use `UPPER_SNAKE_CASE`.

### Initial wire enums

| Concept | Stable values |
| --- | --- |
| Service-request status | `TriagePending`, `HumanReview`, `DuplicateReview`, `ReadyForAction`, `AwaitingApproval`, `ActionRevisionRequired`, `ActionPendingExecution`, `RetryableFailure`, `Completed`, `TerminalFailure`, `ClosedDuplicate` |
| Priority | `Low`, `Normal`, `High`, `Urgent` |
| Operational queue | `InvalidSubmissions`, `StandardRequests`, `PriorityRequests`, `HumanReview`, `DuplicateReview`, `FailedRetryRequired` |
| Proposed-action state | `Draft`, `PendingApproval`, `Approved`, `Rejected`, `Superseded`, `PendingExecution`, `Executed`, `RetryableExecutionFailure`, `TerminalExecutionFailure` |
| Integration-attempt state | `Pending`, `Running`, `Succeeded`, `RetryableFailure`, `TerminalFailure` |
| Approval decision | `Approved`, `Rejected` |

Display labels may be localized, but clients must not invent or translate wire values.

## Command contract

All mutation endpoints in the command catalog require `Idempotency-Key`. Intake uses the accepted-intake rules described below. Other commands scope the key to the authenticated actor class, HTTP route, and target resource:

- Same key and same canonical command body returns the original command result and does not repeat changes or side effects.
- Same key with a different canonical command body returns `409 COMMAND_IDEMPOTENCY_CONFLICT`.
- A key used for intake does not reserve or identify an outbound operation.
- Outbound commands use the backend-created `logical_operation_id` and stable outbound idempotency key in addition to command replay protection. Clients cannot choose or replace the outbound key.

For non-intake commands, the backend resolves command idempotency before expected-version checks. An identical replay therefore receives its original result even though the aggregate version advanced during the first execution. A new command key proceeds to version and business-guard validation.

### Expected versions

Commands include the expected version of every mutable aggregate they directly change. Keys for uninvolved aggregates are omitted.

```json
{
  "schema_version": "1.0",
  "expected_versions": {
    "service_request": 4,
    "proposed_action": 2,
    "integration_attempt": 1
  },
  "command": {}
}
```

The backend compares every supplied value atomically. A mismatch changes nothing and returns HTTP `409` with `CONCURRENCY_CONFLICT` and the safe current versions. Backend-owned related aggregates that are not exposed to the caller are still protected by transactional guards.

### Success envelope

```json
{
  "schema_version": "1.0",
  "correlation_id": "3a9d9af4-a611-4e10-b916-50d07ff56748",
  "result": {
    "resource_type": "ServiceRequest",
    "resource_id": "f33809eb-cf57-480a-9a89-aed2469fe55a"
  },
  "versions": {
    "service_request": 5
  }
}
```

Command-specific results add identifiers, lifecycle summaries, or attempt references but do not expose unrestricted provider payloads or PII. A replay returns the same logical status code and result unless the intake contract explicitly specifies `200` for replay.

## Intake contract

### Endpoint

`POST /api/v1/intake/service-requests`

Required transport inputs are `Content-Type: application/json`, `Idempotency-Key`, and a JSON body. `X-Correlation-ID` is optional. The body contains `schema_version`, normalized contact inputs, and service-request inputs such as description and optional category, location, or scheduling context. Exact field lengths and channel-specific validation remain a later schema task.

### Outcomes

| Case | Persisted evidence | Key effect | HTTP response |
| --- | --- | --- | --- |
| New valid request | New `InboundDelivery` with `Accepted` + `New`; one `ServiceRequest` in `TriagePending`; audit and outbox evidence | Reserves the accepted-intake key and canonical payload hash | `201 Created`; delivery ID, request ID, `New`, initial status, correlation ID, request version; `Location` points to the request query |
| Accepted replay | New physical `InboundDelivery` with `Accepted` + `IdempotentReplay`, linked to the original delivery and request; no downstream work repeated | Existing accepted key/hash remains authoritative | `200 OK`; replay delivery ID plus the original accepted delivery ID, request ID, original logical intake result and initial status, `IdempotentReplay`, correlation ID |
| Idempotency conflict | New physical `InboundDelivery` with `Rejected` + `IdempotencyConflict`; no request created | Original accepted reservation remains unchanged | `409 IDEMPOTENCY_CONFLICT`; rejected delivery ID and correlation ID; no payload comparison details |
| Well-formed JSON failing intake schema or business validation, with no accepted reservation for the key | Inspectable `InboundDelivery` with `Rejected` + `Invalid`; no request | Does not reserve the key as an accepted logical intake | `422 INTAKE_VALIDATION_FAILED`; delivery ID, sanitized field issues, correlation ID |
| Malformed JSON with a usable unreserved idempotency key and supported content type | Inspectable `InboundDelivery` with `Rejected` + `Invalid`, using a raw-body fingerprint; no request | Does not reserve the key as accepted | `400 MALFORMED_JSON`; delivery ID and correlation ID |
| Missing or unusable `Idempotency-Key` or invalid correlation identifier | Rejected at the HTTP edge; no canonical delivery because safe identity/correlation cannot be established | No reservation | `400 MISSING_IDEMPOTENCY_KEY` or `400 INVALID_TRANSPORT_IDENTIFIER` |
| Unsupported media type or unreadable transport body | Rejected at the HTTP edge; no canonical delivery | No reservation | `415 UNSUPPORTED_MEDIA_TYPE` or `400 INVALID_TRANSPORT_BODY` |

A corrected valid request may reuse a key previously seen only on rejected invalid deliveries. Once a valid intake is accepted, reuse with a different canonical payload is always a conflict. If a later body is malformed or cannot be canonicalized to prove equality, the accepted reservation wins: the physical delivery is recorded as `Rejected` + `IdempotencyConflict` and returns `409 IDEMPOTENCY_CONFLICT`. Recording a rejected physical delivery never creates a normal service request.

Intake processing order is: validate usable transport identity, parse/canonicalize when possible, look up an accepted reservation, return replay or conflict when one exists, then validate and accept/reject a previously unreserved payload. An accepted replay returns the original logical result without re-running current business validation.

### Intake result shape

```json
{
  "schema_version": "1.0",
  "correlation_id": "3a9d9af4-a611-4e10-b916-50d07ff56748",
  "result": {
    "delivery_id": "b0f34ee4-d16c-4adc-933e-aabcf8d86253",
    "service_request_id": "f33809eb-cf57-480a-9a89-aed2469fe55a",
    "intake_outcome": "New",
    "service_request_status": "TriagePending"
  },
  "versions": {
    "inbound_delivery": 1,
    "service_request": 1
  }
}
```

For an accepted replay, `delivery_id` identifies the new physical replay, `original_delivery_id` identifies the accepted delivery, and `service_request_id`, initial status, and logical request version reproduce the original intake result. Clients query the request endpoint for its current state.

## Command catalog

Common rules apply to every row: meaningful backend commands enforce the [state-machine guards](state-machines.md), resolve the actor through the [permission matrix](authentication-and-authorization.md#permission-matrix), append audit evidence transactionally, and return updated aggregate versions. Endpoint permission never bypasses self-approval, exact-proposal, version, idempotency, attempt, or retry guards. `400`, `401`, `403`, `404`, `409`, and `422` errors use the common error envelope.

### Request interpretation and review

| Intent and endpoint | Request information and expected versions | Guard and authority | Idempotency and response |
| --- | --- | --- | --- |
| Start AI interpretation — `POST /api/v1/service-requests/{request_id}/commands/start-ai-interpretation` | Expected `service_request`; optional backend-known interpretation configuration reference, not prompt text | Request `TriagePending`; no active/successful attempt for the same input/configuration; `BackendService` or constrained `WorkflowService`; backend creates attempt | Command key required; `202 Accepted` with backend-created attempt ID, operation ID, request/attempt versions |
| Complete deterministic triage — `POST /api/v1/service-requests/{request_id}/commands/complete-triage` | Expected `service_request`; references to current stored interpretation and duplicate-check evidence | Request `TriagePending`; evidence current; `BackendService` deterministic rules calculate category, priority, queue, and review need | Command key required; `200 OK` with routing-decision ID, status, priority, queue, request version |
| Resolve duplicate — `POST /api/v1/service-requests/{request_id}/duplicate-candidates/{candidate_id}/commands/resolve` | Expected `service_request`; decision `ConfirmedDuplicate` or `NotDuplicate`; rationale when policy requires | Request `DuplicateReview`; candidate unresolved/current; `OperationsAgent`, `ManagerApprover`, or `Administrator` | Command key required; `200 OK` with candidate resolution, request status/queue/version |
| Complete human review — `POST /api/v1/service-requests/{request_id}/commands/complete-human-review` | Expected `service_request`; references to resolved information/evidence and optional sanitized note | Request `HumanReview`; all required items resolved; `OperationsAgent` only when non-Urgent, otherwise `ManagerApprover` or `Administrator` | Command key required; `200 OK` with request status/queue/version |
| Retry AI processing — `POST /api/v1/service-requests/{request_id}/commands/retry-ai` | Expected `service_request`; failed AI attempt ID | Request `RetryableFailure` targeting `TriagePending`; attempt retryable; no active/successful sibling; allowed human role, `BackendService`, or constrained `WorkflowService` | Command key required; `202 Accepted` with request `TriagePending`, new `Pending` attempt ID under the same AI logical operation, and updated versions |
| Mark retryable work terminal — `POST /api/v1/service-requests/{request_id}/commands/mark-terminal-failure` | Expected `service_request`; expected `proposed_action` when failure is outbound; required rationale and failed attempt/reference | Request `RetryableFailure`; `ManagerApprover` or `Administrator`; terminal disposition or policy exhaustion | Command key required; `200 OK` with request `TerminalFailure`, related proposal state when applicable, queue, versions |

Command bodies may select evidence or record a human disposition; they cannot submit final priority, queue, routing, or arbitrary next status.

### Proposal and approval

| Intent and endpoint | Request information and expected versions | Guard and authority | Idempotency and response |
| --- | --- | --- | --- |
| Create draft — `POST /api/v1/service-requests/{request_id}/proposed-actions` | Expected `service_request`; action type, destination reference, content/scheduling draft | Request `ReadyForAction` or `ActionRevisionRequired`; no conflicting active draft; authorized operations actor | Command key required; `201 Created` with draft ID/state, series/operation references, request/action versions; `Location` points to proposal query |
| Update editable draft — `PUT /api/v1/proposed-actions/{action_id}/draft` | Expected `proposed_action`; editable destination/content/scheduling fields | Proposal `Draft`; authorized operations actor; no submitted snapshot or decision | Command key required; `200 OK` with updated draft digest preview and action version; no lifecycle transition |
| Submit proposal — `POST /api/v1/proposed-actions/{action_id}/commands/submit-for-approval` | Expected `service_request` and `proposed_action` | Active proposal `Draft`; request `ReadyForAction` or `ActionRevisionRequired`; required fields present; authorized operations actor | Command key required; `200 OK` with frozen digest, proposal `PendingApproval`, request `AwaitingApproval`, versions |
| Approve exact proposal — `POST /api/v1/proposed-actions/{action_id}/commands/approve` | Expected `service_request` and `proposed_action`; exact payload digest; optional rationale | Request `AwaitingApproval`; proposal `PendingApproval`; distinct `ManagerApprover` or `Administrator`; actor absent from frozen attribution exclusion; no prior decision; digest exact | Command key required; `200 OK` with immutable approval ID, proposal `Approved`, request `ActionPendingExecution`, queue, versions |
| Reject exact proposal — `POST /api/v1/proposed-actions/{action_id}/commands/reject` | Expected `service_request` and `proposed_action`; exact payload digest; rationale when required | Same distinct-approver, self-decision, exact-version/digest, and no-prior-decision guards as approval | Command key required; `200 OK` with immutable rejection ID, proposal `Rejected`, request `ActionRevisionRequired`, queue, versions; no attempt |
| Create material revision — `POST /api/v1/proposed-actions/{action_id}/commands/create-material-revision` | Expected `service_request` and `proposed_action`; replacement draft content | Source/request pair must match an allowed revision path; no active/successful operation; authorized operations actor. Backend derives the same request, proposal series, and operation identity | Command key required; `201 Created` with old `Superseded` or historical `Rejected`, replacement `Draft`, request `ActionRevisionRequired`, cleared recovery marker when applicable, versions; no approval transfer |
| Submit replacement — `POST /api/v1/proposed-actions/{replacement_id}/commands/submit-for-approval` | Expected `service_request` and replacement `proposed_action`; same contract as submission | Active replacement is `Draft`, same request/series, current versions; authorized operations actor | Command key required; `200 OK` with replacement `PendingApproval`, request `AwaitingApproval`, frozen digest, versions; new decision required |

There are no generic `PATCH` endpoints for request status, queue, priority, approval state, or execution state.

### Outbound execution

| Intent and endpoint | Request information and expected versions | Guard and authority | Idempotency and response |
| --- | --- | --- | --- |
| Start approved outbound operation — `POST /api/v1/proposed-actions/{action_id}/commands/start-outbound` | Expected `service_request` and `proposed_action` | Request `ActionPendingExecution`; proposal `Approved`; exact approval valid; no active/successful attempt; `BackendService` or constrained `WorkflowService` | Command key required; backend reserves stable outbound key; `202 Accepted` with `Pending` attempt ID, logical operation ID, versions |
| Retry outbound operation — `POST /api/v1/proposed-actions/{action_id}/commands/retry-outbound` | Expected `service_request` and `proposed_action`; failed attempt ID | Request `RetryableFailure` targeting `ActionPendingExecution`; proposal `RetryableExecutionFailure`; approval still valid; failure retryable; no active/successful sibling; authorized operations/system actor | Command key required; `202 Accepted` with request `ActionPendingExecution`, proposal `PendingExecution`, next `Pending` attempt ID/number using the same logical operation/outbound key, and versions |

The client never supplies provider success, request completion, or a new outbound idempotency key to these endpoints.

### Attempt dispatch and integration-result callbacks

Callbacks report evidence for backend-created attempts. They are commands, not direct state patches.

| Intent and endpoint | Request information and expected versions | Guard and authority | Idempotency and response |
| --- | --- | --- | --- |
| Claim and start attempt — `POST /api/v1/integration-attempts/{attempt_id}/commands/start` | Expected `integration_attempt`; no provider result or owner-state fields | Attempt is `Pending`; `BackendService` internal execution or HMAC-authenticated `WorkflowService` assigned to the attempt; owner input/approval remains valid; no successful sibling | Command key required; `200 OK` with attempt `Running`, correlation/context references, and attempt version |
| Record success — `POST /api/v1/integration-attempts/{attempt_id}/callbacks/succeeded` | Expected `integration_attempt`; result schema/adapter version, provider correlation, sanitized evidence; validated structured interpretation for AI or simulated result for mock outbound | Valid `WorkflowService` HMAC identity and exact attempt-scoped callback credential; attempt is `Running`; no contradictory terminal result | Callback command key required; duplicate same result returns `200`; first result returns `200 OK` with attempt and backend-derived owner states/versions |
| Record retryable failure — `POST /api/v1/integration-attempts/{attempt_id}/callbacks/retryable-failure` | Expected `integration_attempt`; stable failure classification/code, sanitized evidence, adapter version | Same `WorkflowService` HMAC and exact attempt-scoped credential; same attempt ownership/current-state guards; backend owns retry eligibility and parent transition | Same-result replay safe; `200 OK` with attempt `RetryableFailure` and backend-derived request/proposal summary |
| Record terminal failure — `POST /api/v1/integration-attempts/{attempt_id}/callbacks/terminal-failure` | Expected `integration_attempt`; stable failure classification/code, sanitized evidence, adapter version | Same `WorkflowService` HMAC and exact attempt-scoped credential; same ownership/current-state guards; classification must satisfy terminal policy | Same-result replay safe; `200 OK` with attempt `TerminalFailure` and backend-derived request/proposal summary |

Callbacks reject fields that attempt to set service-request status, priority, queue, routing, approval, proposal state, retry eligibility, or arbitrary aggregate IDs. A callback for an unknown or unauthorized attempt returns `404 ATTEMPT_NOT_FOUND` or `403 CALLBACK_FORBIDDEN`. A different result after terminalization returns `409 INTEGRATION_RESULT_CONFLICT`.

## Query catalog

Queries never change state and do not require expected versions or idempotency keys. Authorization and field-level redaction still apply.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/service-requests/{request_id}` | Current request summary, status, priority, queue, active references, and aggregate version |
| `GET /api/v1/service-requests?queue=&priority=&status=&cursor=&limit=` | Cursor-paginated operational views using backend-owned filters |
| `GET /api/v1/inbound-deliveries/{delivery_id}` | Inspect accepted, replayed, rejected, conflicted, or failed delivery evidence |
| `GET /api/v1/service-requests/{request_id}/timeline` | Authorized combined lifecycle and audit projection |
| `GET /api/v1/service-requests/{request_id}/ai-interpretations` | Versioned advisory interpretations and safe metadata |
| `GET /api/v1/service-requests/{request_id}/duplicate-candidates` | Candidate evidence and resolution state |
| `GET /api/v1/service-requests/{request_id}/routing-decisions` | Versioned deterministic decisions and rule references |
| `GET /api/v1/service-requests/{request_id}/proposed-actions` | Proposal series, versions, states, and active marker |
| `GET /api/v1/proposed-actions/{action_id}` | Exact proposal version, digest, state, approval validity summary, and version |
| `GET /api/v1/proposed-actions/{action_id}/approvals` | Immutable decisions for the exact proposal version |
| `GET /api/v1/proposed-actions/{action_id}/integration-attempts` | Attempt history for the logical operation |
| `GET /api/v1/integration-attempts/{attempt_id}` | One attempt's safe provider metadata and current version |
| `GET /api/v1/audit-events?aggregate_type=&aggregate_id=&cursor=&limit=` | Authorized append-oriented evidence search |

List responses use opaque cursor pagination and return `next_cursor` when more results exist. Clients must not calculate authoritative queue membership from raw fields; the service-request query returns the backend-owned current queue.

## Demo-scenario contract coverage

| # | Approved scenario | Primary API path and result | Minimum event/audit evidence |
| --- | --- | --- | --- |
| 1 | Valid standard request | Intake `201`; start AI, record success, complete triage; request query returns `ReadyForAction`, Low/Normal, `StandardRequests` | Delivery accepted, request created, attempt/interpretation, routing and triage events |
| 2 | High-priority request | Same commands; deterministic triage response returns High and `PriorityRequests` | Routing rule/version and queue change |
| 3 | Urgent request requiring approval | Complete triage returns `HumanReview`, Urgent, `HumanReview`; later exact proposal uses approval command | Review-required and approval evidence |
| 4 | Invalid submission | Intake returns `400` or `422` by failure class; inspectable rejected delivery when transport identity is usable | Delivery rejection and safe issue codes; no request-created event |
| 5 | Missing-information case | Complete triage returns `HumanReview`; complete-human-review is guarded until evidence resolves missing items | Interpretation, review reason, queue change |
| 6 | Low-confidence AI result | AI success evidence stores confidence; backend triage returns `HumanReview` using configured rule version | Interpretation and deterministic threshold decision |
| 7 | Possible duplicate | Triage returns `DuplicateReview`; resolve-candidate command records `ConfirmedDuplicate` or `NotDuplicate` | Candidate detection/resolution and request transition |
| 8 | Repeated webhook delivery | Repeated intake returns `200` with `IdempotentReplay` and original logical intake result | Physical replay accepted and linked; no new request/work event |
| 9 | AI-provider failure | Retryable-failure callback moves request to `RetryableFailure`; retry-AI returns `202` with a new attempt | Failed attempt, request failure, recovery and new-attempt evidence |
| 10 | Mock email failure followed by retry | Start outbound `202`; retryable callback; retry-outbound `202`; success callback returns completed owner state | Two attempt records under one operation, failure/recovery/success |
| 11 | Approved outbound action | Approve exact digest; start outbound; mock success callback | Immutable approval, attempt success, action/request completion |
| 12 | Rejected outbound action | Reject exact digest; optional material-revision `201`; submit replacement; no outbound start without new approval | Immutable rejection, replacement/version events, no adapter attempt |

All scenarios use meaningful commands; none requires a generic status, queue, priority, or approval patch.

## Error contract

### Error envelope

```json
{
  "error": {
    "schema_version": "1.0",
    "code": "CONCURRENCY_CONFLICT",
    "message": "The resource changed after it was read.",
    "correlation_id": "3a9d9af4-a611-4e10-b916-50d07ff56748",
    "retryable": false,
    "current_versions": {
      "service_request": 5,
      "proposed_action": 3
    },
    "details": []
  }
}
```

Messages are human-readable but not stable for program logic. `code` and structured `details` are stable. Details use field paths and safe issue codes and exclude secrets, raw customer text, provider payloads, and stack traces.

### Status and stable-code catalog

| HTTP status | Stable codes | Meaning |
| --- | --- | --- |
| `400` | `MISSING_IDEMPOTENCY_KEY`, `INVALID_TRANSPORT_IDENTIFIER`, `MALFORMED_JSON`, `INVALID_TRANSPORT_BODY`, `INVALID_COMMAND` | Transport or command cannot be interpreted safely |
| `401` | `AUTHENTICATION_REQUIRED`, `MACHINE_AUTHENTICATION_FAILED` | Human or machine identity could not be trusted; machine failures do not reveal which signature check failed |
| `403` | `FORBIDDEN`, `SELF_APPROVAL_FORBIDDEN`, `CALLBACK_FORBIDDEN` | Actor is known but lacks endpoint, separation-of-duty, or exact-attempt permission |
| `404` | `RESOURCE_NOT_FOUND`, `ATTEMPT_NOT_FOUND` | Resource is absent or intentionally not disclosed |
| `409` | `CONCURRENCY_CONFLICT` | One or more expected aggregate versions are stale |
| `409` | `IDEMPOTENCY_CONFLICT`, `COMMAND_IDEMPOTENCY_CONFLICT` | A key was reused with contradictory canonical input |
| `409` | `INVALID_STATE_TRANSITION`, `ACTIVE_ATTEMPT_EXISTS`, `LOGICAL_OPERATION_ALREADY_SUCCEEDED`, `RETRY_NOT_ALLOWED` | Current business state blocks the command |
| `409` | `APPROVAL_REQUIRED`, `APPROVAL_NOT_VALID`, `APPROVAL_VERSION_MISMATCH`, `PROPOSAL_SUPERSEDED` | Exact-proposal approval guard failed |
| `409` | `DUPLICATE_RESOLUTION_CONFLICT`, `INTEGRATION_RESULT_CONFLICT` | Existing resolution/result contradicts the command |
| `415` | `UNSUPPORTED_MEDIA_TYPE` | Intake is not supported JSON transport |
| `422` | `VALIDATION_FAILED`, `INTAKE_VALIDATION_FAILED`, `CALLBACK_FIELD_NOT_ALLOWED` | JSON is well formed but violates schema or allowed evidence fields |
| `500` | `INTERNAL_ERROR` | Unexpected failure; no internal details exposed |
| `503` | `DEPENDENCY_UNAVAILABLE` | Required infrastructure is unavailable before a safe command outcome |

Every business guard failure uses `409` and the most specific code. A command never returns success if only part of its atomic transition committed.

## Contract evolution

- `/api/v1` changes remain backward compatible. Additive optional fields are allowed; removing fields, changing meaning, or changing stable enum values requires a new API version.
- Body and event `schema_version` values evolve independently from the URL version.
- Clients ignore unknown additive response fields but reject unknown command enum values they intend to send.
- Exact authentication libraries/configuration, payload length limits, retry timing, and physical SQL/migration details remain deferred; those choices cannot weaken these guards, the permission matrix, the proposed persistence constraints, or error semantics.
