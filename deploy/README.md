# Deploy

- `kind/`: local Substrate cluster for development. `deploy.sh` runs the API and agents on it, `e2e.sh` tests them.
  See [wiki: Local development](https://github.com/voxix-dev/justpass-ai/wiki/Local-development).
- `substrate/`: the agent's worker pool, actor template and access policy.
- `k8s/`: manifests for the API in the cloud (to be added).

Cloud targets: [wiki: Architecture](https://github.com/voxix-dev/justpass-ai/wiki/Architecture#hosting).

Local clusters use their own kubeconfig (`~/.kube/substrate-kind.yaml`); never write to `~/.kube/config`.
