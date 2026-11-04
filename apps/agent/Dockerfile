# Agent image: the server that runs inside a Substrate actor, and the `agent` service in compose.yaml.
# Build context: repo root (`docker compose build agent`).
# Serves on :80, which is where the Substrate router and wakeup probe connect.

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
    uv sync --locked --no-dev --package agent --no-install-workspace

# Then the workspace packages, installed as regular (non-editable) packages.
COPY apps/agent/src apps/agent/src
COPY packages/shared/src packages/shared/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package agent --no-editable


FROM python:3.14.10-slim-trixie
RUN useradd --uid 10001 --no-create-home app
WORKDIR /app
COPY --from=builder /app/.venv .venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER 10001
EXPOSE 80
CMD ["uvicorn", "agent.server:app", "--host", "0.0.0.0", "--port", "80"]
