# Backend executable foundation

This directory contains the runnable FastAPI foundation, `GET /health`, PostgreSQL/SQLAlchemy/Alembic persistence, atomic public intake, and a human-authenticated service-request detail query.

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

Public intake remains unauthenticated. `GET /api/v1/service-requests/{request_id}` requires a valid bearer token whose verified Supabase subject maps to an active local application actor with a current allowed role. The intake `Location` UUID alone grants no read access.

Current-role resolution fails closed unless exactly one effective allowlisted assignment exists. PostgreSQL prevents multiple open-ended assignments, while overlapping finite historical intervals remain a future controlled role-management concern.

## Migration lifecycle

```powershell
uv run alembic downgrade base
uv run alembic upgrade head
docker compose down
```

The migrations create six accepted-intake/evidence tables, two human-access tables, and four AI execution/interpretation tables, for twelve application tables total. The AI-only additions are `logical_operations`, `integration_attempts`, `attempt_callback_credentials`, and `ai_interpretations`; newly accepted requests keep a null current-interpretation reference.

These four tables are structural foundations only. No real AI provider is called, no callback plaintext is created or stored, and integration tests use synthetic hashes rather than credentials.

The public intake endpoint and protected request detail remain as documented. No AI start/callback runtime, provider integration, credential issuance, deterministic triage, machine HMAC, n8n workflow, publisher, proposal/approval flow, or frontend exists. `/health` remains database- and JWKS-independent.
