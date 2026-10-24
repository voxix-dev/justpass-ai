# JustPass wiki

Design docs for the repo. Keep them in sync with the code: a PR that changes a decision updates the
page that records it.

| Page | Covers |
|------|--------|
| [Architecture](Architecture.md) | Components, how they communicate, hosting |
| [Job lifecycle](Job-lifecycle.md) | Job states, rounds, review window, hibernation |
| [Agents](Agents.md) | Orchestrator and workers, tiers and models, playbooks, OpenRouter |
| [Security](Security.md) | Role permissions, prompt injection, secrets, user data |
| [Data](Data.md) | Postgres tables, object storage, agent memory, vector search later |
| [Local development](Local-development.md) | Postgres, Firebase Auth Emulator, Substrate on kind |
| [CI](CI.md) | Checks that run on every PR, evals |
| [Decisions](Decisions.md) | Decision log and open questions |

Scope today is homework: text or photo in, Markdown with KaTeX out. Test prep comes later; the data
model already stores what it will need.
