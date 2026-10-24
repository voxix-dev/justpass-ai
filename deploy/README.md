# Deploy

- `kind/`: local Substrate cluster for development. See [wiki/Local-development.md](../wiki/Local-development.md).
- `k8s/`, `substrate/`: manifests for the API and Substrate worker pools (to be added).

Cloud targets: [wiki/Architecture.md](../wiki/Architecture.md#hosting).

Local clusters use their own kubeconfig (`~/.kube/substrate-kind.yaml`); never write to `~/.kube/config`.
