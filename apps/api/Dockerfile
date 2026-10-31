# API image for local testing. Build context: repo root (`docker compose build api`).
# On start it migrates the database, then serves on :8000. Needs DATABASE_URL.

FROM python:3.14.10-slim-trixie AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.33 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app

# Dependencies first: this layer is rebuilt only when the lock or a pyproject changes.
COPY uv.lock pyproject.toml ./
COPY apps/api/pyproject.toml apps/api/
COPY apps/agent/pyproject.toml apps/agent/
COPY packages/shared/pyproject.toml packages/shared/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package api --no-install-workspace

# Then the workspace packages, installed as regular (non-editable) packages.
COPY apps/api/src apps/api/src
COPY packages/shared/src packages/shared/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package api --no-editable


FROM python:3.14.10-slim-trixie
RUN useradd --uid 10001 --no-create-home app
WORKDIR /app
COPY --from=builder /app/.venv .venv
# Alembic reads [tool.alembic] from the root pyproject.toml.
COPY pyproject.toml ./
COPY apps/api/migrations apps/api/migrations
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn api.main:app --host 0.0.0.0 --port 8000"]
