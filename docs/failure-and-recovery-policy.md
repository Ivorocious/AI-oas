# Proposed Failure and Recovery Policy

## Status and scope

This approved Phase 1 design defines deterministic failure classification, retry eligibility, bounded recovery, stale-attempt assessment, and uncertain outbound reconciliation. Phase 2 now implements the immutable policy version, the complete AI success/failure/retry lifecycle, exact retry calculations, callback-credential replacement, manager terminal disposition, and trusted Pending/Running AI stale assessment.

Outbound failure/recovery and uncertain-result reconciliation remain unimplemented. No provider adapter, worker, n8n workflow, or event publisher is invoked by the backend.

## Immutable `FailureRecoveryPolicyVersion`

`FailureRecoveryPolicyVersion` is an immutable deployment-controlled policy represented by relation `failure_recovery_policy_versions`. It contains: policy UUID; stable policy key; semantic version; monotonic revision; content digest; effective UTC timestamp; `Draft`, `Active`, or `Retired` status; operation-kind rules; failure-code catalog; retry budgets; retry-delay schedule; stale-attempt thresholds; reconciliation rules and deadline; recovery-disposition rules; terminalization rules; and created timestamp.

There is no policy UI or generic policy API. FastAPI selects the effective policy using database UTC time. Every failed or stale attempt records the exact policy ID, semantic version, revision, and digest used for its assessment. A later version applies only to later assessments or newly created attempts; it never rewrites earlier evidence, disposition, eligibility time, budget result, or terminal result. A command whose expected policy identity is stale fails with `FAILURE_POLICY_VERSION_CONFLICT` and changes nothing.

## Failure evidence dimensions

Evidence is structured and allowlisted. The callback route name does not grant classification authority.

| Dimension | Stable values | Rule |
| --- | --- | --- |
| Operation kind | `AIInterpretation`, `OutboundAction` | Must equal the immutable logical-operation kind. |
| Failure stage | `BeforeDispatch`, `Dispatch`, `ProviderProcessing`, `ResponseValidation`, `CallbackDelivery`, `Reconciliation`, `InternalCommit` | Describes where the evidenced failure occurred, not desired owner state. |
| Provider invocation | `NotInvoked`, `Invoked`, `InvocationUnknown`, `NotApplicable` | `NotApplicable` is limited to non-provider mechanics such as callback authentication or a local commit failure. |
| Customer-facing side effect | `NotApplicable`, `KnownNotApplied`, `Applied`, `Unknown` | AI normally uses `NotApplicable`; outbound uncertainty is never inferred safe from silence. |
| Recovery disposition | `RetrySameOperation`, `ReviseProposal`, `ReconcileBeforeRetry`, `ReplaySameCommand`, `Terminal`, `NoDomainChange` | FastAPI derives this value from evidence, policy, owner state, and budget. |

Valid combinations include: AI transient provider failure with `Invoked`/`NotApplicable` to `RetrySameOperation`; outbound failure with `KnownNotApplied` to `RetrySameOperation`; material outbound defect to `ReviseProposal`; outbound `Unknown` to `ReconcileBeforeRetry`; lost callback response to `ReplaySameCommand`; callback authentication failure to `NoDomainChange`; and permanent, exhausted, or unresolved outcomes to `Terminal`.

Invalid combinations include outbound `Unknown` with `RetrySameOperation`; `Applied` with any retry or revision; AI with `Applied` or `KnownNotApplied`; `NotInvoked` at `ProviderProcessing`; `BeforeDispatch` with `Invoked`; `ReviseProposal` for AI; `ReconcileBeforeRetry` for AI; callback authentication failure that changes domain state; or any caller-supplied canonical status, queue, attempt state, or retry-eligibility boolean. FastAPI rejects inconsistent evidence with `RECOVERY_DISPOSITION_CONFLICT` or the applicable validation conflict.

## Stable failure-code catalog

