# Deploy

- `kind/`: local Substrate cluster for development. See [wiki: Local development](https://github.com/voxix-dev/justpass-ai/wiki/Local-development).
- `k8s/`, `substrate/`: manifests for the API and Substrate worker pools (to be added).

Cloud targets: [wiki: Architecture](https://github.com/voxix-dev/justpass-ai/wiki/Architecture#hosting).

Local clusters use their own kubeconfig (`~/.kube/substrate-kind.yaml`); never write to `~/.kube/config`.
