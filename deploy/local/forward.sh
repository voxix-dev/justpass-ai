#!/usr/bin/env bash
# Forwards ate-api and the atenet router to this machine for the API. Only on 127.0.0.1: neither may
# reach the LAN. A port-forward ends when its pod or connection goes, so each one restarts in a loop.
# Ports: ATE_API_PORT and ATE_ROUTER_PORT, see env.sh.
# deploy.sh starts this in the background. Only one copy runs: another start exits at once.
# To stop it: kill "$(cat deploy/local/.identity/forward.pid)"
set -euo pipefail
shopt -s inherit_errexit # stop on failures inside $(...) too
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
source deploy/local/env.sh

# Stopping sends a signal to the whole process group, so this script must lead its own.
[[ $(ps -o pgid= -p $$ | tr -d ' ') == "$$" ]] || exec setsid "$0" "$@"

install -d -m 700 deploy/local/.identity
pid_file=deploy/local/.identity/forward.pid
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

# Also stops the loops and their kubectl, which would otherwise ignore Ctrl-C and keep the lock.
trap 'trap - INT TERM; kill 0' INT TERM
forward api "$ATE_API_PORT" &
forward atenet-router "$ATE_ROUTER_PORT" &
wait