All codes are immutable `UPPER_SNAKE_CASE`. “Attempt” and “budget” mean consumption of the provider-attempt ordinal and the logical-operation maximum. Required evidence is sanitized and allowlisted. Forbidden for every code: secrets, credentials, unrestricted provider bodies, stack traces, customer text, raw contact details, or arbitrary metadata.

| Code | Kind | Stage; invocation; side effect | Default disposition | Attempt / budget | Canonical effect; rationale | Required sanitized evidence; additionally forbidden |
| --- | --- | --- | --- | --- | --- | --- |
| `WORKFLOW_FAILED_BEFORE_PROVIDER_INVOCATION` | Both | `BeforeDispatch`; `NotInvoked`; AI `NotApplicable`, outbound `KnownNotApplied` | `RetrySameOperation` | Yes / yes | Retryable or exhausted; no human rationale | workflow error class, adapter version; workflow payload |
| `PROVIDER_CONNECTION_FAILED` | Both | `Dispatch`; `InvocationUnknown`; AI `NotApplicable`, outbound `Unknown` | AI retry; outbound reconcile | Yes / yes | AI retryable; outbound remains running; no rationale | correlation hash, timeout class; network trace |
| `PROVIDER_TIMEOUT` | Both | `ProviderProcessing`; `Invoked`; AI `NotApplicable`, outbound `Unknown` | AI retry; outbound reconcile | Yes / yes | AI retryable; outbound remains running | duration, correlation hash, safe hint; body |
| `PROVIDER_RATE_LIMITED` | Both | `ProviderProcessing`; `Invoked`; AI `NotApplicable`, outbound `KnownNotApplied` only with provider guarantee, otherwise `Unknown` | Retry or reconcile | Yes / yes | Retryable only when safe; no rationale | safe rate-limit class and valid Retry-After; headers containing secrets forbidden |
| `PROVIDER_TEMPORARILY_UNAVAILABLE` | Both | `ProviderProcessing`; `Invoked`; AI `NotApplicable`, outbound certainty from evidence | Retry or reconcile | Yes / yes | Retryable only when safe | safe provider status class, correlation hash; body |
| `PROVIDER_AUTHENTICATION_FAILED` | Both | `Dispatch`; `NotInvoked`; AI `NotApplicable`, outbound `KnownNotApplied` | `Terminal` | Yes / yes | Terminal configuration failure; no human rationale | credential-version reference, adapter version; secret/token |
| `PROVIDER_AUTHORIZATION_FAILED` | Both | `ProviderProcessing`; `Invoked`; AI `NotApplicable`, outbound `KnownNotApplied` when guaranteed | `Terminal` | Yes / yes | Terminal | provider decision class, correlation hash; body |
| `PROVIDER_CONFIGURATION_INVALID` | Both | `BeforeDispatch`; `NotInvoked`; AI `NotApplicable`, outbound `KnownNotApplied` | `Terminal` | Yes / yes | Terminal configuration failure | configuration-version/digest; configuration values |
| `PROVIDER_REQUEST_REJECTED` | Both | `ProviderProcessing`; `Invoked`; AI `NotApplicable`, outbound `KnownNotApplied` | AI `Terminal`; outbound `ReviseProposal` only if proposal defect, otherwise `Terminal` | Yes / yes | Retry denied; proposal may need revision | allowlisted rejection class/field codes; body/customer text |
| `PROVIDER_RESPONSE_SCHEMA_INVALID` | Both | `ResponseValidation`; `Invoked`; AI `NotApplicable`, outbound certainty from provider evidence | AI retry; outbound reconcile unless known | Yes / yes | Derived retry/reconcile/terminal | schema version, validation reason codes, response hash; response body |
| `OUTBOUND_DESTINATION_REJECTED` | Outbound | `ProviderProcessing`; `Invoked`; `KnownNotApplied` | `ReviseProposal` | Yes / yes | Request `RetryableFailure`, proposal `RetryableExecutionFailure`; revision required | destination-reference hash, rejection code; raw destination |
| `OUTBOUND_PAYLOAD_REJECTED` | Outbound | `ProviderProcessing`; `Invoked`; `KnownNotApplied` | `ReviseProposal` | Yes / yes | Revision required | proposal digest, allowlisted field codes; payload/customer text |
| `CALLBACK_RESPONSE_LOST_AFTER_COMMIT` | Both | `CallbackDelivery`; `NotApplicable`; `NotApplicable` | `ReplaySameCommand` | No / no | No new domain change; no rationale | callback command ID/key digest, response-loss class; callback body/credential |
| `CALLBACK_AUTHENTICATION_FAILED` | Both | `CallbackDelivery`; `NotApplicable`; `NotApplicable` | `NoDomainChange` | No / no | Security denial only | identity/credential version reference, denial code; credential/HMAC |
| `CALLBACK_CREDENTIAL_INVALID` | Both | `CallbackDelivery`; `NotApplicable`; `NotApplicable` | `NoDomainChange` | No / no | Use existing replacement command when plaintext delivery was lost | attempt/credential version, expiry class; plaintext/hash |
| `ATTEMPT_PENDING_STALE` | Both | `BeforeDispatch`; `NotInvoked`; AI `NotApplicable`, outbound `KnownNotApplied` | `RetrySameOperation` | Yes / yes | Backend stale assessment makes retryable or exhausted | created time, assessed time, unclaimed proof |
| `AI_ATTEMPT_RUNNING_STALE` | AI | `ProviderProcessing`; `InvocationUnknown`; `NotApplicable` | `RetrySameOperation` | Yes / yes | Retryable or exhausted | started time, assessed time, callback absence |
| `OUTBOUND_OUTCOME_UNCERTAIN` | Outbound | `ProviderProcessing`; `InvocationUnknown` or `Invoked`; `Unknown` | `ReconcileBeforeRetry` | Yes / yes | Attempt remains `Running`; reconciliation begins | started time, correlation hash, uncertainty reason |
| `RECONCILIATION_CONFIRMED_SUCCESS` | Outbound | `Reconciliation`; `Invoked`; `Applied` | `NoDomainChange` to success callback result | No / no | Same attempt succeeds; operation closes | provider correlation, evidence hash, confirmed time |
| `RECONCILIATION_CONFIRMED_NOT_APPLIED` | Outbound | `Reconciliation`; invocation retained; `KnownNotApplied` | `RetrySameOperation` | No / no | Same attempt finalizes retryable or exhausted | correlation, evidence hash, confirmed time |
| `RECONCILIATION_PERMANENT_REJECTION` | Outbound | `Reconciliation`; `Invoked`; `KnownNotApplied` | `Terminal` | No / no | Same attempt and owners terminal | rejection class, evidence hash |
| `OUTBOUND_OUTCOME_UNRESOLVED` | Outbound | `Reconciliation`; invocation retained; `Unknown` | `Terminal` | No / no | Attempt/request/proposal terminal; duplicate-risk reason | deadline, assessment time, correlation/evidence hashes |
| `RETRY_BUDGET_EXHAUSTED` | Both | Inherits underlying stage/certainties | `Terminal` | No extra / no extra | Stored terminal reason alongside underlying code | ordinal, maximum, policy identity |
| `MANAGER_TERMINAL_DISPOSITION` | Both | Inherits prior evidence | `Terminal` | No / no | Early terminalization from retryable state; rationale required | actor UUID/role, rationale reference, prior attempt/code |
| `ADMINISTRATOR_TERMINAL_DISPOSITION` | Both | Inherits prior evidence | `Terminal` | No / no | Same as manager | actor UUID/role, rationale reference, prior attempt/code |
| `INTERNAL_TRANSACTION_FAILED_BEFORE_COMMIT` | Both | `InternalCommit`; `NotApplicable`; `NotApplicable` | `ReplaySameCommand` | No / no | Rolled-back command makes no canonical change | command/correlation ID, safe error class; stack trace/SQL |

