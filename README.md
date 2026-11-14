# JustPass

A student sends homework as text or a photo. Agents solve it in a sandbox, and the student
accepts the result or sends feedback, which starts a new agent run.

## How the parts talk

```
web (SPA) ──/api/** + Firebase ID token──▶ API (FastAPI) ──▶ Postgres, object storage
                                            │      ▲
                              start/resume  │      │ Context API (per-job token)
                                            ▼      │
                                  agent actor (Substrate, gVisor) ──egress (openrouter.ai:443)──▶ OpenRouter
                                            │
                                  run_code via the API ──▶ sandbox actor (no network)
```

- The web app uses only `contracts/openapi.json` (generated TypeScript client).
- The agent has no database or storage access. It reads and writes through the Context API.
- The API sends `OPENROUTER_API_KEY` to the agent with each round. The agent calls OpenRouter directly; its egress policy allows openrouter.ai:443 only when a key is set.

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
uv run --env-file .env uvicorn agent.server:app --port 8001 &   # the agent
uv run --env-file .env uvicorn api.main:app --reload
```

Or run the API and the agent in containers (migrates on start, then serves on :8000):
`docker compose up -d --build api`.

Ports taken? `API_PORT` moves the API for both compose and the web app's `pnpm dev`. `DB_PORT` moves
Postgres; change the port in `.env` too.

The agent calls OpenRouter (`deepseek/deepseek-v4.1-flash`, cheapest provider) and has sympy math tools
and tools to write documents through the Context API. Set `OPENROUTER_API_KEY` in `.env`. Without it the
agent waits 10 seconds and sends back a fixed sample answer. A task with the word "fail" fails.

Web app: [apps/web/README.md](apps/web/README.md). Local Substrate cluster:
[wiki: Local development](https://github.com/voxix-dev/justpass-ai/wiki/Local-development). Repo rules: [AGENTS.md](AGENTS.md).
