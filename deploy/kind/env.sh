# Source before any Substrate/kind command. Uses its own kubeconfig, so ~/.kube/config stays untouched.
# Linux hosts: if Envoy crashes on inotify, raise fs.inotify.max_user_instances=1024 and max_user_watches=524288.
KIND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export KUBECONFIG="$HOME/.kube/substrate-kind.yaml"
# Clients the atenet router accepts: Substrate's e2e client and our API.
export ATE_INGRESS_ALLOWED_SPIFFE_IDS=spiffe://cluster.local/ns/ate-e2e-router-client/sa/router-client,spiffe://cluster.local/ns/homework/sa/api
# Optional per-machine overrides (docker vs podman, Go location, ...). Not committed.
if [[ -f "$KIND_DIR/env.local.sh" ]]; then source "$KIND_DIR/env.local.sh"; fi
