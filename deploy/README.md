# Deploy

- `local/`: local Substrate cluster for development. It runs only Substrate and the agents. `deploy.sh` deploys the
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

## GCP: the API on Cloud Run, Postgres on Cloud SQL

- `terraform/modules/api/`: Cloud SQL (Postgres 18), the runtime service account, the secrets and the Cloud Run
  service. `emulator = true` switches only what floci-gcp cannot do: the Cloud SQL volume and Secret Manager
  references become a TCP connection and plain environment values. Its tests: `terraform test` in that directory.
- `terraform/bootstrap/`: what the deploy workflow needs before it can run Terraform, and what Google cannot delete
  again: the Firebase project, its default Hosting site, the Workload Identity pool (only `main` of this repository
  gets a token) and the `github-terraform` service account that applies `prod`, which only the deploy workflow's
  infra job (environment `production`) can impersonate. Apply it by hand once:
  1. Make the state bucket by hand, with versioning and uniform bucket-level access. The state holds the secrets
     (database password, `DATABASE_URL`, token secret), so give read access only to the people who run Terraform.
  2. `terraform init -backend-config=bucket=<bucket> -backend-config=prefix=bootstrap`.
  3. If the project was made in the Firebase console, import what it made:
     `terraform import -var project_id=<project> google_firebase_project.main <project>` and
     `terraform import -var project_id=<project> google_firebase_hosting_site.default <project>/<project>`.
     Then `terraform apply -var project_id=<project>`.
  4. Set the repository variables `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WORKLOAD_IDENTITY_PROVIDER` and
     `GCP_TERRAFORM_SERVICE_ACCOUNT` from `terraform output`, and `GCP_TF_STATE_BUCKET` to the bucket.
- `terraform/prod/`: the module plus an Artifact Registry remote repository that proxies GHCR, where CI's `images`
  job pushes the API image (Cloud Run cannot pull from GHCR; deploys pull it through the proxy by digest) and the
  deployer service account. `.github/workflows/deploy.yml` applies it on every merge after `ci` passes (state prefix
  `prod`), then deploys the API with `gcp/deploy-api.sh` and the web app to Firebase Hosting, which rewrites `/api/**`
  to Cloud Run. Until `GCP_PROJECT_ID` is set that workflow does nothing. Terraform sets only the first image and
  ignores it afterwards, so applying never rolls a deploy back.
- `terraform/local/`: the module in floci-gcp. `gcp/local-e2e.sh` starts floci-gcp (`ENGINE=podman` for podman,
  with `podman.socket` enabled), builds the API image, applies this root, deploys with the same
  script CI uses and checks that the API answers from its database. `gcp/local-e2e.sh down` removes everything.
  CI runs it as `gcp-e2e`.

## GCP: Substrate on GKE

- `terraform/modules/cluster/`: the GKE cluster with the internal load balancers for ate-api and the router, the
  private DNS zone for `ate-system.svc`, and the Secret Manager secrets the API reads (`token`, `ca`, `template`).
  Terraform creates each with a placeholder version. Its tests: `terraform test` in that directory.
- The deploy workflow's `cluster` job (after `infra`, before `api`) first runs `gke/substrate-db.sql` on the
  `substrate` database through the Cloud SQL Auth Proxy, as the `substrate_admin` user Terraform creates (the
  roles and schema ate-api needs). Then it runs `gke/sync.sh` against the cluster, with the agent image CI pushed for
  the commit, pinned by digest: it applies Substrate (v1.0.0) and the agents, issues the API's ate-api token and the router CA, and adds them as new
  versions of the secrets. The token lives 48 hours, so the `refresh` workflow (daily, or a manual run) reruns only this job,
  which then runs only `gke/credentials.sh`: a new token and CA, and nothing applied.
- The API on Cloud Run reaches ate-api and the router through the internal load balancers, by their
  `ate-system.svc` names in the private DNS zone. It presents a router client certificate from our CA and
  authenticates to ate-api with the token.
- `gke/sync.sh` takes `CLUSTER` (the `cluster` Terraform output), `SUBSTRATE_DIR` and `OUT_DIR` and needs only
  `kubectl`, `go` and `uv`, so it also runs against any cluster, such as kind, for testing.
