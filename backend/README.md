# Backend executable foundation

This directory contains the runnable FastAPI foundation, `GET /health`, PostgreSQL/SQLAlchemy/Alembic persistence, atomic public intake, the complete bounded AI-attempt lifecycle, deterministic triage/review, proposal approval, accepted Checkpoint 4 mock-outbound execution/recovery, and the Checkpoint 5 protected query candidate. Checkpoint 4 is recorded at commit `4735ce9d78f2f912d7ad93060a1589f138183052`. The locally validated Checkpoint 5 candidate remains uncommitted, unpushed, unpublished, and pending Orchestration acceptance. Migration head `0012_mock_outbound_execution_foundation` retains 26 application tables; OpenAPI exposes 32 path templates and 33 operations.

It exposes `POST /api/v1/service-requests/{request_id}/commands/start-ai-interpretation` to an HMAC-authenticated `WorkflowService`. The command creates one logical operation, one `Pending` attempt, hash-only callback authorization, safe audit evidence, and one pending outbox row atomically. Claim/start and result callbacks change canonical state, but FastAPI never invokes an AI provider.

The HMAC-authenticated `start-outbound` command creates a mock-only outbound attempt for an exactly approved proposal. `retry-outbound`, generalized claim/start and callbacks, credential replacement, stale assessment, terminal disposition, and 15-minute uncertainty reconciliation reuse the same stable operation key and three-attempt budget. Callback success records simulated `Applied` evidence only: there is no provider invocation, SMTP delivery, real email, n8n workflow, or EventPublisher execution.

## First setup

```powershell
cd C:\Users\ivorr\DevPortfolio\general-service-suite\backend
Copy-Item .env.example .env
uv sync
docker compose up -d postgres
uv run alembic upgrade head
```

The `.env` file is local configuration and must not be committed. The committed `.env.example` contains only nonproduction defaults matching the local Compose service. PostgreSQL is exposed on `127.0.0.1:55432` to avoid the conventional local port. Compose is for local development and integration testing only; no production Supabase project is configured.

The `AI_OPS_SUPABASE_ISSUER`, `AI_OPS_SUPABASE_AUDIENCE`, `AI_OPS_SUPABASE_JWKS_URL`, and `AI_OPS_JWKS_CACHE_SECONDS` values configure asymmetric access-token verification. The committed values are placeholders. Never commit a real token, private key, JWT secret, Supabase service-role key, or user identifier. Tests use local/fake keys and require no hosted Supabase project.

## Run the API

```powershell
uv run uvicorn ai_operations_automation.main:app --reload
```

## Verify

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

## Submit, replay, and conflict

```powershell
$headers = @{ "Idempotency-Key" = "demo-intake-key-001" }
$body = @{
  schema_version = "1.0"
  contact = @{ display_name = "Jane Doe"; email = "jane@example.com"; preferred_channel = "Email" }
  service_request = @{ description = "The air-conditioning unit is leaking."; location_context = "Second-floor office" }
} | ConvertTo-Json -Depth 4

# New acceptance: 201
Invoke-WebRequest http://127.0.0.1:8000/api/v1/intake/service-requests -Method Post -Headers $headers -ContentType "application/json" -Body $body

# Identical replay: 200
Invoke-WebRequest http://127.0.0.1:8000/api/v1/intake/service-requests -Method Post -Headers $headers -ContentType "application/json" -Body $body

# Same key, changed body: 409
$changed = $body.Replace("is leaking", "is making a loud noise")
Invoke-WebRequest http://127.0.0.1:8000/api/v1/intake/service-requests -Method Post -Headers $headers -ContentType "application/json" -Body $changed
```

## Test and check

```powershell
uv run pytest -m "not integration"
uv run pytest -m integration
uv run ruff check .
uv run ruff format --check .
```

Integration tests require the Compose PostgreSQL service. Foundation tests do not.

