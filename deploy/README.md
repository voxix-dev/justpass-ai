# Deploy

- `terraform/`: GCP project resources (GKE, Cloud SQL, buckets, IAM). See [terraform/README.md](terraform/README.md).
- `kind/`: local Substrate cluster for development.
- `k8s/`: manifests for the API and Substrate worker pools.

Local clusters use their own kubeconfig (`~/.kube/substrate-kind.yaml`); never write to `~/.kube/config`.
