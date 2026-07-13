# Backend executable foundation

This directory contains the runnable FastAPI foundation, `GET /health`, PostgreSQL/SQLAlchemy/Alembic persistence, atomic public intake, and a human-authenticated service-request detail query.

It also exposes `POST /api/v1/service-requests/{request_id}/commands/start-ai-interpretation` to an HMAC-authenticated `WorkflowService`. The command creates one logical operation, one `Pending` attempt, hash-only callback authorization, safe audit evidence, and one pending outbox row atomically. It does not start the attempt or invoke an AI provider.

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

## Start AI interpretation

The production command requires `X-Service-ID`, `X-Service-Timestamp`, `X-Service-Nonce`, `X-Service-Signature`, `X-Correlation-ID`, and a visible-ASCII `Idempotency-Key`. The HMAC covers the exact raw JSON body. Its closed body is:

```json
{
  "schema_version": "1.0",
  "expected_versions": { "service_request": 1 },
  "command": {}
}
```

The first committed execution returns `202 Accepted`, a `Pending` attempt, and one opaque callback credential with `credential_delivery: PlaintextIssued`. An exact replay with a fresh HMAC nonce returns `200 OK`, the original safe identifiers, and `credential_delivery: AlreadyIssued` without plaintext. Only the SHA-256 credential hash is stored. Do not place a real machine HMAC key, callback credential, or signature in documentation, `.env`, Git, or logs.

Public intake remains unauthenticated. `GET /api/v1/service-requests/{request_id}` requires a valid bearer token whose verified Supabase subject maps to an active local application actor with a current allowed role. The intake `Location` UUID alone grants no read access.

Current-role resolution fails closed unless exactly one effective allowlisted assignment exists. PostgreSQL prevents multiple open-ended assignments, while overlapping finite historical intervals remain a future controlled role-management concern.

## Migration lifecycle

```powershell
uv run alembic downgrade base
uv run alembic upgrade head
docker compose down
```

The migrations create sixteen application tables: six intake/evidence, two human-access, four AI execution/interpretation, three machine-security tables, and `command_idempotency_records`.

The execution tables now support the Start AI command. No real AI provider is called and callback plaintext is never stored; integration tests use synthetic in-memory credentials.

WorkflowService authentication infrastructure uses `X-Service-ID`, `X-Service-Timestamp`, `X-Service-Nonce`, and a lowercase hexadecimal `X-Service-Signature`. It signs `METHOD`, canonical path/query, timestamp, nonce, and the exact-body SHA-256 digest as newline-separated UTF-8 data. `AI_OPS_MACHINE_CLOCK_SKEW_SECONDS` and `AI_OPS_MACHINE_NONCE_RETENTION_SECONDS` control bounded replay windows.

Machine secrets are resolved through an injected external resolver from stored nonsecret references. No real secret belongs in `.env` or Git; integration tests use synthetic in-memory bytes. Start AI is the first production route using this dependency.

Reusable non-intake command idempotency accepts exactly one 8–128-character visible-ASCII `Idempotency-Key`. Raw keys are never stored; SHA-256 digests are scoped by trusted actor class/ID, command intent, backend route template, and target type/ID. The complete validated closed command model is canonically bound after validation. Exact completed replay returns the stored safe result without execution, while a changed body returns `409 COMMAND_IDEMPOTENCY_CONFLICT`. Secret-bearing records store only safe callback-credential metadata and `PlaintextIssued`; exact replay projects `AlreadyIssued` in memory and returns no plaintext.

The public intake endpoint and protected request detail remain as documented. Start AI uses machine HMAC, nonce, and command idempotency, but leaves its attempt `Pending`. No attempt-start command, callback endpoint, provider invocation, interpretation, credential replacement, n8n workflow, publisher, or frontend exists. `/health` remains database-, JWKS-, generator-, and secret-resolver-independent.