The reproduced Checkpoint 5 gate passed 584 offline tests, 375 PostgreSQL integration tests, and 959 unfiltered tests. The isolated twelve-scenario suite passed 12/12; migration `0012 → 0011 → 0012` and `0012 → base → 0012` round trips and both drift checks passed. The application has 26 modeled tables and 27 physical public tables including `alembic_version`, with no `outbox_publication_attempts`.

## Start AI interpretation

The production command requires `X-Service-ID`, `X-Service-Timestamp`, `X-Service-Nonce`, `X-Service-Signature`, and a visible-ASCII `Idempotency-Key`. `X-Correlation-ID` is optional; the backend generates one when absent and returns the accepted value in the response body and header. The HMAC covers the exact raw JSON body. Its closed body is:

```json
{
  "schema_version": "1.0",
  "expected_versions": { "service_request": 1 },
  "command": {}
}
```

The first committed execution returns `202 Accepted`, a `Pending` attempt, and one opaque callback credential with `credential_delivery: PlaintextIssued`. An exact replay with a fresh HMAC nonce returns `200 OK`, the original safe identifiers, and `credential_delivery: AlreadyIssued` without plaintext. Only the SHA-256 credential hash is stored. Do not place a real machine HMAC key, callback credential, or signature in documentation, `.env`, Git, or logs.

## Claim/start AI attempt

`POST /api/v1/integration-attempts/{attempt_id}/commands/start` requires the same four WorkflowService HMAC headers plus `Idempotency-Key`; correlation remains optional. Its closed body is:

```json
{
  "schema_version": "1.0",
  "expected_versions": { "integration_attempt": 1 },
  "command": {}
}
```

The authenticated stable service ID and environment must exactly match the backend-created assignment. With the expected version and valid owner/callback context, the command moves only that attempt from `Pending` to `Running`, increments its version, and records PostgreSQL start time plus safe audit/outbox evidence. It returns no callback credential and invokes no provider. Exact replay returns the original safe `200 OK` result with the current correlation ID.

## Attempt-scoped callback authentication

WorkflowService HMAC authentication is necessary but insufficient for callbacks. The reusable callback verifier additionally requires exactly one `X-Attempt-Callback-Credential` proving authority over the exact assigned `Running` attempt. Only the SHA-256 hash is stored; candidate rows are loaded by attempt ID and compared in constant time without placing the supplied value or digest in SQL predicates.

Verification runs inside an explicit caller-owned SQLAlchemy transaction. It locks and validates the attempt, frozen operation intent, owner request, credential history, assignment/environment, highest credential version, and PostgreSQL-controlled expiry. The returned immutable safe context remains usable only in that same active session and transaction.

The production `succeeded`, `retryable-failure`, and `terminal-failure` callback routes require WorkflowService HMAC plus the exact attempt credential. Success stores one immutable advisory interpretation while leaving the request `TriagePending`. Failure callbacks accept only allowlisted evidence; the backend derives policy identity, disposition, remaining budget, retry eligibility, request state, audit, and outbox evidence. Exact replay after credential consumption requires the original command key and body plus the consumed credential's durable authorization binding.

`POST /api/v1/integration-attempts/{attempt_id}/commands/replace-callback-credential` replaces only an unexpired `Pending` or `Running` attempt credential. It preserves assignment, environment, scope, and deadline, returns new plaintext once, and creates security audit evidence without a lifecycle outbox event.

`POST /api/v1/service-requests/{request_id}/commands/retry-ai` creates the next bounded attempt under the same logical operation after the database-controlled eligibility time. Human operators receive `ReplacementRequired` rather than callback plaintext; the assigned WorkflowService can then use credential replacement. `POST /api/v1/service-requests/{request_id}/commands/mark-terminal-failure` is limited to current ManagerApprover and Administrator roles. Stale Pending and Running AI assessment is a directly testable trusted in-process service, not a public route.

## Deterministic triage and review

Migration `0010_deterministic_triage_foundation` adds immutable decision-policy, duplicate-candidate, reviewed-fact, routing-decision, and routing-decision/candidate-link records. It seeds deployment-controlled policy `general-service-demo@1.0.0` revision `1`; the backend validates that stored identity and canonical content before evaluating it.

