# CI

GitHub Actions, `.github/workflows/ci.yml`. Pre-commit hooks run ruff and `uv lock --check` locally;
run the rest with the commands in [AGENTS.md](../AGENTS.md#before-you-push).

| Check | Tool | Catches |
|-------|------|---------|
| Format, lint | ruff | Messy code |
| Types | pyright | Made-up fields, wrong signatures |
| Module boundaries | import-linter | `api` and `agent` importing each other |
| Dependencies | deptry, `uv sync --locked` | Unused, missing or unlocked packages |
| Tests | pytest against Postgres 18 | Regressions |
| Coverage of changed lines | diff-cover, 70% | Untested additions |
| API contract | `api.export_openapi` must leave `contracts/` unchanged | Forgotten contract updates |
| Web | oxlint, tsc, vite build, generated client matches contract | Frontend breakage, client drift |
| Secrets | gitleaks | Keys in code or history |

Changes reach `main` through PRs with green checks. Keep PRs small and focused; stack them when a
change is big.

## Evals

Real homework samples with what a good answer must contain (`evals/`). They cost model credits, so
they run nightly or on PRs labelled `run-evals`, never on every PR. Synthetic samples aren't useful;
the set needs real, anonymized homework photos.
