# Backend executable foundation

This directory contains the runnable FastAPI foundation, `GET /health`, PostgreSQL/SQLAlchemy/Alembic persistence, and atomic public service-request intake.

## First setup

```powershell
cd C:\Users\ivorr\DevPortfolio\general-service-suite\backend
Copy-Item .env.example .env
uv sync
docker compose up -d postgres
uv run alembic upgrade head
```

The `.env` file is local configuration and must not be committed. The committed `.env.example` contains only nonproduction defaults matching the local Compose service. PostgreSQL is exposed on `127.0.0.1:55432` to avoid the conventional local port. Compose is for local development and integration testing only; no production Supabase project is configured.

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

## Migration lifecycle

```powershell
uv run alembic downgrade base
uv run alembic upgrade head
docker compose down
```

The migrations create exactly six application tables: `inbound_deliveries`, `accepted_intake_keys`, `contacts`, `service_requests`, `audit_events`, and `outbox_messages`. PostgreSQL foreign keys, stable-value checks, positive-version checks, immediate reservation uniqueness, and narrowly deferred circular references support atomic new acceptance, replay, conflict, audit, and outbox writes.

The public intake endpoint is implemented without authentication as approved. No service-request query, AI interpretation, deterministic triage, n8n workflow, outbound adapter, publisher, or frontend exists. `/health` remains database-independent and is not a readiness check.
