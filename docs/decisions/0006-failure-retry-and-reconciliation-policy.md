# ADR 0006: Failure, Retry, and Reconciliation Policy

- Status: Accepted for Phase 1 design
- Date: 2026-07-11

## Context

AI and mock-outbound attempts need deterministic recovery without letting provider evidence, callbacks, humans, or workflows select canonical state. Recovery must preserve exact proposal approval, logical-operation identity, bounded attempts, stable outbound idempotency, immutable evidence, and safe handling of uncertain customer-facing outcomes.

## Decision

1. `FailureRecoveryPolicyVersion` is immutable, versioned, deployment-controlled, and authoritative. The proposed relation is `failure_recovery_policy_versions`; every assessment stores exact policy ID, semantic version, monotonic revision, and digest.
2. Workflow/provider evidence is nonauthoritative. FastAPI validates structured evidence and derives failure code, certainty, recovery disposition, retry budget, eligibility time, and canonical state.
3. Each AI logical operation and each outbound logical operation has at most three total attempts: one initial plus two retries. AI delays are 30 seconds then 2 minutes; safe outbound delays are 1 minute then 5 minutes. There is no jitter; valid provider Retry-After can only lengthen delay; equality at `next_eligible_at` permits retry.
4. Budget belongs to the logical operation. Manual/service retry cannot reset or bypass budget/delay. Outbound proposal revision retains the same operation, stable key, ordinal sequence, and budget.
5. A material proposal defect requires `ReviseProposal`, a replacement proposal, and new exact approval; it is not a same-payload retry.
6. Callback delivery replay uses the same callback command identity/body/attempt/credential. It is not provider retry, creates no attempt, and consumes no budget. Lost credential plaintext uses the existing replacement command.
7. An unknown outbound outcome requires reconciliation on the same running attempt. It is never blindly retried. Conclusive evidence resolves through one existing final callback.
8. The outbound reconciliation deadline is exactly 15 minutes from `started_at`, and callback authorization cannot expire sooner. An unresolved outcome at the deadline becomes attempt/request `TerminalFailure` and proposal `TerminalExecutionFailure` with `OUTBOUND_OUTCOME_UNRESOLVED`; ordinary retry/revision is forbidden.
9. BackendService may internally assess unclaimed `Pending` attempts at exactly 2 minutes and AI `Running` attempts without accepted callback at exactly 5 minutes. No public route is added. Silence never makes an outbound side effect known not applied.
10. ManagerApprover and Administrator may use the existing terminal-disposition command from valid `RetryableFailure` with rationale. OperationsAgent cannot terminalize. No actor may manufacture certainty, reset budget, bypass approval, or reopen terminal/successful work.
11. PII-minimized consumer events accompany only canonical consumer-relevant state changes. Callback transport retries, reconciliation polls, and rejected early retries do not require integration events.
12. Outbox publisher retry, backoff, transport, and dead-letter policy remains deferred.

The complete catalog, combination rules, state transitions, transaction patterns, scenarios, and future test requirements are defined in the [failure and recovery policy](../failure-and-recovery-policy.md).

## Consequences

Recovery is deterministic and auditable; retry races cannot create two attempts; final-attempt transient failures terminalize; uncertainty favors duplicate prevention over optimistic replay; and policy evolution does not rewrite history. The costs are additional immutable policy/evidence fields, provider-specific reconciliation work, bounded waiting, and more transactional guards.

## Rejected alternatives

- Caller-supplied `retry_eligible`, state, queue, priority, or attempt-state patches.
- Unlimited/manual budget resets or delay bypasses.
- Treating a corrected proposal as a new outbound budget.
- Blind retry after an unknown customer-facing side effect.
- Treating a callback transport failure as another provider attempt.
- Adding a public generic stale-assessment or policy-management API.
- Adding an `OutcomeUnknown` lifecycle enum when the existing running attempt can represent bounded reconciliation.

## Implementation status

Documentation only. No application code, SQL migration, ORM model, OpenAPI generation, automated test, n8n workflow, provider adapter, background worker, Supabase configuration, deployment file, monitoring, publisher policy, or scaffolding was created. The outbound adapter remains proposed, mock-only, and sends no real email. Event delivery remains at least once; no exactly-once transport guarantee is made.
