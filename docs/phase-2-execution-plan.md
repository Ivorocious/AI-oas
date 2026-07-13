# Phase 2 Completion Execution Plan

## Verified baseline

- Approved base commit: `daf4e4d2ef27931a8c428db01b8a8fca848d0764`.
- Approved migration head: `0008_callback_command_authorization_binding`.
- Baseline application-table count: 16.
- Execution branch: `phase-2-completion`, created from the approved base and pushed to origin.
- At branch creation, local `main` and `origin/main` both matched the approved base, divergence was `0/0`, and the worktree was clean.
- Baseline validation passed on Python 3.12.13, uv 0.11.28, and local PostgreSQL 17: Alembic upgrade/check, 205 offline tests, 263 integration tests, Ruff lint, and Ruff format check.

## Dependency order

1. Complete the AI attempt result and recovery lifecycle on the existing AI operation, attempt, interpretation, callback-credential, command-idempotency, audit, and outbox foundations.
2. Persist and execute deterministic triage only after successful AI evidence exists.
3. Build proposal attribution and approval only after a request can become `ReadyForAction`.
4. Generalize the existing operation/attempt machinery to mock outbound execution only after exact proposal approval exists.
5. Add protected operational projections and prove the complete lifecycle through the twelve approved scenarios.

This order preserves the accepted authority boundary: evidence enters through exact commands, FastAPI derives canonical outcomes, and state/audit/outbox/idempotency commit atomically.

## Proposed migrations

| Batch | Revision | Schema effect | Resulting table count |
| --- | --- | --- | ---: |
| 1 | `0009_failure_recovery_foundation` | Add immutable `failure_recovery_policy_versions`; add approved recovery assessment fields to attempts and request summaries. | 17 |
| 2 | `0010_deterministic_triage_foundation` | Add `decision_policy_versions`, `duplicate_candidates`, `reviewed_fact_sets`, `routing_decisions`, and `routing_decision_duplicate_candidates`; add current decision/review references to requests. | 22 |
| 3 | `0011_proposal_approval_foundation` | Add `proposed_actions`, `proposed_action_contributors`, `proposal_approval_exclusions`, and `approval_decisions`; generalize logical operations and add approved request proposal references. | 26 |
| 4 | `0012_mock_outbound_execution_foundation` only if schema changes are needed | Generalize existing operation, attempt, and callback-credential constraints/columns for exact outbound bindings and recovery; add no table. | 26 |
| 5 | No migration unless a verified query index is required | Read-model implementation and acceptance evidence only. | 26 |

Every migration will be explicit, deterministic, reversible, data-preserving, and covered by upgrade/downgrade/re-upgrade plus Alembic drift tests. An empty migration will not be created.

## Batch deliverables and test strategy

### Batch 1 — Complete the AI execution lifecycle

- Add closed success/failure callback evidence models and the three HMAC-plus-attempt-credential callback routes.
- Implement exact consumed-credential replay, immutable AI interpretation creation, attempt/operation/request completion, and safe audit/outbox writes.
- Implement immutable failure-policy selection and seed the approved demonstration policy.
- Implement retryable/terminal AI failure derivation, exact budgets/delays, retry AI, callback-credential replacement, manager/admin terminal disposition, and directly testable BackendService stale assessment.
- Unit tests cover closed evidence, policy calculation, secret-safe projections, and construction boundaries.
- PostgreSQL tests cover first/replay/conflict callbacks, concurrency, write-boundary rollback, credential replacement, retry equality/budget, stale boundaries, policy binding, authorization/scope, and all existing regressions.

### Batch 2 — Deterministic triage and review lifecycle

- Persist and seed the immutable demonstration decision policy.
- Implement the complete ordered evaluator from `docs/deterministic-decision-policy.md`.
- Implement trusted in-process BackendService `CompleteTriage` with no public route, plus human-authenticated duplicate resolution and bounded human-review recalculation.
- Tests cover every threshold/precedence boundary, candidate identity and resolution, role authority, reproducibility, concurrency, rollback, and PII-minimized evidence.

### Batch 3 — Proposal approval lifecycle

- Implement proposal series, one outbound logical operation per series, immutable contributor attribution, frozen exclusion sets, and exact approval decisions.
- Add create/edit/submit/approve/reject/material-revision commands with expected versions and command idempotency.
- Tests cover series/operation uniqueness, stable digesting, contributor carry-forward, self-approval after role changes, concurrent decisions, rejection without attempts, revision/history, success guards, rollback, and redaction.

### Batch 4 — Mock outbound execution lifecycle

- Generalize start/claim/callback/replacement/retry/terminal services for `OutboundAction` while retaining AI behavior.
- Bind every attempt to exact proposal/digest/approval/adapter/stable outbound key.
- Implement known-not-applied retry, proposal-defect revision disposition, bounded unknown-outcome reconciliation, and exact deadline assessment.
- Tests cover all success/failure/retry/reconciliation races, stable key/budget preservation across revisions, one active/one success, no provider invocation or real email, rollback, and secret/PII exclusion.

