#!/usr/bin/env bash
# Forwards ate-api and the atenet router to this machine for the API. Only on 127.0.0.1: neither may
# reach the LAN. A port-forward ends when its pod or connection goes, so each one restarts in a loop.
# Ports: ATE_API_PORT and ATE_ROUTER_PORT, see env.sh.
# deploy.sh starts this in the background. Only one copy runs: another start exits at once.
# To stop it: kill "$(cat deploy/kind/.identity/forward.pid)"
set -euo pipefail
shopt -s inherit_errexit # stop on failures inside $(...) too
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
source deploy/kind/env.sh

install -d -m 700 deploy/kind/.identity
pid_file=deploy/kind/.identity/forward.pid
exec 9>> "$pid_file" # holding this lock is what "running" means, so a stale pid can't fool it
flock -n 9 || { echo "Already running as pid $(< "$pid_file")"; exit 0; }
echo $$ > "$pid_file"

forward() { # service, local port
  while true; do
    # 9>&-: a leftover kubectl must not keep the lock. Its stdout is one line per connection.
    kubectl -n ate-system port-forward --address 127.0.0.1 "svc/$1" "$2:443" > /dev/null 9>&- || true
    echo "$(date -Is) port-forward to $1 ended, restarting"
    sleep 1
  done
}

trap 'trap - TERM; kill 0' TERM # also stops the loops and their kubectl: deploy.sh gives us our own group
forward api "$ATE_API_PORT" &
forward atenet-router "$ATE_ROUTER_PORT" &
wait
