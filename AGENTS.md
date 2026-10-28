# Repository rules

Design docs live in the [GitHub wiki](https://github.com/voxix-dev/justpass-ai/wiki). Read the page for the area you
change, and update it when your PR changes a design decision.

## Layout
- `apps/api/` FastAPI: Jobs API, Context API, dispatcher.
- `apps/agent/` agent harness, role tool lists (`roles.py`), playbooks (`playbooks/*.yaml`).
- `apps/web/` frontend. Talks to the API only through `contracts/openapi.json`.
- `packages/shared/` Pydantic models used by both api and agent.
- `deploy/` local cluster, k8s and Substrate manifests. `evals/` homework samples and scoring.

## Rules
- Every boundary (HTTP, agent tool input/output, DB row) uses a Pydantic model. Shared ones live in `packages/shared`.
- `api` and `agent` never import each other. They talk over HTTP. `lint-imports` enforces this.
- The agent never touches the database or object storage directly. It reads and writes only through Context API tools.
- `apps/agent/src/agent/roles.py` is the permission system. Giving a role new tools needs an explicit review in the PR.
  `tests/test_roles.py` must keep passing: no role may have both private-data tools and outbound tools.
- Never call a paid model in tests. Use recorded responses.
- Never commit secrets. Put them in `.env` (git-ignored); `.env.example` lists the names.
- Add dependencies to the package that uses them: `uv add --package api <pkg>`. No new dependency without a reason.

## Pull requests
- One PR is one feature or one fix. Keep it small. Avoid big PRs.
- If the smallest testable change can't be done in under 1000 lines, a bigger PR is fine, but try hard to avoid it.
- No stacked PRs. Every PR targets `main` and stands on its own. Don't split one change into a chain of PRs.
- If a change depends on another open PR, wait until that PR is merged before opening it.
- Only when waiting would block work for too long, put both changes in one bigger PR instead. Avoid this too.
- To add docstrings with CodeRabbit, pick "Commit on current branch" under "Generate docstrings". Never "Create a new
  PR": that opens a stacked PR.

## Before you push
```
uv run ruff format . && uv run ruff check --fix .
uv run pyright && uv run lint-imports
uv run --env-file .env pytest   # once: cp .env.example .env && docker compose up -d db
uv run python -m api.export_openapi   # if you changed the API; commit contracts/openapi.json
```