### Batch 5 — Protected queries and Phase 2 acceptance

- Implement the thirteen approved human-authenticated query projections with deny-by-default role filtering, stable cursor pagination, concealed not-found behavior, and closed safe schemas.
- Preserve the existing request-detail contract while extending its current approved summary.
- Add one isolated PostgreSQL scenario suite for all twelve approved product demonstrations, including command replay, evidence, history, and leakage assertions.
- Update all implementation-status documentation only after the final gate passes.

## Checkpoint acceptance

Each batch must pass, from `backend/`:

```powershell
uv run alembic upgrade head
uv run alembic check
uv run pytest -m "not integration"
uv run pytest -m integration
uv run ruff check .
uv run ruff format --check .
git diff --check
```

Schema-bearing batches also downgrade to the preceding head, re-upgrade, downgrade to base, re-upgrade, and confirm no drift. The execution log is updated before the exact checkpoint commit is staged, reviewed, committed, and pushed.

## Checkpoint progress

| Batch | Status | Migration | Application tables | Acceptance |
| --- | --- | --- | ---: | --- |
| 1 — AI execution lifecycle | Complete and pushed at `c65ca6f1bbb2b3c0b1c0a3841cdc348a5a7bbea4` | `0009_failure_recovery_foundation` | 17 | 431 offline and 285 PostgreSQL integration tests passed; migration round trips, Alembic drift, Ruff, format, import, health, and OpenAPI checks passed. |
| 2 — Triage and review | Complete and pushed at `65bcc8d70e158940b868792eba3e8c6fd9707400` | `0010_deterministic_triage_foundation` | 22 | 564 offline and 316 PostgreSQL integration tests passed; migration round trips, Alembic drift checks, Ruff, 165-file format check, import, health, 13-route OpenAPI inventory, policy identity, and diff checks passed. |
| 3 — Proposal and approval | Complete; checkpoint commit pending | `0011_proposal_approval_foundation` | 26 | 566 offline and 317 PostgreSQL integration tests passed; migration round trips, Alembic drift, Ruff, 175-file format check, import, 19-path OpenAPI inventory, and diff checks passed. |
| 4 — Mock outbound | Not started | Constraint-only revision if required | 26 | Pending. |
| 5 — Queries and acceptance | Not started | None expected | 26 | Pending. |

## Final acceptance strategy

Checkpoint 4 follow-up: reconcile the AI success-callback transport contract before generalizing callbacks to `OutboundAction`. The executable AI success request currently requires echoed prompt/provider/model/adapter identity beyond the shorter API-contract summary.

- Run dependency sync, offline tests, integration tests, and the unfiltered full suite.
- Run Ruff lint/format, application import, `/health`, OpenAPI reference validation, production-route inventory, and test-route exclusion.
- Exercise the complete migration lifecycle through the approved base head and base; verify exactly 26 application tables and no `outbox_publication_attempts`.
- Search for callback plaintext/hash leakage, raw idempotency keys, HMAC secrets/signatures, unrestricted provider payloads, SMTP/real-email code, provider invocation, generic lifecycle patches, and AI-written canonical routing fields.
- Verify every external mutation's authentication, authorization, idempotency, expected-version, state, concurrency, rollback, audit/outbox, and redaction coverage.
- Remove Compose resources, verify the five checkpoint commits, branch divergence `0/0`, clean worktree, and unchanged `main`/`origin/main`.

## Known risks and mitigations

- **Cross-aggregate complexity:** use explicit domain-specific services, fixed lock ordering, named constraints, and write-boundary rollback tests.
- **Callback replay ordering:** authenticate HMAC and exact credential before exposing idempotency body-conflict information; retain durable authorization binding after consumption.
- **Policy time boundaries:** use PostgreSQL UTC time for selection, stale assessment, and eligibility; test equality and one instant before.
- **Proposal/retry races:** lock request, proposal, operation, attempt, and credential rows in a consistent order and retain database uniqueness backstops.
- **Secret-bearing commands:** assemble plaintext in memory only after commit; persist only hash and safe one-time delivery metadata.
- **Evidence leakage:** use closed Pydantic models and allowlisted audit/outbox builders; test serialized records and query projections.
- **Migration growth:** keep one cohesive migration per schema-bearing batch and review generated drift instead of accepting autogenerated noise.

## Explicitly deferred Phase 3 work

Phase 2 will not implement real n8n workflows, real AI-provider invocation, a real outbound provider, EventPublisher execution, `outbox_publication_attempts`, outbox transport publication, frontend behavior, hosted deployment or Supabase setup, observability/production operations, backup/restore, or retention jobs. Those belong to **Phase 3 — Orchestration, provider adapters, and event publication** or later operational phases.
