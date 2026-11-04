# Deploy

- `kind/`: local Substrate cluster for development. It runs only Substrate and the agents. `deploy.sh` deploys the
  agents and starts the API in a container on this machine, as production has it on Cloud Run with Postgres on Cloud
  SQL; `e2e.sh` tests them. `identity.sh` gives the API its cluster identity, which lasts 24 hours: rerun it after that.
  `forward.sh` keeps the port-forwards to ate-api and the router up, on 127.0.0.1 only.
  See [wiki: Local development](https://github.com/voxix-dev/justpass-ai/wiki/Local-development).
- `substrate/`: what the agents need on Substrate. `agent-pool.yaml` (a kustomize base) is the worker pool;
  `agents.yaml` is the atespace, access policy and actor template, which `api.substrate_sync` creates in
  ate-api (they are not Kubernetes resources). The sandbox size in `agents.yaml` must fit the worker.

Cloud targets: [wiki: Architecture](https://github.com/voxix-dev/justpass-ai/wiki/Architecture#hosting).

Local clusters use their own kubeconfig (`~/.kube/substrate-kind.yaml`); never write to `~/.kube/config`.
