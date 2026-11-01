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

Details: [wiki](https://github.com/voxix-dev/justpass-ai/wiki).

## Setup

You need uv, Docker or Podman, and for the web app Node 22 and pnpm. With Podman, write
`podman compose` for `docker compose` below.

```
uv sync
uv run pre-commit install
cp .env.example .env
docker compose up -d --wait db                # Postgres for development and tests
uv run --env-file .env alembic upgrade head   # create or update the tables
uv run --env-file .env pytest
uv run --env-file .env uvicorn api.main:app --reload
```

Or run the API in a container (migrates on start, then serves on :8000):
`docker compose up -d --build api`.

Ports taken? `API_PORT` moves the API for both compose and the web app's `pnpm dev`. `DB_PORT` moves
Postgres; change the port in `.env` too.

Web app: [apps/web/README.md](apps/web/README.md). Local Substrate cluster:
[wiki: Local development](https://github.com/voxix-dev/justpass-ai/wiki/Local-development). Repo rules: [AGENTS.md](AGENTS.md).
