# Deploy

- `kind/`: local Substrate cluster for development. It runs only Substrate and the agents. `deploy.sh` deploys the
  agents and starts the API in a container on this machine, as production has it on Cloud Run with Postgres on Cloud
  SQL; `e2e.sh` tests them. `identity.sh` gives the API its cluster identity, which lasts 24 hours: rerun it after that.
  `forward.sh` keeps the port-forwards to ate-api and the router up, on 127.0.0.1 only.
  See [wiki: Local development](https://github.com/voxix-dev/justpass-ai/wiki/Local-development).
- `substrate/`: the agent's worker pool, actor template and access policy.

Cloud targets: [wiki: Architecture](https://github.com/voxix-dev/justpass-ai/wiki/Architecture#hosting).

Local clusters use their own kubeconfig (`~/.kube/substrate-kind.yaml`); never write to `~/.kube/config`.
