# Proposed Authentication and Authorization Model

## Status and scope

This document defines the fixed MVP identity, authentication, and authorization model for the [API contracts](api-contracts.md), [event/n8n contracts](event-contracts.md), approved [lifecycle state machines](state-machines.md), and [deterministic triage policy](deterministic-decision-policy.md). It is an accepted technical design under [ADR 0003](decisions/0003-authentication-and-role-permissions.md), with proposed security-record storage in the [persistence design](persistence-design.md). It is not implemented functionality: no Supabase Auth configuration, middleware, role table, seed user, secret, nonce store, callback credential, migration, or application code exists.

The MVP serves one demonstration organization. It uses fixed roles and a centralized permission map rather than editable policies or enterprise RBAC.

## Authorization subjects

| Subject | Authentication | Canonical actor attribution | Purpose |
| --- | --- | --- | --- |
| `PublicCustomer` | No login; public intake transport validation only | Audit/event actor type `Customer` with a backend-generated non-login reference linked to the delivery | Submit one public intake request |
| `OperationsAgent` | Supabase Auth bearer token plus application role lookup | Verified application actor UUID and fixed role | Day-to-day operational handling |
| `ManagerApprover` | Supabase Auth bearer token plus application role lookup | Verified application actor UUID and fixed role | Operations plus Urgent review, approval, terminal disposition, and broader operational audit |
| `Administrator` | Supabase Auth bearer token plus application role lookup | Verified application actor UUID and fixed role | Operations/approval plus security audit and controlled role, machine, and application configuration |
| `BackendService` | Trusted in-process identity; not exposed as a reusable external API credential | `BackendService` actor UUID/name from controlled application configuration | Execute deterministic policy and internal commands |
| `WorkflowService` | HMAC-authenticated n8n service identity; callbacks also require attempt scope | `WorkflowService` service UUID from controlled configuration | Orchestrate provider attempts and constrained callbacks |
| `EventPublisher` | Dedicated worker identity and storage/transport credentials | `EventPublisher` service UUID from controlled configuration | Publish outbox messages and record delivery metadata |

Customer-provided IDs, names, email addresses, and correlation values are never trusted actor identities. External AI and mock-email providers are not canonical API actors; n8n invokes them and reports evidence as `WorkflowService` for an exact backend-created attempt.

## Public customer boundary

`PublicCustomer` may call only `POST /api/v1/intake/service-requests`. The safe intake response can contain delivery, request, outcome, and correlation identifiers, but those identifiers are not bearer credentials and grant no read access.

`PublicCustomer` cannot query status, contacts, requests, queues, proposals, approvals, routing, attempts, or audit history and cannot invoke internal lifecycle commands. A human application user who wants to submit through the public endpoint does so in the separate public-submission context, not by gaining intake authority from an internal role.

## Human authentication

Supabase Auth is the proposed MVP identity provider.

1. A human signs in through Supabase Auth.
2. The client receives a short-lived Supabase access token.
3. The client sends `Authorization: Bearer <token>` to FastAPI. Tokens are never placed in URLs, request JSON, logs, or source control.
4. FastAPI validates the token signature using trusted Supabase JWKS and validates issuer, audience, expiration, not-before time, subject, and the configured access-token type.
5. FastAPI maps the verified Supabase `sub` to one active application actor/profile.
6. FastAPI loads the authoritative fixed application role from application-controlled Postgres data.
7. FastAPI evaluates the centralized endpoint permission and field-access policy.
8. The service layer evaluates lifecycle, concurrency, idempotency, exact-proposal, self-approval, attempt, and retry guards.
9. The verified actor UUID and authoritative role are passed to the command and canonical audit creation.

### Role source and freshness

The MVP loads the application actor and role from Postgres for every protected request. This favors correctness and understandable revocation behavior at demonstration scale: a disablement or role change affects the next request without waiting for the access token to expire. A future bounded cache would require a short TTL, explicit invalidation/versioning, fail-closed behavior, and a documented revocation bound; it is not the proposed MVP default.

Role values in JWT custom claims, request bodies, query parameters, or frontend state are not authoritative. The verified token establishes the Supabase subject only. Role assignment is not self-service, no user-management UI is required, and controlled demo-user setup is deferred. Administrator role changes must use a later controlled process with audit evidence.