`RECONCILIATION_CONFIRMED_SUCCESS`, `RECONCILIATION_CONFIRMED_NOT_APPLIED`, and `RECONCILIATION_PERMANENT_REJECTION` describe evidence on the existing attempt and do not create or consume an additional attempt. The original underlying failure remains linked. Exhaustion similarly preserves the underlying failure code and adds terminal reason `RETRY_BUDGET_EXHAUSTED`.

## Exact budgets and delays

| Logical operation | Maximum total attempts | After failed attempt 1 | After failed attempt 2 | Failed attempt 3 |
| --- | ---: | ---: | ---: | --- |
| AI immutable input/configuration | 3 | 30 seconds | 2 minutes | Terminal exhaustion |
| Outbound across all proposal revisions | 3 | 1 minute | 5 minutes | Terminal exhaustion |

The budget is one initial attempt plus at most two later attempts. Manual, WorkflowService-requested, and BackendService-requested retries share it. No actor resets numbering or budget. Material outbound revision retains the operation, stable outbound key, ordinal sequence, and remaining budget. Success permanently closes the operation.

Changing AI input, prompt/schema intent, provider configuration, or another material AI configuration creates a different logical operation and budget only through an already valid lifecycle path. A transient failure on attempt 3 becomes terminal and stores both its underlying code and `RETRY_BUDGET_EXHAUSTED`.

