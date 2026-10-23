# The code sandbox actor image. Build from the repo root:
#   docker build -f apps/sandbox/Dockerfile -t <registry>/justpass-sandbox:<tag> .
FROM python:3.14.7-slim-trixie AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
# uv checks the lock against every workspace member, so all pyproject files come first.
COPY pyproject.toml uv.lock ./
COPY apps/agent/pyproject.toml apps/agent/
COPY apps/api/pyproject.toml apps/api/
COPY apps/sandbox/pyproject.toml apps/sandbox/
COPY packages/shared/pyproject.toml packages/shared/
RUN uv sync --locked --no-dev --package sandbox --extra math --no-install-workspace
COPY apps/sandbox apps/sandbox
COPY packages/shared packages/shared
RUN uv sync --locked --no-dev --package sandbox --extra math --no-editable

FROM python:3.14.7-slim-trixie
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER 65532:65532
EXPOSE 80
CMD ["python", "-m", "sandbox.server"]