Frontend route guards improve user experience but do not authorize API operations. Supabase service-role credentials, signing keys, and administrative credentials are never exposed to the browser.

## Machine authentication

### WorkflowService HMAC contract

n8n authenticates as `WorkflowService` with these required headers:

- `X-Service-ID`
- `X-Service-Timestamp`
- `X-Service-Nonce`
- `X-Service-Signature`

The proposed algorithm is HMAC-SHA256. The signature covers this canonical byte sequence:

```text
HTTP_METHOD\n
CANONICAL_PATH_AND_QUERY\n
SERVICE_TIMESTAMP\n
SERVICE_NONCE\n
SHA256_BODY_DIGEST
```

- Method is uppercase.
- Path uses normalized encoding; query parameters are normalized and sorted so their values cannot be changed outside the signature.
- Timestamp is a UTC Unix timestamp.
- Nonce is a high-entropy unique value for that service identity.
- Body digest is lowercase hexadecimal SHA-256 of the exact transmitted body bytes; an empty body uses the SHA-256 digest of empty bytes.
- `X-Service-Signature` uses one configured encoding and is compared in constant time after the expected signature is computed.

The backend accepts a maximum clock skew of five minutes in the proposed MVP. It records each accepted nonce per service for at least the skew window plus processing margin and rejects reuse. Exact nonce persistence and cleanup belong to the next persistence design.

HMAC secrets are stored in n8n credentials or environment/secret configuration and in backend secret configuration outside Git. Each service identity and environment has separate credentials. Rotation uses a short, controlled overlap in which current and immediately previous credentials can be verified, followed by prompt retirement; service disablement fails closed. Raw secrets and signatures are never logged.

Unknown service IDs, invalid signatures, stale timestamps, and replayed nonces return the same `401 MACHINE_AUTHENTICATION_FAILED` envelope without revealing which check failed. Verification occurs before permission or domain processing.

### BackendService

`BackendService` represents trusted internal application execution after the external caller has been authenticated or an internal transaction/event has been accepted. It is not a shared API key and is not available to n8n, the browser, or external providers. Internal policy can create routing decisions, derive transitions, create attempts/outbox work, and append audit evidence, but it cannot create a human `ApprovalDecision`.

### EventPublisher

`EventPublisher` uses dedicated environment-specific storage/transport credentials to claim pending outbox work and record publication attempts. Those privileges are outside the HTTP API permission matrix. It cannot call lifecycle endpoints, read unrestricted domain payloads, or act as `WorkflowService`.

## Attempt-scoped callback authorization

Valid `WorkflowService` HMAC authentication is necessary but not sufficient for result callbacks.

When the backend creates an `IntegrationAttempt`, it creates a high-entropy opaque callback credential bound to:

- One attempt UUID
- One operation kind (`AIInterpretation` or `OutboundAction`)
- One `WorkflowService` identity/environment
- One expiration timestamp

The plaintext credential is returned once to the authorized workflow context in the first successful secret-bearing command response and is never emitted in integration events, audit metadata, provider payloads, Git, ordinary logs, or stored command responses. The backend stores only a cryptographic hash plus safe metadata such as attempt ID, expiry, issued/consumed timestamps, and credential version.

Result callbacks require both valid HMAC headers and the opaque credential, proposed as `X-Attempt-Callback-Credential`. The backend verifies the credential in constant time against the stored hash and exact attempt scope. It cannot create or authorize another attempt, select lifecycle state, or authorize a different operation kind.

After a terminal callback is accepted, the credential is consumed. A later request can receive the original idempotent callback result only when machine authentication is valid and the same callback credential hash, command idempotency key, route, and canonical body match the stored command result. A new key, different body/result, different attempt, expired credential, or replaced credential cannot use the consumed token.

If attempt creation commits but its plaintext response is lost, exact command replay returns only safe attempt/credential metadata and an `AlreadyIssued` receipt; plaintext cannot be recovered from the stored hash. The assigned WorkflowService recovers by calling `POST /api/v1/integration-attempts/{attempt_id}/commands/replace-callback-credential` with a new command key and the expected active credential version.

