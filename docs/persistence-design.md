# Proposed Postgres Persistence Design

## Status and scope

This document defines the approved Phase 1 persistence design. Fifteen of 27 representations are structurally implemented, adding `machine_identities`, `machine_credential_versions`, and `machine_request_nonces` to the prior twelve. Twelve representations remain unimplemented. Credential values remain external; nonce insertion after verified signatures is executable, while cleanup/retention jobs and command idempotency remain unimplemented.

The names below are proposed relational names, not finalized SQL identifiers. Exact data types, lengths, encryption facilities, partitioning, and physical storage parameters remain migration-design decisions.

## Persistence-wide conventions

- Every primary and foreign key is a UUID. Trusted FastAPI or the dedicated publisher generates collision-safe UUIDv4 identifiers before insertion so one transaction can reference new domain, audit, and outbox rows without database round trips. Caller-supplied identifiers never become canonical row IDs. Primary-key and unique constraints remain the collision backstop.
- Postgres generates canonical `created_at`, `updated_at`, decision, and completion timestamps as UTC instants. Database time avoids client clock disagreement and gives every write in a transaction one authoritative time basis; localization is presentation-only.
- Mutable aggregate roots use a positive integer `version`, initialized to `1` and incremented only by an atomic compare-and-update using the expected version. Append-only children do not need optimistic versions unless they have a small explicit lifecycle.
- Stable lifecycle values use the enums already listed in the [API contracts](api-contracts.md#initial-wire-enums). Database checks or enums reject unknown values, but FastAPI decides whether a particular transition is allowed.
- Required columns are non-null at commit. A few circular intake links may be temporarily null inside one transaction and are validated by deferred constraints before commit; no committed reservation is incomplete.
- Canonical rows use foreign keys and uniqueness wherever the relationship is structural. Cross-row lifecycle rules that require authorization, hashing, current-state interpretation, or several aggregates remain guarded FastAPI transactions.
- Business history is append-oriented. Corrections create new versions, decisions, attempts, or audit facts. Security records whose only purpose is a bounded replay window may expire and be purged under controlled cleanup.

## Proposed schema inventory

In the tables below, “restricted” means operational customer or provider data; “security” means authentication or replay-protection metadata; and “safe metadata” excludes secrets, raw signatures, tokens, unrestricted PII, and full provider payloads.

| Table | Purpose, key, and important columns | Relationships and aggregate boundary | Mutability, sensitivity, and retention | Important indexes and constraints |
| --- | --- | --- | --- | --- |
| `inbound_deliveries` | PK `id`. Required: source/channel, accepted-key scope, idempotency-key digest, received time, processing status, schema version, `version`, correlation ID. Nullable by outcome: canonical payload hash, raw-body fingerprint, outcome, original delivery ID, created request ID, logical-result request ID, reservation ID, sanitized payload reference/issues/error, completed time. | Intake aggregate root. Optional FKs to the original accepted delivery, the request physically created by this delivery, the request returned as the logical result, and one `accepted_intake_keys` reservation. | Final outcome/hash/links are immutable; a recoverable `ProcessingFailure` changes only through a guarded version update. Restricted payload evidence; operational evidence is retained, while raw payload references may be expired or redacted under policy. | Index status/outcome/received time and logical-result request ID. Checks enforce outcome/link combinations; accepted-new origin is unique per created request; replay/conflict rows cannot create a request. |
| `accepted_intake_keys` | PK `id`. Required at commit: source/channel scope, idempotency-key digest, canonical payload hash, original delivery ID, request ID, original logical HTTP status, safe logical response snapshot, created time. | Dedicated accepted-intake reservation; FKs to the original accepted `inbound_deliveries` and its `service_requests` result. It is not a request aggregate or an outbound key. | Immutable after the acceptance transaction. Key digest and response snapshot are restricted replay data. Retain at least as long as intake replay protection and the referenced request; expiry policy must not silently permit duplicate creation. | Unique `(scope, idempotency_key_digest)`. Unique original delivery and request links. Deferred commit-time checks require a complete accepted-new graph and matching delivery/request ownership. |
| `contacts` | PK `id`. Required: display/fallback label, created/updated times, `version`. Nullable: normalized email, phone, preferred channel, archival marker, merge/replacement reference. | Contact aggregate root; referenced by requests and duplicate evidence. Optional self-FK preserves merge/supersession history. | Mutable only through expected-version commands. Highly restricted PII; retain while operationally required, with controlled archival/redaction rather than history-destroying cascade deletion. | Index normalized contact lookup representations used by later policy, without defining matching thresholds. Unique constraints apply only to identifiers proven globally unique; suspected matches never auto-merge. |
| `service_requests` | PK `id`. Required: originating delivery ID, contact ID, normalized description, status, created/updated times, `version`. Nullable by state: priority, current queue, category, location/context, timing preference, current interpretation/routing IDs, active proposal ID, recovery target, review/failure summary, terminal time. | Canonical request aggregate root. FKs to one accepted-new delivery/contact and current child references. Child rows belong to this request. | Expected-version mutable; terminal history is not reopened or hard-deleted. Restricted customer/operational data. | Unique originating delivery. Index queue/priority/status and contact/time. Checks constrain status/queue/priority/recovery/terminal combinations; deferred constraint validates active proposal belongs to this request. |
| `ai_interpretations` | PK `id`. Required: request ID, interpretation number, summary, suggested category, missing-information representation, confidence, input hash, schema/prompt/adapter/provider/model versions, created time. Nullable: safe provider correlation, warnings, latency/usage, superseded-by ID. | Immutable versioned child of a request; optional self-FK and link to the producing logical operation/attempt. | Insert-only except a one-time superseded link if used. Restricted because summaries may repeat customer text; raw provider payloads are excluded. Retain with request decision history. | Unique `(service_request_id, interpretation_number)` and, when applicable, producing successful operation. Index request/current ordering and input/configuration identity. |
| `decision_policy_versions` | PK `id`. Required: policy key, semantic version, monotonic revision, content digest, effective UTC time, status, immutable canonical content snapshot, created time. Nullable: retired time/reason. | Immutable policy support record; referenced by routing decisions and duplicate observations, not owned by one request. | Insert-only content; status advances only through controlled policy activation/retirement. Restricted operational configuration, retained as long as any decision references it. | Unique policy key/semantic version/revision and content digest; one active effective record per policy scope; index active/effective time. No configuration UI/API is implied. |
| `failure_recovery_policy_versions` | PK `id`. Required: policy key, semantic version, monotonic revision, content digest, effective UTC time, status, immutable policy snapshot, operation-kind rules, failure-code catalog, attempt budgets, delay schedule, stale thresholds, reconciliation deadline/rules, disposition rules, terminalization rules, and created time. Nullable: retired time/reason and retirement actor/reference metadata. | Immutable policy-support record referenced by assessed integration attempts and applicable audit evidence; not owned by one request. Each assessment retains exact policy ID/version/revision/digest. | Policy content is insert-only. Activation and retirement are controlled status transitions; prior content and derived assessments are never rewritten. Restricted operational configuration retained while referenced by attempts or audit evidence. | Unique policy key/semantic version/revision and content digest; constrain active/effective-time selection to one applicable record per operation scope and index active/effective lookup. No policy-management UI/API is implied. |
| `duplicate_candidates` | PK `id`. Required: request ID, candidate request/contact reference, policy ID/version/digest, source/candidate evidence hashes, ordered stable reason codes, deterministic score, state, created time. Nullable: sanitized display evidence, resolver actor ID, decision, rationale, resolved time, stale/superseding observation ID. | Child evidence of the subject request; FKs to policy, candidate request/contact, resolving application actor, and optional superseding observation. | Detection evidence is immutable; resolution fields are write-once and stale/superseding handling preserves history. Restricted match evidence; retain with request history. | Prevent self-candidate pairs and duplicate observations for one request/candidate/policy/evidence-hash tuple. Index current pending candidates by request/score and stale/resolved evidence by request. Decision/time/resolver must be all null or all present. |
| `routing_decisions` | PK `id`. Required: request ID, decision number, policy ID/version/revision/digest, evaluation time, canonical input snapshot/hash, category, priority, status, queue, review-required flag, ordered review/category/priority reason codes, decision source, created time. Nullable: current interpretation ID/version, AI confidence, missing-information codes, prior decision ID, reviewed-fact set ID, reviewed actor/rationale reference. | Immutable versioned child of a request; FKs to policy, same-request interpretation/prior decision/reviewed facts, and actor where applicable. Exact input references explain the current request summary. | Insert-only, restricted operational metadata, retained with lifecycle history. | Unique `(service_request_id, decision_number)`; index request/time, policy identity, and input hash. Deferred/transactional guards require child references to belong to the same request. |
| `routing_decision_duplicate_candidates` | Composite/UUID PK proposal: decision ID plus ordered position. Required: routing decision ID, duplicate-candidate ID, position, evidence role (`CurrentPending`, `ResolvedHistorical`, or `StaleHistorical`). | Normalized immutable evidence child linking one decision to candidates considered during that evaluation. FKs to routing decision and source-request candidate. | Insert-only restricted match metadata, retained with decision history. | Unique decision/candidate and decision/position; index candidate-to-decision history. A transaction validates candidate source request matches the decision request. |
| `reviewed_fact_sets` | PK `id`. Required: request ID, reviewed actor ID, reviewed-fact schema version, addressed review codes, structured allowlisted fact snapshot, rationale reference, evidence references, created time. Nullable: none beyond fact-type-specific absent values in the bounded snapshot. | Immutable child evidence of one request; FKs to application actor and later routing decisions. | Insert-only restricted operational/safety evidence, retained with decision/audit history. | Index request/time and actor/time; check fact names/enums against the approved allowlist at the backend and schema boundary. No lifecycle, queue, priority output, approval, duplicate-resolution, or retry field is representable. |
| `proposed_actions` | PK `id`. Required: request ID, proposal series ID, proposal version, outbound logical operation ID, logical action type, creator actor ID, destination/content snapshot, payload digest, state, created/updated times, `version`. Nullable: scheduling data, supersedes/superseded-by IDs, submitted time, current valid approval ID, execution summary, terminal time. | Proposed-action aggregate root/version; FKs to request, the series-owned outbound `logical_operations` row, creator, predecessor/successor, and approval. One request owns each series and operation. | Draft fields are expected-version mutable; submission freezes payload/digest; later material changes create a new row under the same operation. Highly restricted customer-facing content. Historical rows are not hard-deleted. | Unique `(proposal_series_id, proposal_version)`; every row in one series has the same operation; operation/request/series consistency; at most one active nonterminal proposal per series/request; state-dependent frozen/execution checks. |
| `proposed_action_contributors` | PK `id`. Required: proposal ID, actor ID, contribution kind (`Creator` or `MaterialEditor`), carried-forward flag, recorded time. Nullable: source proposal ID, required only for carried-forward work. | Normalized attribution child; FKs to proposal, application actor, and optional source proposal. | Insert-only. Restricted personnel metadata, retained permanently with the proposal evidence. | Uniqueness covers proposal/actor/kind/source with null treated as a value. Creator row must match `proposed_actions.creator_actor_id`; carried-forward source must be in the same series. |
| `proposal_approval_exclusions` | PK `id`. Required: proposal ID, excluded actor ID, source contributor ID, frozen time. | Frozen normalized exclusion set copied from represented contributors when the exact proposal is submitted. | Insert-only after submission; security/approval metadata retained with decisions. | Unique `(proposed_action_id, excluded_actor_id)`. FKs preserve immutable actor/source attribution; deferred guard requires every submitted proposal to have its complete exclusion set. |
| `approval_decisions` | PK `id`. Required: proposal ID, proposal version, payload digest, decision, approver actor ID/role-at-decision, decided time, correlation/command IDs. Nullable: sanitized rationale/policy reference. | Immutable decision child of one exact proposal. FK to approver and proposal; historical role is copied as evidence, not re-resolved for validity. | Insert-only, restricted rationale, retained with proposal history. | Unique `proposed_action_id` permits one effective decision per exact proposal row. Transaction verifies version/digest and exclusion absence; checks bind decision fields and forbid service actors structurally. |
| `logical_operations` | PK `id`. Required: kind (`AIInterpretation` or `OutboundAction`), request ID, created time. AI-specific required fields: input/configuration hash and prompt/schema/provider intent. Outbound-specific required field: proposal series ID. Nullable until outbound execution is first reserved: stable outbound-key digest/scope; nullable after completion: success attempt reference and safe outcome summary. | Durable operation identity owned by one request. An outbound operation belongs to one proposal series across every version; it is not bound to one proposal, approval, or adapter version. AI operations are separate per immutable input/configuration intent. | Identity, ownership, and kind-specific intent are immutable; outbound key fields change from null to one immutable value; success reference is set once. Restricted metadata retained with attempts. | Checks enforce mutually exclusive AI/outbound columns. Unique outbound operation per `(service_request_id, proposal_series_id)` and unique `(outbound_key_scope, outbound_key_digest)` once reserved. AI uniqueness follows its input/configuration identity. |
| `integration_attempts` | PK `id`. Required: logical operation ID, attempt number, operation kind, state, adapter/version intent, assigned WorkflowService identity/environment, callback-authorization expiry, created/updated times, `version`. Outbound-specific required immutable fields: proposed-action ID/version, frozen payload digest, approval-decision ID, and stable outbound-key scope/digest. Nullable by lifecycle: claim/started/completed times, safe provider correlation, retryability/uncertainty classification, sanitized error/evidence, result hash/reference. | Mutable execution child of one logical operation. Outbound attempt input binds one exact approved proposal historically even when the series later advances; AI ownership derives from the operation input/configuration. | Binding fields never change; lifecycle state moves forward only; failed/succeeded rows remain historical and are never reset. Restricted provider metadata; full provider payloads are excluded. | Unique `(logical_operation_id, attempt_number)`; partial unique active and succeeded indexes; outbound attempt proposal/request/series/operation consistency; callback deadline and lifecycle timestamp checks. Index operation/time and safe provider correlation. |
| `application_actors` | PK `id`. Required: Supabase subject, display-safe label, active status, created/updated times, `version`. Nullable: disabled time/reason metadata. | Human application identity root mapped from verified Supabase `sub`. | Controlled expected-version changes only. Security metadata; no password/token. Retain identity history for audit attribution. | Unique Supabase subject. Index active status. Disablement fields are consistent with status. |
| `application_actor_role_assignments` | PK `id`. Required: actor ID, fixed role, assigned-by actor ID, effective-from time, reason. Nullable: effective-to time and revoked-by actor ID. | Append-oriented role history; FKs to target and administrator actors. Current authorization reads the one active assignment. | Insert-only except one-time closure. Security metadata retained with audit history. | Partial unique active assignment per actor; fixed-role check allows only `OperationsAgent`, `ManagerApprover`, `Administrator`; valid non-overlapping intervals require transaction/constraint support. |
| `machine_identities` | PK `id`. Required: service type, environment, stable service ID, status, created/updated times, `version`. Nullable: disabled time/reason. | Root for `BackendService`, `WorkflowService`, or `EventPublisher`; HTTP authority remains defined by the permission matrix. | Controlled configuration changes only. Security metadata; no credentials. Retained for service audit attribution. | Unique `(environment, stable_service_id)`; service-type check; status/time consistency. |
| `machine_credential_versions` | PK `id`. Required: machine identity ID, credential version, external secret-reference identifier, status, activated time, created time. Nullable: previous-version overlap end, retired/revoked time, safe rotation reason. | Metadata child of a machine identity; actual HMAC/transport secret remains in external secret configuration. | Append/activate/retire lifecycle only. Highly restricted metadata, never secret values or raw signatures. Retain rotation history. | Unique `(machine_identity_id, credential_version)`; at most the controlled current/previous verification set active; time/status consistency. |
| `machine_request_nonces` | PK `id`. Required: machine identity ID, environment, verified credential version, nonce digest, signed timestamp, received time, expires time. | Short-lived replay record for authenticated machine requests. | Insert-only then purge after expiry plus safety margin. Security metadata; no raw signature, body, secret, or necessarily raw nonce. | Unique `(machine_identity_id, environment, nonce_digest)` across credential versions prevents rotation replay. Index expiry for cleanup. |
| `attempt_callback_credentials` | PK `id`. Required: attempt ID, operation kind, WorkflowService identity/environment, credential version, cryptographic credential hash, state, issued/expires times. Nullable: consumed, revoked, replaced times and replacement ID. | Security child of one backend-created attempt and assigned workflow identity. | Metadata lifecycle is one-way; plaintext is never stored. Hash is highly restricted and retained at least through callback-command replay protection, then may be cryptographically expired/purged by policy. | Unique `(attempt_id, credential_version)` and credential hash; partial unique active credential per attempt; state/timestamp checks; replacement stays on the same nonterminal attempt/operation/workflow scope and must not extend its callback-authorization deadline. |
| `command_idempotency_records` | PK `id`. Required: actor class and actor/service ID, command intent, route template, target type/ID, idempotency-key digest, canonical body hash, status, command/correlation IDs, created time. Nullable until completion: logical HTTP status, safe response snapshot, completed time, callback credential record ID, safe secret-delivery receipt metadata. | Replay record outside the domain aggregate but written in the same command transaction. Intake uses `accepted_intake_keys` instead. | Inserted as transaction-local `Processing`, committed only as terminal `Completed`; immutable thereafter. Snapshots never contain callback plaintext/hash. Secret-bearing first responses are intentionally not reproducible; replay returns only the stored safe receipt. | Unique full actor/intent/route/target/key scope. Same scope/body returns the permitted stored result; same scope/different body conflicts. Index target/time and cleanup eligibility. |
| `audit_events` | PK/event ID `id`. Required: schema version, event name, aggregate type/ID/version, actor type and immutable actor ID, occurred time, outcome, correlation, causation and command references, safe reason codes/metadata. Nullable: policy ID/version/digest and prompt/schema/adapter references when inapplicable. | Canonical evidence ledger, separate from domain aggregates, security telemetry, and integration events. Links may be polymorphic safe identifiers rather than cascading FKs. | Insert-only through backend audit writer. Restricted by projection; no secrets, tokens, callback hashes, raw PII/provider payloads. Not hard-deleted in MVP. | Index aggregate/version/time, correlation, command, actor/time, event name, and policy identity when present. Event IDs unique; application/database permissions deny ordinary update/delete. |
| `outbox_messages` | PK/event ID `id`. Required immutable fields: event type/schema version, aggregate type/ID/version, audit event ID, correlation/causation, allowlisted payload, created and available times. Required publication controls: state. Nullable controls: lease owner/until, published/dead-letter time and safe terminal reason. | Durable integration message created beside domain state and audit evidence. FK to the originating audit event. | Identity/payload are immutable; only publisher control fields change. Payload is PII-minimized. Retain through consumer recovery needs, then archive without changing audit history. | Index `(state, available_at)` and lease expiry; unique event ID; checks constrain claim/published/dead-letter fields. Multiple events at one aggregate version are allowed. |
| `outbox_publication_attempts` | PK `id`. Required: outbox message ID, attempt number, publisher identity, started time, state. Nullable until completion: outcome, completed time, transport message ID, safe error code/metadata. | Historical child of an outbox message; written only by EventPublisher's constrained database role/process. | Insert once as started and complete once; never reset or delete. Sanitized transport metadata is retained with outbox publication history. | Unique `(outbox_message_id, attempt_number)`; index incomplete attempts and outcome/time; publisher identity FK; completion fields change together once. |

## Lifecycle and relational constraints

### Direct Postgres enforcement

- Delivery checks permit `created_service_request_id` only for `Accepted` + `New`, whose logical-result request is the same row. `Accepted` + `IdempotentReplay` requires original delivery/reservation and original logical-result request links but no created request. `Rejected` + `Invalid` or `IdempotencyConflict` and `ProcessingFailure` have neither created nor logical-result request links.
- The original delivery referenced by `accepted_intake_keys` is accepted-new, and its request has that delivery as its unique origin. Deferred constraints validate this graph at commit.
- Service-request checks enumerate valid basic combinations. `RetryableFailure` requires a recovery target; nonfailure states do not carry one. `Completed` and `ClosedDuplicate` have no active queue. `TerminalFailure` also has no canonical active queue; failure visibility is produced by an explicit derived operational view rather than pretending the terminal record remains active work.
- Priority and category are null until deterministic triage produces them; category checks allow only the six stable wire values. `Urgent` never maps to `PriorityRequests`; review/approval checkpoints use `HumanReview`. Basic status/queue combinations are checked, while exact category, priority, duplicate, and review policy remains FastAPI-owned.
- A routing decision references exactly one immutable policy version and has unique increasing decision number within its request. Its interpretation, prior decision, reviewed-fact set, and every normalized candidate link must belong to that request. The request's current routing-decision FK must point to its own latest committed decision; the transaction updates category, priority, status, queue, reference, and request version together.
- Candidate observation uniqueness prevents duplicate identical source/candidate/policy/evidence observations. `Pending` observations meeting the policy review threshold are separately inspectable; `NotDuplicate`, confirmed, and stale evidence remains historical and cannot be silently overwritten. Candidate rows never express merge or automatic closure authority.
- Reviewed-fact sets are append-only and permit only bounded fact names/enums. They never store a final lifecycle state, queue, unrestricted priority, approval, duplicate resolution, or retry eligibility. A routing decision may reference an immutable set, but policy/authorization checks determine whether that set changes any output.
- A proposal's series/request pairing and version are unique. One outbound logical operation is unique for that request/series, and every proposal version has a required FK to it. Self-references stay in the same series, and the request's active proposal is validated as belonging to that request. Partial uniqueness prevents two active proposal rows for one series.
- `Rejected`, `Superseded`, `Executed`, and `TerminalExecutionFailure` rows fail executable-state checks. No active operation may point to those states.
- An approval row stores and matches the proposal ID, proposal version, and frozen digest. Unique proposal ID permits one effective decision. Approval rows are never updated or deleted.
- Logical-operation kind checks require outbound operations to reference one request/proposal series and AI operations to reference their immutable input/configuration identity. The outbound key becomes immutable once reserved. Each outbound attempt—not the operation—stores the exact approved proposal, digest, approval, adapter intent, and outbound-key identity. Unique outbound-key scope and succeeded-attempt indexes prevent a second success across every proposal version.
- Attempts have unique increasing numbers within an operation, one active (`Pending` or `Running`) row at most, and one `Succeeded` row at most. State/timestamp checks prevent a failed attempt from returning to `Pending`.

### Guarded FastAPI transaction enforcement

Postgres cannot decide whether an AI result is trustworthy, which active policy applies, a route is correct, duplicate evidence meets a policy threshold, a reviewed fact is authorized, a retry is eligible, an approval remains valid under current state, or an edit is materially carried forward. FastAPI therefore authenticates and authorizes the actor, selects the immutable policy, checks exact current states and expected versions, calculates canonical hashes and deterministic outputs, verifies contributor exclusions, selects audit/outbox content, and issues a short transaction whose compare-and-update and inserts are still protected by database constraints. Any constraint conflict is translated to the approved stable API error without exposing internal index names.

## Decision-policy and reviewed-fact persistence

`decision_policy_versions` is a durable immutable configuration snapshot, not a runtime-editable settings surface. Its canonical content includes stable category definitions, confidence threshold, required-information rules, priority and duplicate criteria, review/queue mapping, and reason-code catalog. FastAPI locks/reads the selected active policy during a triage or human-review transaction and records its ID, semantic version, revision, and digest in each decision and relevant audit/outbox record.

`reviewed_fact_sets` stores a schema-validated bounded fact snapshot rather than an opaque request patch. A normalized relational child table may be introduced during migration design if it improves constraint enforcement for individual facts; either representation must preserve exact actor UUID, rationale/evidence references, fact schema version, and append-only history. The proposed MVP has no array or JSON membership check for approval exclusions; those remain normalized in `proposal_approval_exclusions`. The bounded reviewed-fact snapshot is acceptable because FastAPI validates a closed fact schema and indexes request/time/actor; it does not drive authorization by arbitrary JSON membership.

`routing_decision_duplicate_candidates` preserves the ordered candidate evidence set used by one decision. It avoids placing candidate IDs in an unvalidated array, allows historical stale/not-duplicate evidence to remain visible, and lets future authorized queries project masked evidence without copying contact values into decisions. The canonical input hash includes ordered links/evidence hashes and the evaluation instant, not unrestricted customer text.

`complete-human-review` requires at least one accepted reviewed fact and creates no note-only completion record. After role, request state, expected version, policy, current interpretation, duplicate evidence, fact allowlist, rationale, and supporting-evidence guards pass, the transaction inserts one `reviewed_fact_sets` row, reruns the complete policy, inserts one complete `routing_decisions` row, and updates the request's current decision/category/priority/review summary/status/queue plus optimistic version. When review remains incomplete, status/queue remain `HumanReview`, the decision has `review_required=true` with complete outstanding codes, and the version still increments. Audit, command-idempotency, and applicable outbox writes commit or roll back with those rows.

## Accepted-intake idempotency

### Reservation and scope

`accepted_intake_keys` is the only reservation for a successfully accepted public intake. Its unique scope is the configured intake source/channel plus a digest of the opaque `Idempotency-Key`; it stores the canonical payload hash, original accepted delivery, created request, and safe original logical response. Raw keys need not be stored.

Validation that is safe before reservation includes transport identity, parsing/canonicalization, and the approved intake validation. Only a payload that is valid for new acceptance attempts the reservation insert. Rejected invalid or malformed deliveries create evidence but never an accepted reservation, so a corrected valid body may reuse that key.

Concurrent valid first submissions serialize on the unique reservation index. The first transaction inserts an incomplete reservation, creates the delivery/request graph, and completes it before commit. A competing insert waits; after the winner commits it reads the authoritative hash and returns replay or conflict. If the winner rolls back, its reservation disappears and the competitor can become the accepted intake. No broad table lock is used.

### Outcome patterns

- **New valid acceptance:** parse, canonicalize, validate, begin a transaction, insert the unique reservation, then insert/contact-link the accepted-new delivery and request. Complete the reservation with original response fields; append audit and outbox rows; commit as one unit and return `201`.
- **Identical accepted replay:** canonicalize enough to compare, lock/read the reservation, verify the same hash, insert a new accepted-replay delivery linked to the original, and append replay audit/outbox evidence. Return the stored original logical result with the new delivery ID and `200`; do not rerun current business validation or downstream work.
- **Conflicting reuse:** when a reservation exists and the canonical hash differs—or equality cannot be established—insert a rejected-conflict delivery linked to safe reservation/original references, append safe audit evidence, and commit. The reservation is unchanged and the response is `409 IDEMPOTENCY_CONFLICT`.
- **Invalid unreserved intake:** after confirming no accepted reservation exists, insert only a rejected-invalid delivery and safe audit evidence. Commit `422`; no contact, request, reservation, or normal downstream outbox work is created.
- **Malformed input with safe transport identity:** compute a raw-body fingerprint. If a reservation already exists, equality cannot be proven, so record a conflict and return `409`; otherwise insert a rejected-invalid delivery and return `400 MALFORMED_JSON`. Missing/unsafe transport identity remains an edge rejection with no canonical row.

An intake reservation namespace never overlaps `command_idempotency_records` or a backend-generated outbound-key scope. Intake keys cannot authorize, identify, or deduplicate provider operations.

## Command idempotency

Non-intake mutation scope is the authenticated actor class, immutable actor/service identity, command intent, normalized route template, target type/ID, and idempotency-key digest. The record also binds the canonical command-body hash, including expected versions, plus command/correlation IDs. A safe logical HTTP status and response snapshot are stored on completion.

The idempotency record is inserted as `Processing` inside the same transaction that locks/updates aggregates. It is changed to `Completed` before that transaction commits. Consequently:

1. An identical concurrent command blocks on uniqueness and, after the first commit, returns the stored logical result before expected-version evaluation.
2. The same scope/key with a different body hash returns `409 COMMAND_IDEMPOTENCY_CONFLICT` without domain reads or writes.
3. A process or connection failure rolls back both the uncommitted `Processing` record and all domain/audit/outbox changes. The MVP deliberately does not commit abandoned in-progress commands; a retry can safely acquire the key. Any observed committed `Processing` row is an integrity incident, not permission to rerun blindly.
4. Completed domain-guard failures may also be stored as safe logical responses so an identical replay remains stable.

Callback records additionally bind the attempt callback-credential record used. A terminal callback replay is returned only after valid HMAC authentication and proof of the same credential, command key, route, target, and body hash. Command replay protection is not provider-side-effect protection: outbound operations separately retain their backend-generated stable outbound key across attempts.

### Secret-bearing command responses

Attempt creation and callback-credential replacement return plaintext only in the first successful secret-bearing response to the assigned WorkflowService context, assembled from in-memory material after commit. Human-facing projections never contain it. `command_idempotency_records` stores the attempt ID, credential record/version, expiry, logical status, and a safe delivery receipt such as `PlaintextIssued`; it stores neither plaintext nor credential hash.

An exact replay proves that the command committed and returns the same safe resource metadata with `credential_delivery` set to `AlreadyIssued`; it cannot reproduce or fabricate plaintext. If delivery was uncertain, the assigned WorkflowService submits a new idempotency key to the guarded replacement command using the current expected credential version. Losing that replacement response is handled the same way: replay yields only a receipt, then another authorized replacement may create the next version.

## Machine nonce replay persistence

FastAPI first validates header shape, timestamp window, known enabled service/credential candidate, body digest, and HMAC signature in constant time. Only after sufficient signature validation does it perform a separate short transaction inserting `machine_request_nonces`. Persisting attacker-controlled nonces before signature verification would permit storage exhaustion and denial of legitimate requests.

The nonce digest is unique per machine identity and environment across credential versions. The verified credential version is recorded for rotation evidence, but it is intentionally not part of uniqueness: replaying one nonce through the current/previous overlap must still fail. A unique violation returns the generic `401 MACHINE_AUTHENTICATION_FAILED`. The accepted nonce remains consumed even if the later business command fails, so a legitimate retry uses a fresh signed nonce and the same command idempotency key.

Expiry is the signed timestamp window plus processing margin. A controlled cleanup job may purge expired rows only after that protection window. No HMAC secret, raw signature, request body, or token is stored.

## Attempt callback credentials

FastAPI creates a high-entropy plaintext credential in memory when it creates an attempt, hashes it, and inserts one `attempt_callback_credentials` row in the same transaction. The plaintext is returned once only after commit to the assigned WorkflowService context; rollback means it is never released.

The row binds exact attempt ID, operation kind, WorkflowService identity/environment, credential version, hash, and expiry. One partial unique active-row constraint prevents concurrent active credentials. Replacement inserts the next version and atomically marks the old row `Replaced`; revocation and consumption are one-way states. Replacement never changes attempt, operation, or workflow scope and never extends the attempt's fixed callback-authorization deadline.

A callback requires valid HMAC, a constant-time hash match, exact scope, unexpired active state, and the callback command guard. An accepted terminal result consumes the credential in the same transaction as attempt/domain/audit/outbox changes. The same terminal callback may later receive the stored command result using that consumed credential only when every idempotency binding matches. A different result, key, body, attempt, or credential returns the approved conflict/forbidden response.

Credential replacement is an explicit security command, not a lifecycle transition. It is allowed only to the HMAC-authenticated WorkflowService identity/environment assigned to the same `Pending` or `Running` attempt, before its callback-authorization deadline. The request supplies the expected active credential version. Under an attempt/credential row lock, the command marks that version `Replaced`, inserts exactly the next version for the same scope, and returns its plaintext once after commit. It cannot start, retry, complete, terminalize, invoke a provider, or change any request, proposal, approval, priority, queue, routing, or retry field.

Concurrent replacements targeting the same expected version serialize. One creates the next version; the other observes the changed version and returns `409 CALLBACK_CREDENTIAL_VERSION_CONFLICT`. Terminal or expired attempts return `409 CALLBACK_CREDENTIAL_REPLACEMENT_NOT_ALLOWED`; wrong service/attempt scope returns concealed `404` or `403 CALLBACK_FORBIDDEN`. No replacement command emits an integration event because canonical lifecycle state does not change; it appends only sanitized security audit evidence and its command-idempotency record.

Plaintext credentials and credential hashes never appear in audit events, integration events, provider payloads, ordinary logs, Git, or ordinary queries.

## Proposal attribution and immutable approvals

`proposed_actions.creator_actor_id` is immutable. Each verified material edit inserts a normalized `proposed_action_contributors` row. When a new version carries content forward, FastAPI copies the represented contributors with the source proposal and `carried_forward` fact rather than replacing their history.

Submission freezes the exact payload/digest and copies distinct represented actor IDs into `proposal_approval_exclusions`. This normalized set is indexed and foreign-keyed; it avoids opaque JSON/array membership and supports a direct existence check under the proposal lock. Later role changes never edit contributor or exclusion rows.

Approval/rejection locks the exact proposal, checks its submitted version/digest and absence of the approver UUID in the frozen exclusions, then inserts one immutable `approval_decisions` row. The unique proposal FK resolves concurrent decisions. A material revision preserves the old decision, supersedes the old proposal, creates a new attributed draft, and makes the old approval unusable; it never updates or transfers that decision.

## Logical operations and attempts

One `logical_operations` row represents one AI input/configuration intent or one outbound proposal-series side effect. AI operations are created when interpretation starts; changing AI input, prompt/schema, or configuration creates another AI operation. An outbound operation is created atomically with the first draft, belongs to that request/series, and is reused by every material revision and retry. It never stores one immutable proposal/approval binding.

Every outbound attempt instead freezes the exact proposed-action ID/version, payload digest, approval-decision ID, adapter/version intent, and stable outbound-key identity it is authorized to execute. A failed attempt on an older proposal remains historical after revision. A succeeded attempt sets the operation's success reference and blocks later attempts and material revisions under all proposal versions in that series.

Credible PostgreSQL enforcement uses:

- unique `(logical_operation_id, attempt_number)`;
- a partial unique index on `logical_operation_id` where state is `Pending` or `Running`;
- a partial unique index on `logical_operation_id` where state is `Succeeded`;
- unique `(outbound_key_scope, outbound_key_digest)` for outbound logical operations; and
- kind-specific checks preventing AI fields on outbound rows and outbound fields on AI rows.

The next attempt number is selected while the logical-operation row is locked; it must be exactly the current maximum plus one. Initial or retry creation also rechecks the no-active/no-success indexes. Outbound creation uses the proposal's existing operation, locks and validates the exact proposal/approval, reserves the operation's stable key if not already set, and copies the exact authorization snapshot to the new attempt.

An uncertain provider outcome is stored as explicit safe evidence/classification on the failed/running attempt. It is not automatically marked retryable. FastAPI permits retry only when later policy and adapter reconciliation prove safety; this design intentionally defines no retry count or classification formula.

## Canonical audit persistence

`audit_events` is the append-oriented canonical record of material commands, decisions, failures, and security actions by a trusted actor. Each row carries event/schema identity, aggregate identity and resulting version, immutable actor type/ID, database time, command/correlation/causation references, outcome, safe reason codes, sanitized structured metadata, and applicable rule/prompt/schema/adapter versions.

Human actor IDs reference `application_actors`; service actor IDs reference `machine_identities`. A public `Customer` intake action uses the backend-generated inbound-delivery/non-login reference approved by the authorization model, never a customer-supplied name, email, or identifier. Conditional foreign-key or validation-trigger mechanics for this polymorphic actor reference are deferred to migration design.

Audit rows are separate from:

- integration events, which are PII-minimized consumer notifications and can be delivered more than once; and
- security telemetry, which may describe a failed request before a trusted actor or aggregate exists.

FastAPI's database role receives insert/select projection privileges but no ordinary update/delete privilege on audit rows. EventPublisher and WorkflowService receive no audit-table write access. A restricted maintenance owner can administer the database, so this is strong append-oriented control and evidence—not a claim of absolute tamper-proofing. Redacted views and FastAPI projections enforce OperationsAgent, ManagerApprover, and Administrator visibility from the authorization model.

## Transactional outbox and publication

Every integration event is inserted into `outbox_messages` in the same transaction as its canonical state and required audit event. Its `id` is the final `event_id`; immutable fields contain event type/schema, aggregate ID/version, audit link, correlation/causation, and allowlisted payload. Publication controls (`Pending`, `Publishing`, `Published`, or `DeadLetter`) never alter canonical domain or audit results.

EventPublisher claims a small batch in a short transaction using eligible state/time, expired-lease recovery, row locks, and PostgreSQL `SKIP LOCKED` semantics. It sets a lease owner/deadline, inserts the next started `outbox_publication_attempts` row, and commits before transport I/O. Success completes that attempt with safe transport metadata and marks the message published under the matching lease; failure completes it with a safe code and returns the message to pending with a later availability time, or marks it dead-letter after configured publication policy. No retry count is fixed here.

A crash after send but before acknowledgment causes the lease to expire and the same `event_id` to be delivered again. This is intentional at-least-once delivery, never exactly-once delivery. Consumers deduplicate by `event_id`, then use aggregate version for stale/gap handling as defined by the event contract. Dead-letter records preserve payload identity and failure history for investigation and controlled requeue policy; they do not roll back the business transaction.

## Atomic transaction patterns

Transactions are short and use unique indexes, targeted row locks, and optimistic compare-and-update. Provider calls and message publication occur outside domain transactions.

### 1. New accepted intake

1. Before the transaction, establish safe transport identity, canonicalize/hash, validate, and prepare trusted UUIDs plus the safe logical response.
2. Insert the unique accepted-key reservation; on conflict, switch to replay/conflict processing. Insert or safely reference the contact, accepted-new delivery, `TriagePending` request, and complete reservation links. Deferred constraints validate the graph.
3. Insert request/delivery audit events and allowlisted outbox messages in the same transaction. No downstream attempt is invoked here.
4. Commit all rows or roll back all rows. A uniqueness, validation, or write failure creates no accepted reservation or request and returns the applicable safe error.

### 2. Accepted intake replay

1. Read and lock the reservation for the scoped key after canonical hashing; compare the stored hash without applying current business validation.
2. Insert one accepted-replay delivery linked to the reservation/original delivery/request; do not update the original request, contact, or reservation.
3. Insert replay audit evidence and its allowlisted outbox message.
4. Commit and return the stored original logical result plus the new delivery ID. Hash mismatch rolls back this path and uses the conflict pattern.

### 3. Intake idempotency conflict

1. Lock/read the existing reservation and establish a different hash or inability to prove equality.
2. Insert a rejected-conflict delivery with safe original references and no request FK; leave the reservation and original graph unchanged.
3. Insert sanitized conflict audit evidence and any approved conflict notification outbox row.
4. Commit `409 IDEMPOTENCY_CONFLICT`; constraint failure rolls back the conflict evidence without affecting the accepted result.

### 4. Complete deterministic triage

1. Resolve command idempotency, then lock/read the `TriagePending` request, current validated interpretation, current duplicate observations, and one selected active `decision_policy_versions` record. Compare expected request/policy versions and canonical evidence identities.
2. FastAPI deterministically creates or reuses candidate observations under their uniqueness guard, calculates the policy outputs, inserts one immutable routing decision plus its normalized candidate links, and atomically updates request category, priority, status, queue, current routing reference, and version.
3. Insert candidate, routing, triage, review/duplicate/ready, and queue audit events as applicable, their PII-minimized outbox messages, and the completed command response record.
4. Commit together. Stale version/policy/interpretation, pending-candidate uniqueness races, unavailable required evidence, or a constraint failure changes nothing and returns the specific conflict. No provider failure or retry outcome is inferred by this transaction.

### 4a. Complete human review and recalculate

1. Resolve command idempotency; lock the `HumanReview` request, current routing decision, current interpretation, current candidate observations, and selected active policy. Compare expected request/policy versions and reject pending material duplicate evidence before accepting facts.
2. Authorize the actor against current and recalculated priority. Insert one immutable allowlisted reviewed-fact set with actor/rationale/evidence references; calculate a new routing decision and its candidate links from the new canonical inputs.
3. Always insert one complete recalculated decision and update `current_routing_decision_id`, category, priority, review summary, status, queue, and request version. If no gate remains, use the calculated `ReadyForAction` result; an authorized Urgent disposition retains `HumanReview` as oversight queue. If review remains incomplete, keep `HumanReview`/`HumanReview`, set `review_required=true`, retain all outstanding codes, and still increment the version.
4. Insert `reviewed_facts.recorded`, `routing_decision.recalculated`, and exactly one completed/incomplete audit event; add `service_request.queue_changed` only when changed. Store the command result and only applicable PII-minimized outbox work; no integration event is required when consumer-facing status/queue remain unchanged.
5. A stale request/policy/evidence, forbidden fact, missing rationale/evidence, OperationsAgent Urgent result, unresolved duplicate, optimistic conflict, or write failure rolls back request state/version, fact set, decision, audit, command, and outbox together.

### 4b. Resolve duplicate then recalculate

1. `ResolveDuplicate` locks the source request and exact pending candidate, checks expected version and authorization, and writes one immutable resolution. A confirmed duplicate closes only through this command; a fully not-duplicate result returns the request to `TriagePending` with no queue.
2. A later guarded `CompleteTriage` reads the preserved not-duplicate/stale history plus current candidate observations and selected policy, then writes the next decision as pattern 4. Resolution and recalculation retain separate command/idempotency identities.
3. Concurrent resolution or triage conflicts leave existing candidate state and current decision untouched; neither path silently merges contacts or closes a request without the authorized confirmed resolution.

### 5. Draft and outbound-operation creation

1. Resolve command idempotency; lock the service request, compare its expected version, and verify an allowed draft checkpoint with no conflicting active proposal.
2. Generate one proposal series ID and one `OutboundAction` logical-operation ID. Insert the operation owned by that request/series with no outbound key yet, then insert proposal version `1` with a required FK to it and creator attribution; update the request's active proposal and version.
3. Insert draft/operation/request audit evidence and allowlisted outbox messages; complete the command record. No attempt, approval, provider call, callback credential, or outbound key is created.
4. Commit all rows together. Series/operation uniqueness or version failure rolls back the entire draft graph; a replay returns the original safe proposal-series/operation references.

### 6. Proposal submission

1. Resolve command idempotency; lock the request and draft proposal and compare both expected versions, active reference, and allowed states.
2. Validate required content, calculate/freeze the payload digest, ensure contributor history, insert the complete normalized exclusion set, and update proposal to `PendingApproval` plus request to `AwaitingApproval`/`HumanReview`, incrementing both versions.
3. Insert proposal/request audit events and outbox messages; complete the command result.
4. Commit atomically. Any missing attribution, concurrent edit, or state conflict leaves the draft/request unchanged.

### 7. Approval

1. Resolve command idempotency; lock the exact request/proposal and compare versions, submitted digest, active proposal, `AwaitingApproval`/`PendingApproval`, and no prior decision.
2. Verify the authenticated ManagerApprover/Administrator UUID is absent from exclusions. Insert the unique immutable approval; update proposal to `Approved`, request to `ActionPendingExecution`, queue, current approval reference, and versions.
3. Insert approval/proposal/request/queue audit events and allowlisted outbox messages; complete the command response.
4. Commit together. Self-approval, unique-decision race, stale digest/version, or state failure rolls back every change.

### 8. Rejection

1. Resolve command idempotency and lock/check the same exact proposal/request/decision/exclusion guards as approval.
2. Insert the unique immutable rejection; update proposal to `Rejected` and request to `ActionRevisionRequired` in `HumanReview`, incrementing versions. Create no logical operation or attempt.
3. Insert decision and lifecycle audit/outbox rows and complete the stored result.
4. Commit together; a concurrent decision or guard failure leaves all state unchanged.

### 9. Material proposal revision

1. Resolve command idempotency; lock the request, active source proposal, its existing outbound logical operation, and relevant attempt rows; compare expected versions and verify one request/series/operation identity, no active/successful attempt, and an allowed revision path.
2. Insert a replacement `Draft` in the same series with the same required logical-operation FK and next unique version, plus creator/editor and carried-forward contributor rows. Never insert another outbound operation. Mark the old proposal `Superseded` where applicable, move the active request reference, clear recovery data, and update request to `ActionRevisionRequired`/`HumanReview` with versions.
3. Preserve old approval/attempt rows; insert approval-validity-lost and revision lifecycle audit/outbox rows and complete the command response.
4. Commit as one unit. Version/index/operation conflicts leave the old proposal executable or retryable exactly as before.

### 10. Initial integration-attempt creation

1. Resolve command idempotency. For AI, lock the request/input and create the AI logical operation when interpretation starts. For outbound, lock the request, exact proposal/approval, and the proposal's already-existing outbound operation; verify versions, shared request/series identity, and no active/successful attempt.
2. For outbound, reserve the operation's stable key once if still null and select the next attempt number (normally `1`, but later after a failed older proposal). Insert `Pending` with immutable exact proposal ID/version/digest, approval ID, adapter/version intent, and stable key identity. For either kind, set the fixed callback-authorization deadline and insert credential version `1` hash.
3. Update owner state/version as required; insert attempt/owner audit and outbox rows; complete the command record with safe attempt/credential metadata and a plaintext-issued receipt, never the plaintext/hash.
4. Commit, then return plaintext once from memory. If delivery is lost, exact replay returns only the safe receipt; WorkflowService must use the replacement command. Any transaction failure rolls back all operation/attempt/credential/owner/evidence rows.

### 11. Retry-attempt creation

1. Resolve command idempotency; lock the logical operation, failed attempt, owner request, and exact current outbound proposal/approval when applicable. Verify retryable evidence/policy, versions, shared request/series/operation identity, and no active/successful sibling.
2. Select the next number while the same operation is locked; insert a new `Pending` attempt under its immutable outbound key. Copy the exact current proposal ID/version/digest, approval ID, adapter/version intent, and key identity to the attempt; insert callback credential version `1` for that new attempt. Older failed attempts and their proposal bindings remain unchanged.
3. Update owner recovery state/version; insert retry/attempt/owner audit/outbox rows; complete the command record with safe secret-delivery metadata only.
4. Commit and return plaintext once from memory. Lost delivery uses the replacement command; uniqueness, approval, policy, or concurrency failure creates no attempt.

### 12. Attempt success callback

1. After HMAC/nonce validation, resolve callback command idempotency and credential proof; lock the attempt, logical operation, and owner aggregates. Verify `Running`, exact scope, expected version, immutable attempt input, and no successful sibling.
2. Mark the attempt `Succeeded`, set safe result evidence, consume the credential, and set the operation success reference once. For outbound, transition only the exact proposal ID/version/digest and approval bound on the attempt plus its request; that operation-level success blocks every later proposal version, revision, start, or retry. For AI, insert the interpretation for the operation input.
3. Insert attempt/result/owner audit events and outbox messages; complete the callback command result.
4. Commit atomically. Identical replay returns the stored result; a second/different success or contradictory terminal result rolls back with `INTEGRATION_RESULT_CONFLICT`.

### 13. Retryable-failure callback

1. Authenticate, resolve callback idempotency/credential, and lock the running attempt, operation, and owners; verify exact scope and no terminal/success result.
2. Mark the attempt `RetryableFailure` with sanitized classification/evidence, consume the credential, and update request/proposal recovery state and versions. Do not create the retry attempt automatically.
3. Insert failure/recovery audit and outbox rows and complete the stored result.
4. Commit together. An uncertain outcome is recorded without asserting retry eligibility; contradictory or stale callbacks roll back.

### 14. Terminal-failure callback

1. Authenticate, resolve callback idempotency/credential, and lock the exact attempt, operation, and owners; validate terminal classification and no prior result.
2. Mark the attempt `TerminalFailure`, consume the credential, and update request/proposal terminal state, queue visibility facts, and versions. Prevent later attempt creation through state and indexes.
3. Insert terminal failure audit/outbox rows and complete the stored result.
4. Commit together. Identical replay uses the stored result; contradiction or invalid classification changes nothing.

### 15. Callback-credential replacement

1. Authenticate HMAC and nonce, resolve replacement-command idempotency, then lock the exact attempt and active callback credential. Verify the assigned WorkflowService identity/environment, expected attempt version, expected credential version, `Pending`/`Running` state, and unexpired callback-authorization deadline.
2. Generate the next plaintext in memory, hash it, mark the expected credential `Replaced`, and insert exactly the next version for the same attempt/operation/workflow scope with expiry no later than the attempt deadline. Do not update the attempt or any domain lifecycle field.
3. Insert sanitized security audit evidence and complete the command record with safe credential version/expiry/delivery-receipt metadata only; create no integration event/outbox message.
4. Commit, then return plaintext once. Exact replay returns no plaintext; a new key plus current expected version performs another recovery. Concurrent commands for one expected version yield one success and one version conflict; terminal, expired, or wrong-scope attempts create nothing.

### 16. Command-idempotency replay

1. After authentication/permission (and callback proof where applicable), look up the full scoped key before expected-version evaluation.
2. If the body hash matches a completed row, read its stored logical status/response without locking or changing domain rows. For a secret-bearing command, return only safe attempt/credential metadata and `AlreadyIssued`, never plaintext. If the body differs, return `409 COMMAND_IDEMPOTENCY_CONFLICT`.
3. Replays create no new audit/outbox/domain evidence unless a separately approved security-denial audit is required.
4. Return the original safe result or, for a secret-bearing command, the non-secret issuance receipt. A transaction-local concurrent `Processing` insert resolves by waiting for commit/rollback, never by stealing execution.

### 17. Outbox publication claim and result

1. In a short claim transaction, EventPublisher selects eligible pending or expired-lease rows with row locks/`SKIP LOCKED`, sets lease owner/deadline and `Publishing`, inserts the next started publication-attempt row, and commits.
2. Publish outside the transaction using immutable `event_id` and payload. Consumers deduplicate that ID.
3. In a new transaction, lock the message under the matching lease, complete that attempt once, and mark `Published`, reschedule `Pending`, or mark `DeadLetter` according to configured publication policy.
4. Commit publication metadata only. A crash or lease mismatch leaves canonical state untouched and permits later at-least-once redelivery.

## Supabase/Postgres responsibility boundary

| Layer | Responsibilities |
| --- | --- |
| Postgres | Foreign keys, uniqueness, basic enum/state combinations, compare-and-version updates, accepted-key and command reservations, nonce uniqueness, attempt/outbound-key uniqueness, immutable-evidence structure, atomic transactions, and durable audit/outbox storage. |
| FastAPI | Human/machine authorization, immutable policy selection, deterministic category/priority/duplicate/review routing, reviewed-fact authorization, lifecycle policy, retry eligibility, exact-proposal approval validity, self-approval checks using frozen attribution, canonical hashing, data redaction/projection, and selection of audit/outbox content. |
| n8n / WorkflowService | Consume allowlisted events, call guarded FastAPI commands, invoke adapters for exact attempts, and report evidence through scoped callbacks. It receives no canonical table-write credentials. |
| EventPublisher | Claim/read only the outbox fields needed for publication and insert/update publication metadata through a dedicated constrained database role/process. It cannot modify domain, approval, command, nonce, or audit rows. |

### Proposed Row Level Security use

- **Browser clients:** no direct canonical-table access. Supabase Auth tokens are presented to FastAPI. If Supabase exposes schemas by default, RLS denies browser roles all canonical tables; narrowly designed future read projections would require a separate ADR/contract.
- **FastAPI:** uses a server-only database identity outside the browser, with table privileges and transactions needed for canonical commands. The MVP does not depend on per-user RLS for authorization because FastAPI performs authoritative role/field/domain checks. Defense-in-depth RLS may restrict the FastAPI identity by schema, but must not fragment one atomic command transaction.
- **WorkflowService:** no Postgres role and no Supabase service-role credential. It uses HMAC-authenticated FastAPI endpoints only.
- **EventPublisher:** a dedicated nonbrowser database identity may bypass browser RLS only for outbox claim/read control fields and publication-attempt writes. It has no broader canonical privileges.

Supabase service-role credentials, database passwords, HMAC secrets, and publisher credentials remain outside Git and are never exposed to the browser.

## Retention and sensitive-data principles

No legally authoritative retention duration is chosen here. Before implementation, the owner must approve operational, privacy, security, and recovery windows. The MVP principles are:

| Data class | Classification and proposed retention behavior |
| --- | --- |
| Raw intake payloads | Highest customer sensitivity. Avoid storage when normalized evidence is sufficient; if retained by reference, encrypt/restrict and use a shorter approved window, preserving safe hashes/outcomes after expiry. |
| Normalized contact data | Restricted PII. Retain while requests require service/history; support controlled archival/redaction without erasing request/audit identity. |
| Request descriptions and location/context | Restricted customer data. Retain with the operational request; minimize copies in audit/events. |
| AI interpretations | Restricted derived customer data. Retain immutable accepted versions and version references; do not retain unrestricted raw provider payloads by default. |
| Decision-policy versions and routing decisions | Restricted operational policy/evidence. Retain immutable policy content/digest, canonical input hash, outputs, and minimal snapshots with request history so a result remains reproducible. |
| Duplicate candidates and reviewed facts | Restricted match, safety, and operations evidence. Retain observations, resolutions, masked reason codes, reviewed actor/rationale references, and immutable fact sets with decision history; do not copy full contact evidence into audit/events. |
| Proposal content and destinations | Highly restricted customer communication data. Retain exact frozen versions needed to explain approval/execution; protect from general logs/events. |
| Approval rationale | Restricted management data. Retain with immutable decision under manager/administrator projection. |
| Provider metadata and errors | Store only safe correlation/version/classification and sanitized errors with attempts. Exclude full payloads, stack traces, secrets, and customer text not required as evidence. |
| Callback credential hashes | Highly restricted security data. Retain through callback and command-replay protection; after every dependent replay window closes, controlled cryptographic expiry/purge may remove the hash while safe issuance/status metadata remains. |
| Command response snapshots | Restricted projection, potentially containing operational identifiers. Retain for a documented replay window long enough to cover client retries and side-effect safety; expire under controlled policy, never earlier than dependent callback/outbound protection. |
| Audit metadata | Append-oriented, sanitized evidence retained in the MVP; access is role-projected. Customer content and secrets are excluded. |
| Outbox payloads | PII-minimized integration data. Retain through publication recovery/consumer investigation, then archive/purge payload copies under policy without deleting canonical audit evidence. |
| Machine nonces | Short-lived security metadata. Purge after expiry plus clock-skew/processing margin; they are not business history. |
| Machine credential metadata | Retain safe version/status/rotation history; secret values live outside Postgres/Git. |
| Integration attempts and publication attempts | Append-oriented operational evidence retained with their operations/messages; sanitized diagnostics only. |

Core requests, decision-policy versions, routing decisions, duplicate candidate observations, reviewed fact sets, proposal versions, approvals, logical operations, integration attempts, and canonical audit events are not hard-deleted in the MVP. Short-lived nonces, expired raw payload references, callback hashes, response snapshots, and archived outbox payload copies may be purged only by explicit controlled retention jobs that preserve required evidence and replay guarantees.

## Proposed migration ordering

Migrations `0001_intake_persistence` through `0003_human_access_foundation` implement intake and human access. Migration `0004_ai_execution_foundation` implements the AI-only logical-operation, attempt, callback-credential, and interpretation structures. The remaining safe future sequence is:

Migration `0005_ai_execution_constraint_hardening` makes callback workflow scope nonblank and defers the replacement self-FK so one old-to-new hash-only credential transition can commit atomically. Same-attempt identity, exact next-version sequencing, deadline, authority, and plaintext issuance remain future FastAPI command guards; no replacement runtime is implemented.

Migration `0006_workflow_authentication_foundation` adds machine identities, external credential-version metadata, and nonce replay evidence. Secrets remain external, and command idempotency and nonce cleanup remain future work.

1. Required extensions, fixed enums, domains, and foundational types.
2. `application_actors`, role assignments, `machine_identities`, and credential metadata.
3. Immutable `decision_policy_versions` and `failure_recovery_policy_versions`, intake reservation/delivery, contacts, and service-request aggregates, initially with deferred circular links.
4. AI interpretations, duplicate candidates, reviewed fact sets, routing decisions, and normalized decision-to-candidate evidence links.
5. Logical operations, proposed actions, contributor attribution, and frozen approval exclusions so every proposal can require its series-owned operation FK from creation.
6. Approval decisions and exact proposal/decision constraints.
7. Integration attempts and their exact AI/outbound execution-binding constraints.
8. Command idempotency, nonce, and callback-credential security records.
9. Canonical audit events and restricted projections.
10. Outbox messages, publication attempts, and publisher privileges.
11. Deferred foreign keys, validation constraints, partial unique indexes, lifecycle checks, policy/read projections, and operational read projections after data backfill/validation.
12. Controlled demonstration policy/role/service metadata only—never secrets or credentials.

Enum value removal/renaming, narrowing a populated column, adding uniqueness to dirty data, changing canonical hashing or policy digest semantics, deleting accepted-key reservations, changing decision-policy/routing-decision identity, changing proposal-series/version identity, or altering audit/outbox event identity is difficult or unsafe to roll back once records exist. Such migrations require preflight queries, backfill, dual-read/write or compatibility plans where appropriate, and a superseding contract/ADR when semantics change. Seed rollback must not delete identities or policy records already referenced by audit evidence.

## Future persistence-focused test requirements

Executable new/replay/conflict/invalid/malformed, concurrency, migration, constraint, rollback, timezone, and evidence-retention tests now cover the implemented intake slice. The remaining items are requirements for later capabilities.

1. Concurrent accepted intake with the same key and same canonical payload creates one request and one accepted replay.
2. Concurrent accepted intake with the same key and different payload creates one request and one conflict.
3. Invalid intake followed by valid reuse of the key succeeds because invalid evidence reserved nothing.
4. Identical command replay after aggregate version advancement returns the original result.
5. Conflicting command-key reuse returns `COMMAND_IDEMPOTENCY_CONFLICT` without mutation.
6. Concurrent proposal approval attempts create one immutable decision.
7. Self-approval is rejected from the frozen normalized exclusion set despite role changes.
8. Draft creation atomically produces one proposal series and one outbound logical operation.
9. Material revision preserves the source proposal's logical-operation ID.
10. Old and replacement proposals cannot create separate outbound operations for one series/intended side effect.
11. Each outbound attempt immutably binds its exact proposal ID/version/digest, approval, adapter intent, and stable outbound key.
12. A failed attempt on an old proposal remains historical after a replacement is created and approved.
13. Success under any proposal version blocks every later attempt or revision under the operation.
14. Concurrent initial attempt creation produces one active attempt.
15. Concurrent retry creation produces one next-numbered active attempt.
16. A second successful attempt for one logical operation is prevented by the database backstop.
17. A replayed machine nonce is rejected across credential-version overlap.
18. An expired callback credential is rejected.
19. A callback credential cannot authorize another attempt or operation kind.
20. An identical terminal callback replay returns the stored result after credential consumption.
21. A contradictory terminal callback returns `INTEGRATION_RESULT_CONFLICT`.
22. A forced error proves state, audit, command record, and outbox rows roll back atomically.
23. Publisher crash after transport send but before acknowledgment leads to safe redelivery.
24. Duplicate publisher delivery is deduplicated by the consumer's `event_id` record.
25. Competing expected-version commands produce one success and one `CONCURRENCY_CONFLICT`.
26. Attempt creation commits but its initial plaintext-credential response is lost without making the attempt unrecoverable.
27. Exact command replay returns a safe receipt and neither exposes nor fabricates lost plaintext.
28. The assigned WorkflowService can replace a credential for one still-eligible attempt and receives one new plaintext version.
29. Successful replacement atomically invalidates the prior credential.
30. Concurrent replacements targeting one expected version leave one active credential and return one version conflict.
31. Wrong WorkflowService, wrong attempt, expired callback-authorization deadline, and terminal-attempt replacement are denied.
32. A replacement credential authorizes only the original attempt, operation kind, WorkflowService identity, and environment.
33. Concurrent triage commands create one current routing decision, one current request summary, and at most one candidate observation per evidence identity.
34. A routing decision cannot reference an interpretation, reviewed fact set, or duplicate candidate from another request.
35. Non-Urgent review by `OperationsAgent` and Urgent/hard-signal review by manager/admin enforce the same persisted fact/decision guards.
36. An incomplete accepted review creates a complete new decision, updates the current routing-decision reference and request version, keeps `HumanReview`/`HumanReview`, and returns complete outstanding codes.
37. Two concurrent incomplete reviews using the same expected request version produce one success and one `CONCURRENCY_CONFLICT`.
38. A forced review or triage error proves request summary/version, reviewed facts, decision/candidate links, audit, command record, and applicable outbox rows roll back together.

## Deferred implementation choices

Intake idempotency-key hashing and normalized canonical-payload hashing are implemented with SHA-256. Hashing specifications for future commands, proposals, providers, and policies, plus encryption/key management, remaining SQL types/indexes, triggers/functions, transaction isolation, pooling, partitioning, backup/restore, retention, Supabase settings, and later migration tooling remain deferred. None may weaken the approved guarantees.
