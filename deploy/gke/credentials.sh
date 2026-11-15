#!/usr/bin/env bash
# Writes the cluster credentials the API reads from Secret Manager into the directory given: token, the
# API's ate-api token (48 hours), and ca.crt, the CA of ate-api's and the router's serving certificates.
# Needs only kubectl and jq: sync.sh runs it after applying Substrate, and the daily refresh runs it alone
# against the cluster as it is.
#   credentials.sh DIR
set -euo pipefail
shopt -s inherit_errexit # stop on failures inside $(...) too
out=${1:?usage: credentials.sh DIR}

ca=$(kubectl get clustertrustbundles -l podcert.ate.dev/canarying=live -o json |
  jq -r '.items[] | select(.spec.signerName == "servicedns.podcert.ate.dev/identity") | .spec.trustBundle')
[[ -n $ca ]] || { echo "No servicedns trust bundle" >&2; exit 1; }
(umask 077 && kubectl -n homework create token api --audience api.ate-system.svc --duration 48h > "$out/token")
(umask 077 && echo "$ca" > "$out/ca.crt")
jq -R 'split(".")[1] | gsub("-"; "+") | gsub("_"; "/") | @base64d | fromjson | "Token lives \(.exp - .iat) seconds"' \
  -r "$out/token" >&2