There is no jitter. A valid provider `Retry-After` may lengthen but never shorten the policy delay. FastAPI safely records the hint and stores `next_eligible_at = max(policy_time, valid_provider_hint_time)`. Database UTC time controls comparison. Retry is permitted at exact equality; one instant earlier returns `RETRY_NOT_YET_ELIGIBLE` with no attempt, provider call, domain transition, or audit claiming a retry. A separately approved rejected-command operational/security audit is optional.

## Retry eligibility

A new attempt exists only when FastAPI verifies all applicable conditions in one guarded transaction: exact current request, proposal, approval, operation, and failed-attempt references; current expected versions; terminal previous attempt; derived `RetrySameOperation`; safe side-effect certainty; `next_eligible_at <= database_now`; remaining budget; no `Pending`, `Running`, or `Succeeded` sibling; no operation success; current outbound proposal and exact approval; unchanged stable outbound key; no material correction; no pending reconciliation; and valid actor/endpoint permission.

WorkflowService, OperationsAgent, ManagerApprover, Administrator, and BackendService retain only existing retry-route permissions. Permission never bypasses guards. Eligibility is derived, never accepted as a body boolean or generic mutable field.

## Same operation versus proposal revision

`RetrySameOperation` requires the same immutable AI input/configuration or the same exact approved outbound proposal to remain valid, and either known absence of the customer-facing side effect or provider-supported safe replay under the stable outbound idempotency key.

`ReviseProposal` applies to material proposal defects such as invalid destination reference, rejected customer-facing payload, invalid scheduling content, or another material defect. The failed attempt remains historical; same-proposal `retry-outbound` fails with `RECOVERY_DISPOSITION_CONFLICT`; the existing recoverable path permits `create-material-revision` only when its current guards hold; the replacement remains in the same series, logical operation, stable key, ordinal sequence, and budget; and new exact approval is mandatory. The later attempt binds the replacement proposal ID/version/digest and new approval. Correctable defects are never safe same-payload retries.

## Callback delivery recovery

A callback network failure or lost HTTP response is not provider retry. WorkflowService repeats the same callback route with the same command idempotency key, canonical body, exact attempt, and callback credential. It does not call the provider, create an attempt, consume budget, or alter `next_eligible_at`. If the first callback committed, replay returns its stored safe result under the existing credential/idempotency rules; if it rolled back, the repeated callback may commit once. A contradictory body or key conflicts. Callback authentication failures mutate no domain state and are not provider-failure records.