Replacement requires valid WorkflowService HMAC, exact assigned identity/environment, the same nonterminal attempt/operation kind, an unexpired fixed callback-authorization deadline, and an exact active credential version. It atomically marks the old version replaced and inserts one next version, whose plaintext is returned once. Concurrent commands for the same expected version yield one replacement and one version conflict. Exact replay of the replacement also returns no plaintext; another uncertain delivery requires another new replacement command. The command cannot start, retry, complete, terminalize, invoke a provider, or change domain state. AI and mock-email providers never call callback or replacement endpoints directly.

## Authorization enforcement model

Every protected request passes three distinct checks:

1. **Authentication:** establish a trusted `PublicCustomer`, human actor, or machine identity.
2. **Permission:** determine whether that subject may invoke the endpoint/command class and see the requested fields.
3. **Domain guard:** determine whether the exact state transition is valid now, including expected versions, idempotency, exact proposal/approval, self-approval, attempt ownership, and retry eligibility.

FastAPI authenticates and checks the centralized permission map before executing a domain command. The service/domain layer still enforces every business guard; endpoint permission never bypasses state. Frontend visibility, n8n workflow branches, provider results, and Administrator role do not override these checks.

Authorization uses deny by default. Resource lookup and field projection are scoped to the caller before data is returned. When `403` would confirm a hidden record, the API returns `404 RESOURCE_NOT_FOUND` while recording the actual internal denial safely.

## Separation of duties and proposal attribution

No human may approve or reject a proposal they created or materially revised.

- Every proposal version retains immutable `created_by_actor_id` attribution.
- Every content, destination, or scheduling change records the verified material editor actor UUID.
- When a proposal is submitted, the backend freezes an `approval_excluded_actor_ids` set containing the creator and all actors whose material work is represented in that payload, including carried-forward attribution from a prior version when its content remains.
- Approval/rejection compares the verified actor UUID—not display name, email, or current role label—against that frozen set.
- `OperationsAgent` can never approve or reject.
- A proposal submitted by any human, including a manager or administrator, requires a different `ManagerApprover` or `Administrator` not in the exclusion set.
- Role changes after submission do not change creator/editor history or remove anyone from the frozen exclusion set.
- Workflow and backend service identities cannot create human approvals.
- Approval delegation, emergency self-approval, and break-glass bypass are not part of the MVP.

A material revision creates a new proposal version and attribution set. Prior approval and attribution remain immutable historical evidence and do not transfer authority to the replacement.

## Permission matrix

### Notation and common conditions

- `A`: endpoint class is allowed; all normal lifecycle, version, idempotency, field, and scope guards still apply.
- `C-x`: conditionally allowed only under condition `x` plus all normal domain guards.
- `D`: denied.

| Code | Additional condition |
| --- | --- |
| `NU` | `OperationsAgent` may complete only non-Urgent review when both current and recalculated policy priority are non-Urgent. Hard safety/continuity reduction is manager/admin-only and needs an explicit reviewed fact and rationale. |
| `AP` | `ManagerApprover` or `Administrator` may decide only an exact pending proposal when their actor UUID is absent from `approval_excluded_actor_ids`. |
| `RT` | Human retry is limited to an allowed retryable AI/outbound failure with no active/successful sibling and valid approval when outbound. |
| `TM` | Manager/administrator may terminalize only retryable work and must provide the required rationale. |
| `BS` | `BackendService` only through trusted internal execution, never an external reusable credential. |
| `WF` | `WorkflowService` has valid HMAC auth, an allowlisted orchestration intent, current versions, stable command idempotency, and minimum target scope. |
| `WA` | `WorkflowService` is assigned to the exact backend-created attempt and it is eligible to be claimed/started. |
| `CR` | `WorkflowService` is assigned to the exact nonterminal attempt/environment; callback-authorization deadline and expected active credential version are current; replacement changes only credential metadata. |
| `CB` | Valid `WorkflowService` HMAC plus valid attempt-scoped callback credential and evidence-only callback body. |
| `OQ` | Human access is limited to operationally necessary records and field projection for the demonstration organization. |
| `WQ` | Workflow query is limited to the exact request/proposal/attempt needed for an assigned operation and attempt-scoped fields. |
| `MA` | Manager audit search is limited to broader operational evidence; security and machine-credential audit remains administrator-only. |