`CompleteTriage` is a trusted `BackendService` operation invoked directly in process. It is deliberately not exposed as a public HTTP route. It uses current request, contact, interpretation, duplicate, and PostgreSQL-time evidence to run the complete deterministic policy, then commits immutable candidates/decision evidence, the request summary, audit records, outbox messages, and command-idempotency outcome atomically.

The human-authenticated API exposes these guarded commands:

- `POST /api/v1/service-requests/{request_id}/duplicate-candidates/{candidate_id}/commands/resolve`
- `POST /api/v1/service-requests/{request_id}/commands/complete-human-review`

Duplicate resolution records `ConfirmedDuplicate` or `NotDuplicate` without merging contacts or requests. Human review accepts only bounded reviewed facts and always recalculates through the same immutable policy; OperationsAgent authority remains limited to non-Urgent review, while Urgent and hard safety/continuity correction requires ManagerApprover or Administrator authority.

Public intake remains unauthenticated. `GET /api/v1/service-requests/{request_id}` requires a valid bearer token whose verified Supabase subject maps to an active local application actor with a current allowed role. The intake `Location` UUID alone grants no read access.

Current-role resolution fails closed unless exactly one effective allowlisted assignment exists. PostgreSQL prevents multiple open-ended assignments, while overlapping finite historical intervals remain a future controlled role-management concern.

## Migration lifecycle

```powershell
uv run alembic downgrade base
uv run alembic upgrade head
docker compose down
```

The migrations create 26 application tables. Revision `0011_proposal_approval_foundation` adds `proposed_actions`, `proposed_action_contributors`, `proposal_approval_exclusions`, and `approval_decisions`, generalizes logical operations for a proposal-series-owned `OutboundAction`, and adds the request's exact active-proposal reference.

The execution tables now support the Start AI command. No real AI provider is called and callback plaintext is never stored; integration tests use synthetic in-memory credentials.

WorkflowService authentication infrastructure uses `X-Service-ID`, `X-Service-Timestamp`, `X-Service-Nonce`, and a lowercase hexadecimal `X-Service-Signature`. It signs `METHOD`, canonical path/query, timestamp, nonce, and the exact-body SHA-256 digest as newline-separated UTF-8 data. `AI_OPS_MACHINE_CLOCK_SKEW_SECONDS` and `AI_OPS_MACHINE_NONCE_RETENTION_SECONDS` control bounded replay windows.

Machine secrets are resolved through an injected external resolver from stored nonsecret references. No real secret belongs in `.env` or Git; integration tests use synthetic in-memory bytes. Start AI is the first production route using this dependency.

Reusable non-intake command idempotency accepts exactly one 8–128-character visible-ASCII `Idempotency-Key`. Raw keys are never stored; SHA-256 digests are scoped by trusted actor class/ID, command intent, backend route template, and target type/ID. The complete validated closed command model is canonically bound after validation. Exact completed replay returns the stored safe result without execution, while a changed body returns `409 COMMAND_IDEMPOTENCY_CONFLICT`. Secret-bearing records store only safe callback-credential metadata and `PlaintextIssued`; exact replay projects `AlreadyIssued` in memory and returns no plaintext.

Callback-command authorization metadata is stored independently from secret-delivery metadata. The authorization binding identifies the credential that proved authority for a callback command; the secret-delivery fields identify a credential whose plaintext was issued once. Either or both groups may be present on a completed command record without placing plaintext in the safe response snapshot.

Public intake and all thirteen protected query operations are implemented as documented. Start AI and mock outbound begin attempts `Pending`; claim/start moves only the exact assigned attempt to `Running`; closed callbacks complete success or backend-derived recovery. Outbound callback results may include the safe prior canonical queue when a transition occurred, and retry-outbound returns both safe current and prior queue names; both remain closed against unrelated fields. Real integrations, n8n workflows, the outbox publisher, frontend, deployment, and Phase 3 behavior do not exist. `/health` remains database-, JWKS-, policy-, generator-, secret-resolver-, callback-verifier-, and command-independent.
