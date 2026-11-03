#!/usr/bin/env bash
# Builds the agent and runs it on the local Substrate cluster, and the API next to the cluster in a
# container, as production has it on Cloud Run. Safe to run again.
# Needs the cluster up, kubectl ate, envsubst, openssl, and CONTAINER_TOOL (default docker; set it to
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
worker_digest=$(digest ateworker-gvisor latest)
[[ -n $worker_digest ]] || { echo "No ateworker-gvisor in $registry" >&2; exit 1; }
ATEWORKER_GVISOR_IMAGE=$registry/ateworker-gvisor@$worker_digest
# Prints one value of the agent-size ConfigMap, the only place that sizes an agent. Stops the
# deploy unless it is a whole number, so a missing key can't become an empty limit.
size() {
  local value
  value=$(kubectl create --dry-run=client -f deploy/substrate/agent-size.yaml -o "jsonpath={.data.$1}")
  [[ $value =~ ^[0-9]+$ ]] || { echo "agent-size.yaml: $1 is '$value', not a number" >&2; exit 1; }
  echo "$value"
}
AGENT_CPU_MILLICORES=$(size cpuMillicores)
memory_mib=$(size memoryMiB)
overhead_mib=$(size workerOverheadMiB)
WORKER_CPU_REQUEST_MILLICORES=$(size workerCpuRequestMillicores)
AGENT_MEMORY_BYTES=$((memory_mib * 1024 * 1024))
WORKER_MEMORY_MIB=$((memory_mib + overhead_mib))
export AGENT_IMAGE ATEWORKER_GVISOR_IMAGE AGENT_CPU_MILLICORES AGENT_MEMORY_BYTES WORKER_MEMORY_MIB \
  WORKER_CPU_REQUEST_MILLICORES
# Templates are immutable, so the name comes from everything in one: image, size and settings.
ATE_TEMPLATE=agent-$(ATE_TEMPLATE='' envsubst < deploy/substrate/agent-template.yaml | sha256sum | cut -c1-12)
export ATE_TEMPLATE

envsubst < deploy/substrate/agent-pool.yaml | kubectl apply -f -
kubectl apply -f deploy/substrate/agent-size.yaml # so the cluster shows the size too
kubectl -n justpass-agents wait --for=create deploy/agent --timeout=60s # the controller makes it
kubectl -n justpass-agents rollout status deploy/agent --timeout=180s

kubectl ate get atespace justpass-agents &> /dev/null || kubectl ate create atespace justpass-agents
# This get exits 0 even when there is no policy.
if kubectl ate get access-policy -a justpass-agents 2>&1 | grep -q 'no access policy'; then
  kubectl ate create access-policy -a justpass-agents -f deploy/substrate/access-policy.yaml
fi

if ! kubectl ate get actor-template "$ATE_TEMPLATE" -a justpass-agents &> /dev/null; then
  envsubst < deploy/substrate/agent-template.yaml | kubectl ate create actor-template -f -
fi
echo "Waiting for the golden snapshot of $ATE_TEMPLATE"
for _ in {1..120}; do
  tag=$(kubectl ate get actor-template "$ATE_TEMPLATE" -a justpass-agents -o json |
    jq -r '.status.goldenSnapshotStatus.goldenTag.name // empty')
  [[ -n $tag ]] && break
  sleep 2
done
[[ -n ${tag:-} ]] || { echo "No golden snapshot for $ATE_TEMPLATE" >&2; exit 1; }

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

"$tool" compose -f compose.yaml -f deploy/local/compose.yaml up -d --build --wait api
