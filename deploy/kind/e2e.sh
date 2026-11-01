#!/usr/bin/env bash
# Sends jobs through the API on the local cluster and checks each job's actor along the way.
# Run deploy.sh first. Needs kubectl ate, curl and jq.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
source deploy/kind/env.sh

port=${E2E_PORT:-18000}
api=http://localhost:$port
kubectl -n homework port-forward svc/api "$port:80" > /dev/null &
forward=$!
trap 'kill "$forward"' EXIT
fail() { echo "FAILED: $*" >&2; exit 1; }
for _ in {1..60}; do
  curl -fs "$api/ready" > /dev/null && break
  kill -0 "$forward" 2> /dev/null || fail "port-forward to the API died"
  sleep 0.5
done
post() { curl -fsS -X POST "$api$1" -H 'content-type: application/json' -d "$2"; }
actor_state() {
  kubectl ate get actor "job-$1" -a justpass-agents -o json 2> /dev/null | jq -r .status.state || echo gone
}

# Waits for the job to leave queued/running, and prints how many seconds that took.
wait_for_job() {
  local started=$SECONDS status
  for _ in {1..120}; do
    status=$(curl -fsS "$api/jobs/$1" | jq -r .status)
    if [[ $status != queued && $status != running ]]; then
      echo $((SECONDS - started))
      return
    fi
    sleep 1
  done
  fail "job $1 is still $status"
}

expect() { # name, actual, expected
  [[ $2 == "$3" ]] || fail "$1: got '$2', expected '$3'"
  echo "ok: $1 is $2"
}

job=$(post /jobs '{"text": "Solve x^2 - 5x + 6 = 0"}')
id=$(jq -r .id <<< "$job")
expect "a new job" "$(jq -r .status <<< "$job")" queued

seconds=$(wait_for_job "$id")
((seconds >= 10)) || fail "the agent answered in ${seconds}s, it should wait 10s"
echo "ok: the answer took ${seconds}s"
expect "the answered job" "$(curl -fsS "$api/jobs/$id" | jq -r .status)" done
expect "the answer" "$(curl -fsS "$api/jobs/$id" | jq -r '.result | split("\n")[0]')" "## Solution"
expect "the hibernated actor" "$(actor_state "$id")" ACTOR_STATE_SUSPENDED

running=$(post "/jobs/$id/feedback" '{"text": "Explain step 2", "round": 1}')
expect "the job after feedback" "$(jq -r .status <<< "$running")" running
wait_for_job "$id" > /dev/null
revised=$(curl -fsS "$api/jobs/$id")
expect "the revised job" "$(jq -r .status <<< "$revised")" done
expect "its round" "$(jq -r .round <<< "$revised")" 2
expect "its feedback" "$(jq -r .feedback <<< "$revised")" "Explain step 2"
expect "the actor after round 2" "$(actor_state "$id")" ACTOR_STATE_SUSPENDED

post "/jobs/$id/accept" '{"round": 2}' > /dev/null
for _ in {1..30}; do
  [[ $(actor_state "$id") == gone ]] && break
  sleep 1
done
expect "the accepted job's actor" "$(actor_state "$id")" gone

failed=$(post /jobs '{"text": "Please fail this one"}' | jq -r .id)
wait_for_job "$failed" > /dev/null
expect "a job the agent cannot solve" "$(curl -fsS "$api/jobs/$failed" | jq -r .status)" failed
expect "its actor" "$(actor_state "$failed")" gone

# More jobs at once than the pool has workers: the later ones wait for a free one.
ids=()
for n in 1 2 3 4; do ids+=("$(post /jobs "{\"text\": \"Job $n of 4\"}" | jq -r .id)"); done
for job_id in "${ids[@]}"; do
  wait_for_job "$job_id" > /dev/null
  expect "job ${job_id:0:8} of four at once" "$(curl -fsS "$api/jobs/$job_id" | jq -r .status)" done
  post "/jobs/$job_id/accept" '{"round": 1}' > /dev/null
done

echo "All good."
