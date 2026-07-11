# Backend executable foundation

This directory contains the runnable FastAPI foundation and the implemented `GET /health` infrastructure endpoint.

## First setup

```powershell
cd C:\Users\ivorr\DevPortfolio\general-service-suite\backend
uv sync
Copy-Item .env.example .env
```

The `.env` file is local configuration and must not be committed. The committed `.env.example` contains nonsecret development defaults only.

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
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

No domain API, database, authentication, AI integration, n8n workflow, outbound adapter, or frontend exists yet.
