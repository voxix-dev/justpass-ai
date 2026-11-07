#!/usr/bin/env bash
# Checks that every agent worker pod is registered in ate-api under its current IP, and with --fix
# recreates the pods if one is not. Run by deploy.sh (--fix) and e2e.sh (check only).
#
# Why: after the kind node restarts (a host reboot), the same pods come back with new IPs, but ate-api
# keeps the IPs a worker registered with (they cannot be changed). Substrate's workersync only logs
# "registered worker IPs disagree with its pod", and the router answers 503 until the pods are new.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
source deploy/local/env.sh

stale() {
  local registered pod_ips ip
  registered=$(kubectl ate get worker -o json 2> /dev/null | jq -r '.. | .ips? // empty | .[]')
  pod_ips=$(kubectl -n justpass-agents get pods -l ate.dev/worker-pool=agent \
    -o jsonpath='{range .items[*]}{.status.podIP}{"\n"}{end}')
  for ip in $pod_ips; do
    grep -qx "$ip" <<< "$registered" || return 0
  done
  return 1
}

stale || exit 0
if [[ ${1:-} != --fix ]]; then
  echo "A worker pod's IP differs from the one it registered with, so the router answers 503." >&2
  echo "Run deploy/local/workers.sh --fix (it recreates the agent worker pods)." >&2
  exit 1
fi
echo "Recreating the agent worker pods: their IPs changed since they registered."
kubectl -n justpass-agents delete pod -l ate.dev/worker-pool=agent
sleep 3 # the controller makes the new pods
kubectl -n justpass-agents rollout status deploy/agent --timeout=180s
for _ in {1..60}; do
  stale || exit 0
  sleep 2
done
echo "The new worker pods did not register under their IPs" >&2
exit 1