Loss of callback-credential plaintext is separate and uses the existing credential-replacement command. Replacement never replays a provider call or changes the attempt lifecycle.

## Stale-attempt assessment

FastAPI may execute guarded internal command `AssessStaleAttempt` as `BackendService`; it is not a public HTTP route, browser credential, or n8n-reusable route.

| Condition | Eligibility boundary | Derived behavior |
| --- | --- | --- |
| `Pending` unclaimed | Exactly 2 minutes after creation | Lock and prove no claim/provider dispatch; record `ATTEMPT_PENDING_STALE`, `NotInvoked`, and outbound `KnownNotApplied`; finalize retryable if budget remains, otherwise exhausted. Never infer provider invocation. |
| AI `Running`, no accepted callback | Exactly 5 minutes after `started_at` | Record `AI_ATTEMPT_RUNNING_STALE`; because no customer-facing outbound effect exists, finalize retryable when input remains current and budget remains, otherwise exhausted. |
| Outbound `Running`, late callback | Reconciliation starts when policy evidence indicates uncertainty; final deadline exactly 15 minutes after `started_at` | Never infer `KnownNotApplied` from silence. Keep the same attempt `Running` during bounded reconciliation. |

Below the boundary no assessment occurs; equality qualifies. Assessment uses the policy effective for that assessment and records its identity without rewriting prior evidence.

## Uncertain outbound reconciliation

An outbound `Unknown` side effect is never blindly retried:

1. Keep the same attempt and logical operation, stable outbound key, exact binding, and provider correlation.
2. WorkflowService reconciles through the adapter boundary; polling creates no attempt, domain event, or retry-budget use.
3. While unresolved, no sibling attempt or proposal revision may be created; callback authorization remains valid through at least the 15-minute deadline from `started_at`.
4. Once evidence is sufficient, submit exactly one existing final callback: confirmed applied uses success; confirmed not applied, or a provider guarantee of safe stable-key replay, uses retryable failure; permanent rejection or deadline-unresolved uses terminal failure.
5. FastAPI validates evidence and derives final attempt/request/proposal state.

At exactly the deadline, absent conclusive evidence, FastAPI derives attempt `TerminalFailure` with `OUTBOUND_OUTCOME_UNRESOLVED`, request `TerminalFailure`, and proposal `TerminalExecutionFailure`. Ordinary retry and revision are prohibited. Audit evidence says success was not asserted and duplicate-side-effect risk prevented retry. The mock adapter may simulate uncertainty but never claims real email delivery. Demo scenario 10 instead uses a known simulated `KnownNotApplied` failure.

## Callback evidence and backend derivation

For a retryable-failure callback, FastAPI validates policy, evidence, certainty, operation, ordinal, and owner versions. It may derive `RetryableFailure`, `ReviseProposal`, or `TerminalFailure` on exhaustion/incompatibility. Evidence that selects canonical status, queue, proposal/attempt state, owner, or eligibility is rejected. Responses return safe backend-derived state, disposition, attempt ordinal, maximum and remaining attempts, `next_eligible_at` when applicable, reconciliation status/deadline when authorized, and current request/proposal/attempt versions.

## Actor authority

| Actor | May | May not |
| --- | --- | --- |
| OperationsAgent | Request eligible, nonforbidden AI/outbound retries; create material revision when disposition and existing guards permit; inspect safe evidence | Classify failure; mark uncertainty safe; reset budget/delay; override approval; terminalize; reopen terminal work |
| ManagerApprover / Administrator | Same recovery requests plus existing terminal-disposition command from valid `RetryableFailure`, with documented rationale | Manufacture known-not-applied evidence; reset budget; reopen success/terminal work; bypass approval or self-approval rules |
| WorkflowService | Report sanitized exact-attempt evidence; replay callback command; reconcile adapter outcome; request already permitted retry commands | Decide state/eligibility/owner; reset budget; create attempt/operation/key/approval/proposal; blindly retry unknown outcome |
| BackendService | Select policy; derive classification/disposition/budget/time; assess stale work; terminalize exhaustion; atomically write canonical state, command result, audit, and outbox | Weaken exact binding, approval, idempotency, or terminal-state guards |

