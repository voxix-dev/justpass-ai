# Local development

## Python services

```
uv sync
cp .env.example .env
docker compose up -d --wait db
uv run --env-file .env alembic upgrade head
uv run --env-file .env pytest
uv run --env-file .env uvicorn api.main:app --reload    # http://localhost:8000
```

Tests create and drop their own database on `TEST_DATABASE_URL`. Without the env file the database
tests are skipped.

## Web app

See [apps/web/README.md](../apps/web/README.md). `pnpm dev` serves on `localhost:5173` and proxies
`/api/**` to the API on `localhost:8000`.

## Auth

Use the Firebase Auth Emulator for local accounts:

```
firebase emulators:start --only auth --project demo-justpass
```

In `.env`: `FIREBASE_PROJECT_ID=demo-justpass`, `FIREBASE_AUTH_EMULATOR_HOST=localhost:9099`. The API
refuses emulator mode with a non-`demo-*` project ID.

## Substrate on kind

Agents run as Substrate actors on a local kind cluster (rootless podman works).

```
source deploy/kind/env.sh
```

- `env.sh` sets `KUBECONFIG=~/.kube/substrate-kind.yaml` (never `~/.kube/config`) and
  `ATE_INGRESS_ALLOWED_SPIFFE_IDS`, the clients the atenet router accepts.
- Machine-specific settings (container runtime, Go location) go in `deploy/kind/env.local.sh`,
  which is not committed.
- Build the cluster with Substrate's quickstart (`hack/create-kind-cluster.sh` in the Substrate
  repo). Images go to the local registry at `localhost:5001`.
- The router only accepts mTLS. `deploy/kind/router-client.yaml` runs a debug pod with the API's
  identity (`spiffe://cluster.local/ns/homework/sa/api`); its header shows the curl to call an actor.
- Actor commands: `kubectl ate create actor ...`, `kubectl ate suspend actor ... --mode=sync`; the
  next request resumes it.
- On Linux, if Envoy crashes on inotify, raise `fs.inotify.max_user_instances=1024` and
  `fs.inotify.max_user_watches=524288`.
- After a host reboot, start the registry, the kind node and the buildx builder containers again.
