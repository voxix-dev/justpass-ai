# Rules for AI coding assistants (and humans)

## Layout
- `apps/api/` FastAPI: Jobs API, Context API, dispatcher.
- `apps/agent/` agent harness, role tool lists (`roles.py`), playbooks (`playbooks/*.yaml`).
- `apps/web/` frontend, owned by its teammate. Talks to the API only through `contracts/openapi.json`.
- `packages/shared/` Pydantic models used by both api and agent.
- `deploy/` Terraform, k8s and Substrate manifests. `evals/` homework samples and scoring.

## Rules
- Every boundary (HTTP, agent tool input/output, DB row) uses a Pydantic model. Shared ones live in `packages/shared`.
- `api` and `agent` never import each other. They talk over HTTP. `lint-imports` enforces this.
- The agent never touches the database or Cloud Storage directly. It reads and writes only through Context API tools.
- `apps/agent/src/agent/roles.py` is the permission system. Do not give a role new tools without the owner's review.
  `tests/test_roles.py` must keep passing: no role may have both private-data tools and outbound tools.
- Never call a paid model in tests. Use recorded responses.
- Never commit secrets. Put them in `.env` (git-ignored); `.env.example` lists the names.
- Keep PRs under ~400 changed lines. Split bigger work.
- Add dependencies to the package that uses them: `uv add --package api <pkg>`. No new dependency without a reason.

## Before you push
```
uv run ruff format . && uv run ruff check --fix .
uv run pyright && uv run lint-imports && uv run pytest
uv run python -m api.export_openapi   # if you changed the API; commit contracts/openapi.json
```
