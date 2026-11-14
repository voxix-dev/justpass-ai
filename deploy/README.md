# Deploy

- `kind/`: local Substrate cluster for development. It runs only Substrate and the agents. `deploy.sh` deploys the
  agents and starts the API in a container on this machine, as production has it on Cloud Run with Postgres on Cloud
  SQL; `e2e.sh` tests them. `identity.sh` gives the API its cluster identity, which lasts 24 hours: rerun it after that.
  `forward.sh` keeps the port-forwards to ate-api and the router up, on 127.0.0.1 only.
  Actors reach the Context API, and only it: each gets an egress policy for the Service `context-api`
  (`context-api.yaml`, no selector; `deploy.sh` points its EndpointSlice at `context-relay`). That relay lets
  only POSTs to `/v1/context/` through (`context-relay.conf`) to a unix socket, which `context-uds` serves
  to the API on 127.0.0.1 (`context-uds.conf`), so the API never listens on the LAN.
  The relay's IP is fixed in the EndpointSlice only when `deploy.sh` runs: if the relay is recreated on its own, rerun
  `deploy.sh` (`e2e.sh` checks that the two agree).
  After a host reboot the agent pods get new IPs while Substrate still holds the old ones (the router answers 503):
  `deploy/local/workers.sh --fix` recreates the pods (`deploy.sh` runs it; without `--fix` it only checks).
  The API container gets `OPENROUTER_API_KEY` from `.env`. When it is set, each actor's egress policy also allows
  openrouter.ai:443 (TLS passthrough).
  See [wiki: Local development](https://github.com/voxix-dev/justpass-ai/wiki/Local-development).
- `substrate/`: what the agents need on Substrate. `agent-pool.yaml` (a kustomize base) is the worker pool;
  `agents.yaml` is the atespace, access policy and actor template, which `api.substrate_sync` creates in
  ate-api (they are not Kubernetes resources). The sandbox size in `agents.yaml` must fit the worker.

Cloud targets: [wiki: Architecture](https://github.com/voxix-dev/justpass-ai/wiki/Architecture#hosting).

Local clusters use their own kubeconfig (`~/.kube/substrate-kind.yaml`); never write to `~/.kube/config`.

## GCP: the API on Cloud Run, Postgres on Cloud SQL

- `terraform/modules/api/`: Cloud SQL (Postgres 18), the runtime service account, the secrets and the Cloud Run
  service. `emulator = true` switches only what floci-gcp cannot do: the Cloud SQL volume and Secret Manager
  references become a TCP connection and plain environment values. Its tests: `terraform test` in that directory.
- `terraform/prod/`: the module plus Artifact Registry, Workload Identity Federation for GitHub Actions (only `main`
  of this repository) and the deployer service account. State is in a GCS bucket: `terraform init
  -backend-config="bucket=<bucket>"`. The state holds the secrets (database password, `DATABASE_URL`, token
  secret), so make that bucket by hand with versioning and uniform bucket-level access, and give read access only
  to the people who run Terraform. `terraform output` lists the repository variables `.github/workflows/deploy.yml`
  needs; until `GCP_PROJECT_ID` is set that workflow does nothing. Terraform sets only the first image; CI deploys
  the rest with `gcp/deploy-api.sh`.
- `terraform/local/`: the module in floci-gcp. `gcp/local-e2e.sh` starts floci-gcp (rootless podman: enable
  `podman.socket` first; `ENGINE=docker` for Docker), builds the API image, applies this root, deploys with the same
  script CI uses and checks that the API answers from its database. `gcp/local-e2e.sh down` removes everything.
  CI runs it as `gcp-e2e`.