## Domain and persistence representation

`IntegrationAttempt` retains, when applicable: failure-policy ID/version/revision/digest; stage; stable failure code and linked terminal reason; invocation and side-effect certainty; derived disposition; ordinal; maximum budget; remaining attempts after assessment; `next_eligible_at`; sanitized provider Retry-After hint; reconciliation status/deadline; sanitized evidence reference/hash; terminal reason; and assessment timestamp. Immutable inputs and outcomes remain historical.

`failure_recovery_policy_versions` joins the proposed inventory before logical operations and attempts. Request/proposal summaries change only in the same guarded transaction as attempt assessment. No full provider payload, unrestricted customer data, raw contact detail, stack trace, or secret is duplicated. Any stored eligibility projection is backend-derived and bound to the exact assessment/policy, never caller-writable.

## State-machine alignment

| Case | Atomic derived result |
| --- | --- |
| Pending dispatch failure, known not invoked | Attempt `RetryableFailure` or final exhaustion; request `RetryableFailure`/`FailedRetryRequired`; outbound proposal `RetryableExecutionFailure` when applicable. |
| Retryable AI transient | Attempt `RetryableFailure`; request `RetryableFailure`/`FailedRetryRequired`; timed eligibility. |
| AI final attempt | Attempt/request `TerminalFailure`; underlying code plus exhaustion reason. |
| Outbound known-not-applied transient | Attempt `RetryableFailure`; request `RetryableFailure`; proposal `RetryableExecutionFailure`; timed eligibility. |
| Outbound proposal defect | Same terminal attempt outcome detail, request recovery summary, proposal `RetryableExecutionFailure`, disposition `ReviseProposal`; same-proposal retry denied. |
| Outbound uncertainty | Attempt remains `Running`; reconciliation status/deadline recorded; no new lifecycle enum. |
| Reconciliation confirms success | Same attempt `Succeeded`; exact proposal `Executed`; request `Completed`; operation closes. |
| Reconciliation confirms no effect | Same attempt `RetryableFailure` or exhausted terminal; eligibility derived. |
| Permanent rejection | Same attempt/request `TerminalFailure`; proposal `TerminalExecutionFailure`. |
| Deadline unresolved | Same terminal states with `OUTBOUND_OUTCOME_UNRESOLVED`. |
| Stale Pending / stale Running AI | Backend assessment follows exact thresholds and budget rules above. |
| Manager/admin disposition | Valid retryable attempt/request and applicable proposal become terminal; rationale required. |
| Early retry | `RETRY_NOT_YET_ELIGIBLE`; no canonical writes except optional denial audit. |
| Eligible retry | One next-ordinal `Pending` attempt under same operation; owner returns to execution target state. |
| Concurrent retries | Operation/owner locks and ordinal/active-sibling constraints allow one; loser replays or conflicts without a second attempt. |

## API and permission alignment

No policy-management or stale-assessment public route is added. The catalog remains 21 command intents over 20 normalized mutation templates and 13 queries. Existing `retry-ai`, `retry-outbound`, success/retryable/terminal callbacks, `mark-terminal-failure`, and attempt queries receive the guards and safe projections in this policy. Stable errors added are `RETRY_NOT_YET_ELIGIBLE`, `RETRY_BUDGET_EXHAUSTED`, `RECONCILIATION_REQUIRED`, `OUTBOUND_OUTCOME_UNRESOLVED`, `RECOVERY_DISPOSITION_CONFLICT`, and `FAILURE_POLICY_VERSION_CONFLICT`. Raw provider errors and unrestricted evidence are never returned.

## Audit and integration events

