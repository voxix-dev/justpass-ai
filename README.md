# JustPass

A student sends homework as text or a photo. Agents solve it in a sandbox, and the student
accepts the result or sends feedback, which starts a new agent run.

## How the parts talk

```
web (SPA) ──/api/** + Firebase ID token──▶ API (FastAPI) ──▶ Postgres, object storage
                                            │      ▲
                              start/resume  │      │ Context API (per-job, per-role token)
                                            ▼      │
                                  agent actor (Substrate, gVisor) ──egress gateway──▶ OpenRouter, search API
                                            │
                                  run_code via the API ──▶ sandbox actor (no network)
```

- The web app uses only `contracts/openapi.json` (generated TypeScript client).
- The agent has no database or storage access. It reads and writes through the Context API.
- API keys are added by the egress gateway, outside the sandbox.

Details: [wiki](wiki/Home.md).

## Setup

```
uv sync
uv run pre-commit install
cp .env.example .env
docker compose up -d --wait db                # Postgres for development and tests
uv run --env-file .env alembic upgrade head   # create or update the tables
uv run --env-file .env pytest
uv run --env-file .env uvicorn api.main:app --reload
```

Web app: [apps/web/README.md](apps/web/README.md). Local Substrate cluster:
[wiki/Local-development.md](wiki/Local-development.md). Repo rules: [AGENTS.md](AGENTS.md).
