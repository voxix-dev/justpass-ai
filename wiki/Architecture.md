# Architecture

```
web (SPA) ──/api/** + Firebase ID token──▶ API (FastAPI) ──▶ Postgres, object storage
                                            │      ▲
                              start/resume  │      │ Context API (per-job, per-role token)
                                            ▼      │
                                  agent actor (Substrate, gVisor) ──egress gateway──▶ OpenRouter, search API
                                            │
                                  run_code via the API ──▶ sandbox actor (no network)
```

## Components

**Web app** (`apps/web`). Vite + React + TypeScript single-page app. Mobile-first: take a photo,
submit, come back later. Polls the API for finished jobs (TanStack Query `refetchInterval`) and
renders results as Markdown with KaTeX. Its API client is generated from `contracts/openapi.json`;
that file is the only interface between frontend and backend.

**API** (`apps/api`). One FastAPI app with three parts:
- *Jobs API*: submit, list, poll (`updated_since`), get, accept, feedback. Students see only their
  own jobs.
- *Dispatcher*: starts or resumes the job's agent actor and mints its Context API tokens.
- *Context API*: the tools agents call (`read_artifact`, `write_artifact`, `get_profile`,
  `search_history`, `update_memory`, `run_code`, ...). Each call is checked against the role table.

It also enforces the spend limits. Postgres is the source of truth; a Substrate snapshot only makes
resuming faster.

**Agent actor** (`apps/agent`). One Substrate actor (gVisor sandbox) per job. It serves `/readyz`
and `POST /run`. The orchestrator and its workers are LLM calls running inside this one actor, not
separate pods. The actor hibernates as soon as the result is saved.

**Sandbox actor**. Runs model-written code for the solver role. A separate actor with no
EgressPolicy (so no network) and no tokens or user data. An actor can't call another actor
directly, so `run_code` goes agent → Context API → sandbox actor.

## How they communicate

| From → to | Transport | Auth |
|-----------|-----------|------|
| Web → API | HTTPS, same origin under `/api/**` (no CORS) | Firebase ID token in `Authorization: Bearer`, no cookies |
| API → agent actor | Substrate atenet router | mTLS, the API's SPIFFE ID must be on the router allowlist |
| Agent → Context API | HTTPS | HMAC-signed token for one role in one round of one job |
| Agent → OpenRouter / search | Substrate egress gateway | Gateway injects the API keys; the actor never holds them |
| API → sandbox actor | Substrate atenet router | mTLS, same as above |

The API verifies Firebase ID tokens itself (PyJWT against Google's signing keys), see
`apps/api/src/api/auth/firebase.py`. The API serves the same routes with and without the `/api`
prefix, because Firebase Hosting forwards it.

## Hosting

- **Web**: Firebase Hosting (static files on a CDN) with a `/api/**` rewrite to the API, so both
  share one HTTPS domain. Rewrite limits: 60 s per request, all cookies except `__session` are
  stripped. Every PR can get a preview URL.
- **API**: Cloud Run (serverless). Not on the Substrate cluster: Substrate changes the control plane,
  and a cluster for the API alone is overkill.
- **Agents**: Substrate on GKE, one zonal cluster, node pool scaled to zero when idle.
- **Data**: Cloud SQL Postgres (databases `app` and `substrate` on one instance), Cloud Storage
  buckets `app-data` and `snapshots`.
- **Region**: EU.
- **Local**: everything runs on a laptop (Postgres in compose, Firebase Auth Emulator, Substrate on
  kind). See [Local development](Local-development.md).

> **Note: cloud provider.** Access to the GCP account is currently blocked by Google's account
> review. The target cloud may change to AWS or Azure soon. Until that is settled, cloud
> infrastructure code (Terraform, cloud manifests) is on hold and development runs locally.
