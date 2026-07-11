# Backend executable foundation

This directory contains the runnable FastAPI foundation, the implemented `GET /health` infrastructure endpoint, and the initial PostgreSQL/SQLAlchemy/Alembic persistence foundation.

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

The migration creates exactly six application tables: `inbound_deliveries`, `accepted_intake_keys`, `contacts`, `service_requests`, `audit_events`, and `outbox_messages`. PostgreSQL foreign keys, stable-value checks, positive-version checks, and uniqueness constraints provide structural protection. The future atomic-intake command remains responsible for complete accepted-new/replay/conflict graph validation and coordinated domain/audit/outbox writes.

The public intake endpoint is not implemented. No authentication, AI integration, n8n workflow, outbound adapter, publisher, or frontend exists. `/health` remains a process-health endpoint and does not perform a database readiness check.