Canonical audit reason codes cover: `FAILURE_EVIDENCE_ACCEPTED`, `FAILURE_ASSESSMENT_CREATED`, `RETRY_ELIGIBLE`, `RETRY_DEFERRED`, `RETRY_BUDGET_EXHAUSTED`, `PROPOSAL_REVISION_REQUIRED`, `RECONCILIATION_STARTED`, `RECONCILIATION_SUCCEEDED`, `RECONCILIATION_KNOWN_NOT_APPLIED`, `RECONCILIATION_TERMINAL`, `RECONCILIATION_UNRESOLVED`, `STALE_ATTEMPT_ASSESSED`, `RETRY_REQUESTED`, `RETRY_ATTEMPT_CREATED`, `RETRY_REQUEST_REJECTED`, and `HUMAN_TERMINAL_DISPOSITION`.

Evidence includes stable code, attempt/operation IDs, policy identity, certainties, disposition, remaining budget, actor/command IDs, and sanitized references. PII-minimized integration events are emitted only for canonical consumer-relevant state changes: retryable failure, terminal failure, success, owner/proposal state change, and new attempt. No event is published for reconciliation polls, callback transport retries, or rejected early retries. Delivery remains at least once. Outbox publisher backoff, transport choice, retry count, and dead-letter limits remain deferred.

## Atomic transaction patterns

Each pattern authenticates the named actor, resolves scoped command idempotency first, uses database UTC time, locks the listed rows, checks expected versions and the selected immutable policy, and completes command/audit/outbox writes with owner/attempt writes or rolls everything back.

| # | Pattern; actor | Locked/read guards | Writes and result |
| ---: | --- | --- | --- |
| 1 | AI retryable callback; WorkflowService | command, credential, running attempt, operation, request; exact scope/evidence, attempts < 3 | Finalize attempt retryable; request `RetryableFailure`; policy/evidence/budget/time; audits and consumer event; stored callback result. |
| 2 | AI final failure; WorkflowService | Same, ordinal 3 | Attempt/request terminal; underlying code + exhaustion; audits/outbox; stored result. |
| 3 | Outbound known-not-applied; WorkflowService | Attempt, operation, exact proposal/approval/request | Retryable owner summaries and timed eligibility; exact evidence/audit/outbox. |
| 4 | Outbound proposal defect; WorkflowService | Same; defect allowlist | Disposition `ReviseProposal`; no retry attempt; proposal/request recovery state; revision-required audit/event. |
| 5 | Outbound uncertainty; WorkflowService/BackendService | Running attempt, operation, owners; `Unknown` evidence | Keep running; set reconciliation status/deadline and policy identity; audit reconciliation start; no lifecycle event unless consumer-relevant state changed. |
| 6 | Reconciliation success; WorkflowService | Same attempt/owners; conclusive applied evidence; no success sibling | Same attempt succeeds; operation closes; proposal executed; request completed; audit/outbox; callback result. |
| 7 | Reconciliation no effect; WorkflowService | Same; conclusive known-not-applied evidence | Same attempt retryable or exhausted; eligibility/audit/event; callback result. |
| 8 | Reconciliation rejection; WorkflowService | Same; conclusive permanent evidence | Same attempt and owners terminal; audit/event/result. |
| 9 | Deadline unresolved; BackendService | Running outbound attempt/owners; database time >= deadline; no conclusive callback | Terminal unresolved states and reason; audit/event/internal command result. Race loser conflicts/replays. |
| 10 | Stale Pending; BackendService | Pending attempt/operation/owner; time >= created+2m; prove no claim | Finalize retryable/exhausted with `NotInvoked`; owner/audit/event/internal result. |
| 11 | Stale AI Running; BackendService | Running AI attempt/operation/request; time >= started+5m; no callback | Finalize retryable/exhausted; owner/audit/event/internal result. |
| 12 | Early retry; permitted existing actor | Command, operation, previous attempt, owners; time < eligible | `RETRY_NOT_YET_ELIGIBLE`; no domain/attempt/outbox; stored rejection only if existing command policy requires, optional denial audit. |
| 13 | Eligible retry; permitted existing actor | Operation, prior terminal attempt, request and exact proposal/approval; all eligibility guards | Insert one next-ordinal `Pending` attempt/credential; update owner execution states; retry/creation audits and outbox; safe command receipt. |
| 14 | Concurrent retry | Same locks plus unique ordinal/active-sibling backstops | One commits; identical key replays, otherwise concurrency/eligibility conflict; loser writes no second attempt. |
| 15 | Early human terminal; manager/admin | Retryable attempt/request/proposal; actor role; rationale | Terminal owner/attempt summaries as applicable, rationale audit/event, stored command result; unauthorized OperationsAgent denied. |
| 16 | Callback committed, response lost; WorkflowService | Completed command record before aggregate version evaluation | Same key/body returns stored result; no locks/writes/provider call/new attempt; mismatch conflicts. |
| 17 | Later policy version | Effective policies plus target assessment rows | New assessment stores newly selected identity; earlier assessments/times/results unchanged; expected mismatch conflicts atomically. |

