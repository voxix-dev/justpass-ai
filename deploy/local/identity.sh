#!/usr/bin/env bash
# Gives the API on this machine the identity it would have in the cluster, with no pod: keeps the
# namespace and service account, and writes these files to deploy/local/.identity/mounted/ (git-ignored):
#   trust-bundle.pem       CA that signs the router's and ate-api's serving certificates
#   credential-bundle.pem  private key and client certificate for the router's mTLS
#   token                  service account token for ate-api
# THE TOKEN AND THE CERTIFICATE LAST 24 HOURS. Run this again at any time: every file is replaced in
# one step, and the API rereads them without a restart.
# Needs the cluster up, kubectl, openssl and jq.
set -euo pipefail
shopt -s inherit_errexit # stop on failures inside $(...) too
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
source deploy/local/env.sh

namespace=homework
service_account=api
audience=api.ate-system.svc
dir=deploy/local/.identity/mounted

fail() { echo "FAILED: $*" >&2; exit 1; }

# Replaces a file with stdin in one step, so the API never reads half of one. The temporary file must
# start inside $dir: a file renamed into it keeps its old SELinux label, which the container can't read.
publish() {
  local tmp
  tmp=$(mktemp "$dir/.$1.XXXXXX") # 0600 until it is complete
  cat > "$tmp"
  chmod 644 "$tmp"
  mv "$tmp" "$dir/$1"
}

# Prints a certificate for the private key on stdin, from the signer behind the router's SPIFFE check.
# The request names a pod and node that don't exist: kube-apiserver checks those only for kubelets.
# Anyone else needs RBAC for PodCertificateRequests, and the signer takes the identity from the spec.
issue_certificate() {
  local sa_uid uid csr request json chain
  sa_uid=$(kubectl -n "$namespace" get serviceaccount "$service_account" -o jsonpath='{.metadata.uid}')
  uid=$(< /proc/sys/kernel/random/uuid)
  csr=$(openssl req -new -key /dev/stdin -subj / -outform DER | base64 -w0)
  request=$(kubectl create -o 'jsonpath={.metadata.name}' -f - << EOF
apiVersion: certificates.k8s.io/v1
kind: PodCertificateRequest
metadata:
  generateName: api-on-host-
  namespace: $namespace
spec:
  signerName: podidentity.podcert.ate.dev/identity
  podName: api-on-host
  podUID: $uid
  serviceAccountName: $service_account
  serviceAccountUID: $sa_uid
  nodeName: host
  nodeUID: $uid
  maxExpirationSeconds: 86400
  stubPKCS10Request: $csr
EOF
  )
  for _ in {1..60}; do
    json=$(kubectl -n "$namespace" get podcertificaterequest "$request" -o json)
    chain=$(jq -r '.status.certificateChain // empty' <<< "$json")
    if [[ -n $chain ]]; then
      echo "$chain"
      return
    fi
    if jq -e '.status.conditions // [] | any(.type == "Denied" or .type == "Failed")' <<< "$json" > /dev/null; then
      fail "$request was refused: $(jq -c .status.conditions <<< "$json")"
    fi
    sleep 1
  done
  fail "no certificate in $request" # kube-apiserver garbage collects the requests themselves
}

kubectl apply -f - << EOF
apiVersion: v1
kind: Namespace
metadata:
  name: $namespace
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: $service_account
  namespace: $namespace
EOF

# Everything is fetched before the first write, so a failure leaves the last good files in place.
ca=$(kubectl get clustertrustbundles -l podcert.ate.dev/canarying=live -o json |
  jq -r '.items[] | select(.spec.signerName == "servicedns.podcert.ate.dev/identity") | .spec.trustBundle')
[[ -n $ca ]] || fail "no servicedns trust bundle"
token=$(kubectl -n "$namespace" create token "$service_account" --audience "$audience" --duration 24h)
key=$(openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256)
chain=$(issue_certificate <<< "$key")

# Rootless podman runs the image's uid 10001 as an unrelated host uid, which is "other" to these files:
# with 0600 the container could not read them, so they are world-readable. The private parent keeps
# other host users out, and the bind mount of the subdirectory doesn't need to pass through it.
install -d -m 700 deploy/local/.identity
install -d -m 755 "$dir"
publish trust-bundle.pem <<< "$ca"
publish token <<< "$token"
publish credential-bundle.pem <<< "$key"$'\n'"$chain"
echo "Wrote $dir, valid for 24 hours"
