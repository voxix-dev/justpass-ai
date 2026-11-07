#!/usr/bin/env bash
# Builds the agent and runs it on the local Substrate cluster, and the API next to the cluster in a
# container, as production has it on Cloud Run. Safe to run again.
# Needs the cluster up, kubectl, uv, openssl, jq, and CONTAINER_TOOL (default docker; set it to
# podman in env.local.sh for rootless podman).
set -euo pipefail
shopt -s inherit_errexit # stop on failures inside $(...) too
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
source deploy/local/env.sh

registry=localhost:5001
tool=${CONTAINER_TOOL:-docker}
push_flags=()
if [[ $tool == podman ]]; then push_flags=(--tls-verify=false); fi

# Prints the digest of an image in the local registry.
digest() {
  curl -fsSI -H 'Accept: application/vnd.oci.image.manifest.v1+json' \
    -H 'Accept: application/vnd.docker.distribution.manifest.v2+json' \
    "http://$registry/v2/$1/manifests/$2" |
    tr -d '\r' | awk 'tolower($1) == "docker-content-digest:" { print $2 }'
}

# Builds and pushes an image, and prints its name pinned by digest.
publish() {
  "$tool" build -q -f "$2" -t "$registry/$1:dev" . >&2
  "$tool" push "${push_flags[@]}" "$registry/$1:dev" >&2
  local pinned
  pinned=$(digest "$1" dev)
  [[ -n $pinned ]] || { echo "No digest for $1" >&2; exit 1; }
  echo "$registry/$1@$pinned"
}

AGENT_IMAGE=$(publish justpass-agent apps/agent/Dockerfile)

# The worker pool is a Kubernetes resource; the worker image comes from the registry, see kustomization.yaml.
kubectl apply -k deploy/local
kubectl -n justpass-agents wait --for=create deploy/agent --timeout=60s # the controller makes it
kubectl -n justpass-agents rollout status deploy/agent --timeout=180s
deploy/local/workers.sh --fix # after a reboot the pods have new IPs that ate-api does not know

deploy/local/identity.sh

# Exits at once if the port-forwards already run. setsid: they outlive this script.
log=deploy/local/.identity/forward.log
setsid deploy/local/forward.sh >> "$log" 2>&1 < /dev/null &
for port in "$ATE_API_PORT" "$ATE_ROUTER_PORT"; do
  for _ in {1..60}; do
    if (: < "/dev/tcp/127.0.0.1/$port") 2> /dev/null; then continue 2; fi
    sleep 0.5
  done
  echo "Nothing listens on 127.0.0.1:$port, see $log" >&2
  exit 1
done

# The atespace, its access policy and the actor template live in ate-api, not in Kubernetes: see
# deploy/substrate/agents.yaml. Creating them takes an owner, which the API's own identity is not, so
# this mints a short-lived token for ate-client, a bootstrap owner of the kind cluster (what
# kubectl ate uses). Templates are immutable, so this prints the name of the one for this image.
owner_token=deploy/local/.identity/owner-token
trap 'rm -f deploy/local/.identity/owner-token' EXIT
(umask 077 && kubectl -n ate-system create token ate-client --audience api.ate-system.svc --duration 10m > "$owner_token")
ATE_TEMPLATE=$(uv run python -m api.substrate_sync deploy/substrate/agents.yaml \
  --agent-image "$AGENT_IMAGE" --target "127.0.0.1:$ATE_API_PORT" --server-name api.ate-system.svc \
  --ca-file deploy/local/.identity/mounted/trust-bundle.pem --token-file "$owner_token")
export ATE_TEMPLATE

compose=("$tool" compose -f compose.yaml -f deploy/local/compose.yaml)
"${compose[@]}" up -d --build --wait api context-uds context-relay

# The Service context-api has no selector, so its endpoint is the relay's address on the kind network,
# which is a new one whenever the container was recreated. Applying this again is harmless.
relay=$("$tool" inspect -f '{{(index .NetworkSettings.Networks "kind").IPAddress}}' \
  "$("${compose[@]}" ps -q context-relay)")
[[ -n $relay ]] || { echo "context-relay has no address on the kind network" >&2; exit 1; }
kubectl apply -f - << EOF
apiVersion: discovery.k8s.io/v1
kind: EndpointSlice
metadata:
  name: context-api
  namespace: justpass-agents
  labels:
    kubernetes.io/service-name: context-api
    endpointslice.kubernetes.io/managed-by: deploy.sh
addressType: IPv4
ports:
- name: http
  port: 8000
  protocol: TCP
endpoints:
- addresses: ["$relay"]
EOF
