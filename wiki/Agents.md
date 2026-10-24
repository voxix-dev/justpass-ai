# Agents

An orchestrator plans and delegates; workers do the work. The orchestrator uses a strong model so
the plan comes out right, but it only writes briefs and reads short results. Full content never
passes through its context: workers write to the artifact store and return an id.

## The orchestrator's one tool

```
run_agent(role, tier, instructions, input_artifacts[])
  → {artifact_id, summary, status, confidence}
```

- Roles and tiers are limited to the steps of the chosen playbook.
- Inputs must be artifacts of this job (the request's inputs or something a worker wrote).
- Research summaries never reach the orchestrator; it gets the artifact id and status only.
- The final artifact must pass review. A failed review reruns the step one tier higher, at most twice.
- Spend and turn limits end the job with a failed result instead of overshooting.

## Roles

| Role | Does | Default tier |
|------|------|--------------|
| orchestrator | Picks a playbook, writes briefs, decides retries | Opus |
| research | Web search and reading; gets no user data | mid |
| writer | Writes essays and answers | mid, strong if hard |
| solver | Solves tasks, can run code in the sandbox | mid |
| review | Checks the result against the task | mid |
| curator | Updates memory docs after accepted runs or feedback | cheap |

Tool lists per role: [Security](Security.md#what-each-role-can-touch).

## Tiers and models

Workers ask for a tier, never a model name. One config maps `(role, tier)` to models, so trying a
new model is a config change.

| Tier | Model | Used for |
|------|-------|----------|
| orchestrator | Opus 5.5, low–medium effort | Planning and delegation only |
| strong | Opus 5.5, Fable 5.1 when needed | Hard essays, proofs, the last retry |
| mid | GPT Sol | Default worker: research, writing, solving, review |
| cheap | Open models | Reading the photo, splitting tasks, formatting |

## Playbooks

YAML files in `apps/agent/src/agent/playbooks/`, validated against a schema. The orchestrator picks
one and fills it in; it plans from scratch only when none fits.

- `essay`: research → write → review
- `math`: one solver per task in parallel → verify with code → format

## OpenRouter

All model calls go through OpenRouter with a plain HTTP client (no agent framework):
- Pass `reasoning_details` back unchanged across tool calls.
- Prompt caching on, one `session_id` per job so the cache stays warm.
- Route only to zero-data-retention endpoints; check every configured model still has one.
- Retry 429, 5xx and timeouts with backoff, under a per-call timeout.
- Subagents are our own `run_agent` tool, not `openrouter:subagent`, which can't reach our storage
  or sandbox.

## Spend limits

- Credit limit on the OpenRouter key ($20).
- The API stops dispatching when the day's spend reaches $1.
- 3 jobs per user per day.
- Tests replay recorded responses; pipeline tests use free models with fake data. `AGENT_FAKES=1`
  runs a scripted job with no model calls.