Any credential, evidence, version, binding, budget, deadline, approval, success-race, or state conflict rolls back attempt, owner summary, audit, command result, and outbox together. Plaintext callback credentials follow the existing one-time issuance/replacement rules and never enter these records.

## Demo scenarios

### Scenario 9 — AI-provider failure

Attempt 1 fails transiently under the active policy. FastAPI records structured evidence, sets the attempt to `RetryableFailure`, request to `RetryableFailure` in `FailedRetryRequired`, and `next_eligible_at = assessed/database time + 30 seconds` unless a longer valid provider hint applies. One instant early is rejected. At equality or later, an authorized retry creates attempt 2 under the same immutable AI operation; attempt 1 remains historical.

### Scenario 10 — mock email failure followed by retry

Mock attempt 1 reports a simulated failure with `KnownNotApplied`. Exact proposal/digest/approval/key remain valid. FastAPI sets request `RetryableFailure`, proposal `RetryableExecutionFailure`, queue `FailedRetryRequired`, and eligibility after 1 minute. At equality or later, retry creates attempt 2 under the same outbound operation and stable key. Its simulated success completes the request and executes the proposal. No real email is sent.

### Design test case — uncertain outbound outcome

A simulated late response produces `Unknown`, so the same running attempt enters bounded reconciliation. No retry or revision is allowed. Conclusive applied evidence succeeds the same attempt; conclusive no-effect evidence makes it retryable subject to budget/delay; no conclusion at exactly 15 minutes terminalizes it as `OUTBOUND_OUTCOME_UNRESOLVED`. This is a policy test case, not a new product promise.

## Future executable test requirements

Future tests, not implemented here, must cover: AI attempt 1/2 delay boundaries; outbound attempt 1/2 delay boundaries; equality and one instant before eligibility; third-attempt exhaustion; manual and WorkflowService inability to reset budget; revision preserving outbound budget; known-not-applied same-operation retry; proposal-defect same-proposal denial; Pending 2-minute and AI Running 5-minute below/equal/above boundaries; unknown outbound denial; reconciliation success/no-effect/permanent rejection; unresolved exactly at 15 minutes; callback response loss replay; callback transport retry consuming no budget; Retry-After lengthening but never shortening; concurrent retries creating one attempt; success racing retry; policy-version conflict; manager terminalization with rationale; OperationsAgent denial; WorkflowService inability to choose eligibility; atomic rollback of failure/attempt/owner/audit/command/outbox; no real-email claim; and no exactly-once claim.

## Deferred decisions

Physical SQL types/index names, configuration distribution, provider-specific reconciliation mechanics, evidence retention duration, operational UI, monitoring, and executable tests remain deferred. Outbox publisher retry/backoff/dead-letter policy is explicitly outside this decision.
