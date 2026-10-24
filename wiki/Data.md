# Data

| Kind | Examples | Where |
|------|----------|-------|
| Records | Users, jobs, rounds, model calls and cost, feedback, grades | Postgres |
| Files | Photos, drafts, final answers, Substrate snapshots | Object storage |
| What agents know about a student | "writes informally", "teacher wants 5 paragraphs" | Markdown memory docs, as Postgres rows |

Only the API touches the database and storage. Migrations use Alembic.

## Tables

| Table | Holds |
|-------|-------|
| `users` | Internal id, `auth_provider` + `auth_subject` (Firebase UID), locale, grading scale, school level, `consent_at` |
| `jobs` | One homework request: input, subject, level, language, status, `review_deadline` |
| `job_rounds` | One agent run per round; round 2+ come from feedback |
| `artifacts` | What agents wrote (notes, drafts, reviews, `final`), per round. Agents pass ids, not content |
| `memory_docs` | `profile` or one doc per subject, `content_md`, version, full-text `tsv` |

Planned: `files` (storage path, MIME type, extracted text), `llm_calls` (model, tier, tokens,
cost, latency), `feedback`, `grades` (raw plus normalized), `events` (append-only log),
and `chunks` for vector search.

Conventions:
- Enums are stored as text with a CHECK constraint, generated from `packages/shared`.
- `updated_at` uses `clock_timestamp()`, not `now()`, so polling doesn't miss rows from long
  transactions.
- `llm_calls` plus `feedback` form an eval dataset: an accepted result labels the model choice.

## Storage

- `app-data`: `users/{id}/uploads`, `runs/{id}/artifacts`.
- `snapshots`: Substrate snapshots, deleted by a lifecycle rule after 3 days.
- Locally, an S3-compatible store inside kind holds snapshots.

## Agent memory

- Each user has a profile doc and one doc per subject, capped at about 2k tokens, always in the
  orchestrator's prompt. The curator agent updates them after accepted runs and feedback. They are
  derived from the other tables and can be rebuilt.
- For anything else, agents use agentic search through Context API tools: `list_history`,
  `read_artifact`, `search_history` (Postgres full-text search). With a few dozen items per student
  this beats embeddings and has no indexing pipeline.
- Rows in Postgres rather than files in a bucket: saved in the same transaction as the run, one
  access path to secure, one delete.

## Vector search (with test prep)

pgvector in the same Postgres, added next to the memory docs, not instead of them:
- `CREATE EXTENSION vector`, a `chunks` table with an HNSW index.
- An embedding step on each "artifact saved" event.
- `search_history` switches to hybrid search (keyword + vector, filtered by `user_id`). Agents keep
  calling the same tool, so prompts don't change.

Why not a separate vector database: every query is "this student only" and every deletion request is
"everything about this student". In Postgres both are `WHERE user_id = ...`; a second system doubles
every write and delete. Revisit only if measurements show pgvector falling behind.
