# Homework Agents

Send a photo of your homework, AI agents do it, you review the result.

Design and decisions: https://claude.ai/artifact/898ZKpME1bCdonGAw38sgy

## Setup
```
uv sync
uv run pre-commit install
cp .env.example .env
docker compose up -d db                       # Postgres for development and tests
uv run --env-file .env alembic upgrade head   # create or update the tables
uv run --env-file .env pytest
uv run --env-file .env uvicorn api.main:app --reload
```

Rules for contributors and their AI assistants: [AGENTS.md](AGENTS.md).