The API catalog has 21 command rows. Submission and replacement submission deliberately share one normalized route template, so these represent 21 documented command intents over 20 unique mutation route templates. Both submission intents appear below because their guards differ.

### Mutation commands

Existing retry permissions authorize only a request for recovery; they never bypass backend-derived policy, certainty, delay, budget, exact binding, approval, reconciliation, or terminal-state guards. OperationsAgent cannot terminalize. ManagerApprover and Administrator may use the existing terminal command from valid `RetryableFailure` only with rationale. WorkflowService may submit exact-attempt evidence and reconcile but cannot choose eligibility or state. BackendService alone may execute the non-HTTP `AssessStaleAttempt` internal command described by the [failure and recovery policy](failure-and-recovery-policy.md).

| # | Command endpoint/intent | PublicCustomer | OperationsAgent | ManagerApprover | Administrator | BackendService | WorkflowService | EventPublisher |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `POST /api/v1/intake/service-requests` | A | D | D | D | D | D | D |
| 2 | Start AI — `POST /service-requests/{request_id}/commands/start-ai-interpretation` | D | D | D | D | C-BS | C-WF | D |
| 3 | Complete triage — `POST /service-requests/{request_id}/commands/complete-triage` | D | D | D | D | C-BS | D | D |
| 4 | Resolve duplicate — `POST /service-requests/{request_id}/duplicate-candidates/{candidate_id}/commands/resolve` | D | A | A | A | D | D | D |
| 5 | Complete human review — `POST /service-requests/{request_id}/commands/complete-human-review` | D | C-NU | A | A | D | D | D |
| 6 | Retry AI — `POST /service-requests/{request_id}/commands/retry-ai` | D | C-RT | C-RT | C-RT | C-BS | C-WF | D |
| 7 | Mark terminal — `POST /service-requests/{request_id}/commands/mark-terminal-failure` | D | D | C-TM | C-TM | D | D | D |
| 8 | Create draft — `POST /service-requests/{request_id}/proposed-actions` | D | A | A | A | D | D | D |
| 9 | Update draft — `PUT /proposed-actions/{action_id}/draft` | D | A | A | A | D | D | D |
| 10 | Submit proposal — `POST /proposed-actions/{action_id}/commands/submit-for-approval` | D | A | A | A | D | D | D |
| 11 | Approve — `POST /proposed-actions/{action_id}/commands/approve` | D | D | C-AP | C-AP | D | D | D |
| 12 | Reject — `POST /proposed-actions/{action_id}/commands/reject` | D | D | C-AP | C-AP | D | D | D |
| 13 | Create material revision — `POST /proposed-actions/{action_id}/commands/create-material-revision` | D | A | A | A | D | D | D |
| 14 | Submit replacement — `POST /proposed-actions/{replacement_id}/commands/submit-for-approval` | D | A | A | A | D | D | D |
| 15 | Start outbound — `POST /proposed-actions/{action_id}/commands/start-outbound` | D | D | D | D | C-BS | C-WF | D |
| 16 | Retry outbound — `POST /proposed-actions/{action_id}/commands/retry-outbound` | D | C-RT | C-RT | C-RT | C-BS | C-WF | D |
| 17 | Claim/start attempt — `POST /integration-attempts/{attempt_id}/commands/start` | D | D | D | D | C-BS | C-WA | D |
| 18 | Replace callback credential — `POST /integration-attempts/{attempt_id}/commands/replace-callback-credential` | D | D | D | D | D | C-CR | D |
| 19 | Success callback — `POST /integration-attempts/{attempt_id}/callbacks/succeeded` | D | D | D | D | D | C-CB | D |
| 20 | Retryable-failure callback — `POST /integration-attempts/{attempt_id}/callbacks/retryable-failure` | D | D | D | D | D | C-CB | D |
| 21 | Terminal-failure callback — `POST /integration-attempts/{attempt_id}/callbacks/terminal-failure` | D | D | D | D | D | C-CB | D |

