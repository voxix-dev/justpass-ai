# Deploy

- `terraform/`: GCP project resources (GKE, Cloud SQL, buckets, Workload Identity Federation for CI).
- `kind/`: local Substrate cluster for development.
- `k8s/`: manifests for the API and Substrate worker pools.
- `substrate/`: the agent as a Substrate actor (worker pool, actor template, egress policy).

Local clusters use their own kubeconfig (`~/.kube/substrate-kind.yaml`); never write to `~/.kube/config`.
