# Decisions

| Area | Decision | Status |
|------|----------|--------|
| Scope | Homework first. Test prep later; each homework stores a test-ready summary (`test_summary` artifact) | decided |
| Users | Worldwide, high school and college | decided |
| Input / output | Text and photo in. Markdown with KaTeX out, one answer per task | decided |
| Flow | Async, one shot. Accept or send feedback; 48 h review window | decided |
| Runtime | Substrate actors (gVisor), hibernate as soon as the result is saved. Fallback: k8s Jobs + gVisor | decided |
| Code execution | Separate sandbox actor with no network or tokens, reached through an API relay | decided |
| Language | Python 3.14, uv workspaces | decided |
| Web | Vite + React + TypeScript SPA, generated client from OpenAPI | decided |
| Hosting | Web on Firebase Hosting with `/api/**` rewrite; API on Cloud Run; agents on Substrate on GKE; EU region | decided, see cloud note |
| Cloud provider | GCP | on hold: account blocked, may move to AWS or Azure ([note](Architecture.md#hosting)) |
| Auth | Firebase Auth (Google, email link, anonymous). Bearer ID token, no cookies | decided |
| Notifications | In-app polling. Email and push later | decided |
| Models | Opus 5.5 orchestrator, GPT Sol workers, open models for easy steps, all through OpenRouter | decided |
| Harness | Hand-written loop with a plain OpenRouter client, no agent framework | decided |
| Agent memory | Markdown memory docs as Postgres rows, plus agentic search through Context API tools | decided |
| Agent permissions | Fixed tools per role; research gets no user data; orchestrator never sees research summaries | decided |
| Vector search | pgvector in the same Postgres, added with test prep | later |
| Search API | Tavily or Exa, whichever free tier is better | open |
| Later | Email and push, PDF input, integrations, test prep, subscription, parent accounts, full GDPR/COPPA | later |

## Open questions

- How the API on Cloud Run reaches the Substrate router. The router expects an in-cluster SPIFFE
  identity today (`ns/homework/sa/api`).
- The `run_code` relay in the Context API is not built yet.
- The web app's job statuses and create form don't match the Jobs API yet (status names, required
  `subject`/`level`/`language`, photo upload, feedback as a new round rather than a new job).

## Risks

| Risk | Mitigation |
|------|------------|
| Substrate is pre-1.0 | Postgres is the source of truth, so falling back to k8s Jobs changes only the dispatcher |
| Model spend runs out | Key credit limit, daily dispatch stop, recorded responses in tests |
| School Google accounts block third-party sign-in | Anonymous and email-link sign-in |
| Reasoning lost between tool calls on OpenRouter | Pass `reasoning_details` back unchanged; covered by tests |