All paths in the matrix are under `/api/v1`; the prefix is omitted after row 1 for readability.

### Read-only queries

| # | Query endpoint | PublicCustomer | OperationsAgent | ManagerApprover | Administrator | BackendService | WorkflowService | EventPublisher |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `GET /service-requests/{request_id}` | D | C-OQ | C-OQ | A | C-BS | C-WQ | D |
| 2 | `GET /service-requests?queue=&priority=&status=&cursor=&limit=` | D | C-OQ | C-OQ | A | C-BS | D | D |
| 3 | `GET /inbound-deliveries/{delivery_id}` | D | C-OQ | C-OQ | A | C-BS | D | D |
| 4 | `GET /service-requests/{request_id}/timeline` | D | C-OQ | C-OQ | A | C-BS | D | D |
| 5 | `GET /service-requests/{request_id}/ai-interpretations` | D | C-OQ | C-OQ | A | C-BS | C-WQ | D |
| 6 | `GET /service-requests/{request_id}/duplicate-candidates` | D | C-OQ | C-OQ | A | C-BS | D | D |
| 7 | `GET /service-requests/{request_id}/routing-decisions` | D | C-OQ | C-OQ | A | C-BS | D | D |
| 8 | `GET /service-requests/{request_id}/proposed-actions` | D | C-OQ | C-OQ | A | C-BS | D | D |
| 9 | `GET /proposed-actions/{action_id}` | D | C-OQ | C-OQ | A | C-BS | C-WQ | D |
| 10 | `GET /proposed-actions/{action_id}/approvals` | D | C-OQ | C-OQ | A | C-BS | D | D |
| 11 | `GET /proposed-actions/{action_id}/integration-attempts` | D | C-OQ | C-OQ | A | C-BS | C-WQ | D |
| 12 | `GET /integration-attempts/{attempt_id}` | D | C-OQ | C-OQ | A | C-BS | C-WQ | D |
| 13 | `GET /audit-events?aggregate_type=&aggregate_id=&cursor=&limit=` | D | D | C-MA | A | C-BS | D | D |

All query paths are under `/api/v1`. `OperationsAgent` uses the request-specific timeline instead of unrestricted audit search. `EventPublisher` accesses only outbox publication storage through its dedicated worker boundary, not these APIs. The exact-attempt WorkflowService projection may include safe active credential-version/expiry and callback-authorization deadline metadata needed for replacement, but never plaintext or a hash.

## Field-level access

### Notation

- `W`: accepted only as public intake input; never readable by the public subject.
- `O`: operational minimum, scoped and redacted.
- `B`: broader operational detail for approval/management.
- `S`: security/administrator metadata, still redacted of secrets.
- `I`: internal backend use only as required by the command.
- `T`: exact attempt-scoped workflow data only.
- `M`: metadata only; secret/token value is never readable.
- `N`: no access.

| Field class | PublicCustomer | OperationsAgent | ManagerApprover | Administrator | BackendService | WorkflowService | EventPublisher |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Contact name, email, phone | W | O | O | O | I | T for approved outbound destination only | N |
| Request description | W | O | O | O | I | T for AI attempt only | N |
| Location/service context | W | O | O | O | I | T only when required by exact AI attempt | N |
| Proposed message/scheduling content | N | O | O | O | I | T: exact frozen approved payload for mock attempt | N |
| Approval status | N | O | O | O | I | T: validity fact only when needed for assigned outbound attempt | N |
| Approval rationale | N | N | B | B | I | N | N |
| AI interpretation | N | O | O | O | I | T: exact AI input/result context only | N |
| Provider/adapter metadata | N | O: safe summary | B: safe summary | S: security-safe detail | I | T for assigned attempt | Envelope publication fields only |
| Raw or sanitized errors | Intake-safe issue codes only | O: sanitized | B: sanitized | S: security-safe, never secrets | I: minimize raw payloads | T: sanitized assigned-attempt evidence | Publication errors only |
| Audit metadata | N | O: request timeline | B: broader operational | S: operational and security | I | N | Outbox delivery metadata only |
| Machine credential information | N | N | N | M: identity/status/rotation metadata, never secret | Secret configuration only | Own credential through n8n secret storage, never API | Own credential through secret storage, never API |
| Callback credential information | N | N | N | M: attempt/scope/expiry metadata, never token/hash | Hash and issuance/validation context | T: plaintext once in the first initial/replacement response for the assigned attempt; never queryable or replayable later | N |

