#!/usr/bin/env bash
# Builds the API and the agent and runs them on the local Substrate cluster. Safe to run again.
# Needs the cluster up, kubectl ate, envsubst, and CONTAINER_TOOL (default docker; set it to podman
# in env.local.sh for rootless podman).
set -euo pipefail
shopt -s inherit_errexit # stop on failures inside $(...) too
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
source deploy/kind/env.sh

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

API_IMAGE=$(publish justpass-api apps/api/Dockerfile)
AGENT_IMAGE=$(publish justpass-agent apps/agent/Dockerfile)
ATE_TEMPLATE=agent-${AGENT_IMAGE: -12} # templates are immutable: one per image
worker_digest=$(digest ateworker-gvisor latest)
[[ -n $worker_digest ]] || { echo "No ateworker-gvisor in $registry" >&2; exit 1; }
ATEWORKER_GVISOR_IMAGE=$registry/ateworker-gvisor@$worker_digest
export API_IMAGE AGENT_IMAGE ATE_TEMPLATE ATEWORKER_GVISOR_IMAGE

envsubst < deploy/substrate/agent-pool.yaml | kubectl apply -f -
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

envsubst < deploy/kind/api.yaml | kubectl apply -f -
kubectl -n homework rollout status deploy/db --timeout=120s
kubectl -n homework rollout status deploy/api --timeout=120s
