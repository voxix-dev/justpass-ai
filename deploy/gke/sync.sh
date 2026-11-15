#!/usr/bin/env bash
# Brings the cluster that kubectl points at to the state the API on Cloud Run needs: Substrate and the
# agents installed, ate-api and the router behind internal load balancers, and the credentials the API
# reads from Secret Manager written to OUT_DIR. Safe to run again, and CI does on every deploy; the daily
# refresh runs only credentials.sh.
# Env:
#   CLUSTER        the `cluster` output of the production Terraform, as JSON
#   SUBSTRATE_DIR  a checkout of github.com/agent-substrate/substrate at the version in substrate-install.yaml;
#                  its router manifest is patched in place (router-client-ca.patch)
#   OUT_DIR        receives token, ca.crt and template
#   AGENT_IMAGE    the agent image pinned by digest, for the actor template
# Needs kubectl, go, uv, jq and git. Fields of CLUSTER that are empty fall back to ate-setup's own settings,
# so a cluster without GCP (such as kind) works with ate-setup's environment variables.
set -euo pipefail
shopt -s inherit_errexit # stop on failures inside $(...) too
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
: "${CLUSTER:?}" "${SUBSTRATE_DIR:?}" "${OUT_DIR:?}" "${AGENT_IMAGE:?}"
SUBSTRATE_DIR=$(realpath "$SUBSTRATE_DIR")
gke=$PWD/deploy/gke
audience=api.ate-system.svc
mkdir -p "$OUT_DIR"

cluster() { jq -r --arg k "$1" '.[$k] // empty' <<< "$CLUSTER"; }
require() { [[ -n $(cluster "$1") ]] || { echo "CLUSTER has no $1" >&2; exit 1; }; }
for key in ate_api_ip router_ip run_cidr router_client_ca_pem; do require "$key"; done

work=$(mktemp -d)
forward=
cleanup() {
  [[ -z $forward ]] || kill "$forward" 2> /dev/null || true
  rm -rf "${work:?}"
}
trap cleanup EXIT

# The router checks client certificates against our CA instead of Substrate's, which would only
# accept pods. The patch changes the router manifest in the checkout, because ate-setup applies that
# manifest on every run and would undo the same change made on the cluster. The ConfigMap comes first
# so the router's pods can start the first time.
git -C "$SUBSTRATE_DIR" apply --reverse --check -p0 "$gke/router-client-ca.patch" 2> /dev/null ||
  git -C "$SUBSTRATE_DIR" apply -p0 "$gke/router-client-ca.patch"
kubectl create namespace ate-system --dry-run=client -o yaml | kubectl apply -f - > /dev/null
ca_map=atenet-router-client-ca
old_ca=$(kubectl -n ate-system get configmap "$ca_map" -o 'jsonpath={.data.ca\.pem}' 2> /dev/null || true)
new_ca=$(cluster router_client_ca_pem)
echo "$new_ca" > "$work/ca.pem"
kubectl -n ate-system create configmap "$ca_map" --from-file=ca.pem="$work/ca.pem" \
  --dry-run=client -o yaml | kubectl apply -f - > /dev/null

# ate-setup reads the rest of its settings from the environment and from this file; flags beat both.
# --context stops it from running gcloud to fetch credentials that kubectl already has.
setup=(deploy ate-system --config "$gke/substrate-install.yaml" --sandbox-classes gvisor --no-dev-env
  --context "$(kubectl config current-context)")
flag() { # flag, CLUSTER key
  local value
  value=$(cluster "$2")
  if [[ -n $value ]]; then setup+=("$1" "$value"); fi
}
flag --gcp-project-id project_id
flag --gcp-cluster-name name
flag --gcp-cluster-location location
flag --snapshot-location snapshot_location
flag --snapshot-transfer-gsa-name snapshot_transfer_gsa
flag --ateapi-postgres-cloudsql-instance cloudsql_instance
flag --ateapi-postgres-cloudsql-gsa cloudsql_gsa
# ate-setup's own connection string names the database "atepg", and ours has another name. The user is the
# service account without its domain suffix, and the Auth Proxy sidecar in ate-api's pod supplies the IAM token.
if [[ -n $(cluster cloudsql_instance) ]]; then
  setup+=(--ateapi-postgres-cloudsql-ip-type public) # the instance has no private IP; ate-setup's default is private
  gsa=$(cluster cloudsql_gsa)
  ATE_API_POSTGRES_READ_WRITE_CONNECTION_STRING="user=${gsa%.gserviceaccount.com} host=127.0.0.1 port=5432 dbname=$(cluster cloudsql_database) sslmode=disable"
  export ATE_API_POSTGRES_READ_WRITE_CONNECTION_STRING
fi
(cd "$SUBSTRATE_DIR" && go run ./cmd/ate-setup "${setup[@]}")

# Our resources, with the addresses of this cluster. The base has placeholders for them.
cat > "$work/kustomization.yaml" << EOF2
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
- $(realpath --relative-to="$work" "$gke")
patches:
- target: {kind: Service, name: api-internal}
  patch: |-
    - {op: replace, path: /spec/loadBalancerIP, value: "$(cluster ate_api_ip)"}
    - {op: replace, path: /spec/loadBalancerSourceRanges, value: ["$(cluster run_cidr)"]}
- target: {kind: Service, name: atenet-router-internal}
  patch: |-
    - {op: replace, path: /spec/loadBalancerIP, value: "$(cluster router_ip)"}
    - {op: replace, path: /spec/loadBalancerSourceRanges, value: ["$(cluster run_cidr)"]}
- target: {kind: NetworkPolicy, name: ate-api-server-from-run}
  patch: |-
    - {op: replace, path: /spec/ingress/0/from/0/ipBlock/cidr, value: "$(cluster run_cidr)"}
EOF2
kubectl apply -k "$work" > /dev/null

# Pods read the CA once, so a router that already ran with another one restarts. A new router, or one
# that ate-setup just rolled, already has this one.
if [[ -n $old_ca && $old_ca != "$new_ca" ]]; then
  kubectl -n ate-system rollout restart deployment/atenet-router
fi
kubectl -n ate-system rollout status deployment/atenet-router --timeout=5m

# What the API reads, written only after everything above worked. Files appear in one step each.
"$gke/credentials.sh" "$work"

# The atespace, its policy and the actor template live in ate-api, not in Kubernetes. Creating them takes
# an owner, which the API is not, so this uses a short-lived token for ate-client, the bootstrap owner.
# The port-forward reaches ate-api as 127.0.0.1 and its certificate names the service.
(umask 077 && kubectl -n ate-system create token ate-client --audience "$audience" --duration 10m > "$work/owner-token")
kubectl -n ate-system port-forward --address 127.0.0.1 svc/api :443 > "$work/forward.log" 2>&1 &
forward=$!
port=
for _ in {1..60}; do
  port=$(sed -n 's/^Forwarding from 127.0.0.1:\([0-9]*\) -> .*/\1/p' "$work/forward.log" | head -n 1)
  if [[ -n $port ]]; then break; fi
  sleep 0.5
done
[[ -n $port ]] || { echo "No port-forward to ate-api: $(cat "$work/forward.log")" >&2; exit 1; }
uv run python -m api.substrate_sync deploy/substrate/agents.yaml --agent-image "$AGENT_IMAGE" \
  --target "127.0.0.1:$port" --server-name "$audience" --ca-file "$work/ca.crt" \
  --token-file "$work/owner-token" > "$work/template"

mv "$work/token" "$OUT_DIR/token"
mv "$work/ca.crt" "$OUT_DIR/ca.crt"
mv "$work/template" "$OUT_DIR/template"