General logs and integration events contain no customer PII. Raw provider payloads are not exposed by default. The mock outbound adapter receives only the approved destination and frozen payload needed to simulate the exact action. n8n receives only exact attempt context, not broad customer, proposal, or audit records.

## Authentication and authorization errors

Errors use the existing [API error envelope](api-contracts.md#error-contract).

| Status/code | Use |
| --- | --- |
| `401 AUTHENTICATION_REQUIRED` | Missing, expired, malformed, wrong-type, or otherwise invalid human bearer token |
| `401 MACHINE_AUTHENTICATION_FAILED` | Unknown/disabled service, invalid HMAC, stale timestamp, replayed nonce, or malformed machine-auth headers; response does not identify the failed check |
| `403 FORBIDDEN` | Authenticated human or machine lacks endpoint permission |
| `403 SELF_APPROVAL_FORBIDDEN` | Authorized approval role attempts to approve/reject a proposal whose frozen attribution excludes that actor UUID |
| `403 CALLBACK_FORBIDDEN` | Valid `WorkflowService` identity lacks a valid credential/scope for the exact attempt |
| `404 RESOURCE_NOT_FOUND` | Used instead of `403` when revealing resource existence would leak protected information |
| `409` with existing stable code | Caller is authenticated and permitted, but concurrency, idempotency, lifecycle, approval validity, active attempt, result conflict, or retry guard fails |
| `409 CALLBACK_CREDENTIAL_VERSION_CONFLICT` or `CALLBACK_CREDENTIAL_REPLACEMENT_NOT_ALLOWED` | Replacement expected a stale active version, or the attempt is terminal/expired/ineligible |

Responses never disclose token claims, expected signatures, nonce history, role mappings, callback-token state, hidden resource identity, or security configuration.

## Security audit and telemetry

| Event/failure | Canonical audit evidence | Security telemetry |
| --- | --- | --- |
| Material command succeeds | Yes: verified actor UUID/role or service identity, command, aggregate/version, correlation, outcome | Optional operational metric |
| Known actor denied permission | Yes when safely attributable; target reference stored internally even if response is `404` | Yes, rate-limited |
| Self-approval denied | Yes: proposal/version, actor UUID, exclusion reason code; no content | Yes |
| Human login/token validation fails before actor trust | No canonical actor claim | Yes, sanitized and rate-limited |
| Unknown service, invalid HMAC, invalid timestamp | No canonical service attribution | Yes; never log secret/signature/body |
| Replayed nonce after an otherwise valid service signature | Yes as service-security evidence when identity is safely verified; no business transition | Yes |
| Invalid callback scope after valid WorkflowService auth | Yes when the attempt can be safely identified; otherwise no aggregate audit | Yes |
| Callback credential replaced or replacement denied | Yes after trusted WorkflowService identity: attempt ID, old/new safe version metadata or denial code; never plaintext/hash | Yes |
| Role assigned, changed, or actor disabled | Yes with administrator actor, target actor, old/new role/status, time, reason | Yes |
| Machine identity rotated or disabled | Yes with administrator actor, service ID/environment, credential version metadata, time; no secret | Yes |
| Administrator application/security configuration changes | Yes with old/new safe metadata and reason | Yes |

Canonical audit and telemetry never store bearer tokens, passwords, HMAC secrets, raw signatures, callback credentials/hashes, Supabase service-role keys, unrestricted PII, full provider payloads, or stack traces. Untrusted identifiers supplied by a failed caller are treated as claims in telemetry, not verified actors in canonical audit.

## Deferred implementation decisions

The persistence design now defines proposed actor/role records, nonce uniqueness/retention, proposal attribution, callback hashes/expiry, machine metadata, audit/outbox storage, and database access boundaries. Exact libraries, SQL migrations, Supabase project settings, HTTP middleware, secret manager, token lifetimes, and setup instructions remain deferred and cannot weaken this model.
