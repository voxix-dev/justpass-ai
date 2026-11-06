#!/usr/bin/env bash
# Sends jobs through the local API to the cluster's actors, and checks each actor along the way.
# Run deploy.sh first. Needs kubectl ate, curl, jq and CONTAINER_TOOL (default docker) with compose.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
source deploy/local/env.sh

api=http://localhost:${API_PORT:-8000}
fail() { echo "FAILED: $*" >&2; exit 1; }
for attempt in {1..60}; do
  curl -fs "$api/ready" > /dev/null && break
  ((attempt < 60)) || fail "no API on $api: run deploy.sh, and set API_PORT if it moved"
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

tool=${CONTAINER_TOOL:-docker}
drafts() { # job id: how many draft artifacts the agent has written through the Context API
  "$tool" compose -f compose.yaml exec -T db psql -U app -d app -Atc \
    "select count(*) from artifacts where job_id = '$1' and kind = 'draft'"
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
expect "the drafts after round 1" "$(drafts "$id")" 1
expect "the actor's egress policy" \
  "$(kubectl ate get egress-policy "job-$id" -a justpass-agents -o json 2> /dev/null | jq -c '.rules')" \
  '[{"http":{"hostnames":["context-api.justpass-agents.svc.cluster.local"],"ports":{"numbers":[8000]}}}]'
tunnels=$(kubectl -n ate-system logs deploy/atenet-egress -c ext-proc --since=10m |
  grep -c "egress tunnel opened.*\"name\":\"job-$id\"" || true)
((tunnels >= 1)) || fail "atenet-egress never opened a tunnel for job-$id: the actor did not reach the Context API through it"
echo "ok: the actor reached the Context API through the egress tunnel"

running=$(post "/jobs/$id/feedback" '{"text": "Explain step 2", "round": 1}')
expect "the job after feedback" "$(jq -r .status <<< "$running")" running
wait_for_job "$id" > /dev/null
revised=$(curl -fsS "$api/jobs/$id")
expect "the revised job" "$(jq -r .status <<< "$revised")" done
expect "its round" "$(jq -r .round <<< "$revised")" 2
expect "its feedback" "$(jq -r .feedback <<< "$revised")" "Explain step 2"
[[ $(jq -r .result <<< "$revised") == *"Explain step 2"* ]] || fail "the revised answer does not show the feedback"
echo "ok: the revised answer shows the feedback"
expect "the actor after round 2" "$(actor_state "$id")" ACTOR_STATE_SUSPENDED
expect "the drafts after round 2" "$(drafts "$id")" 2

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

# What the relay in front of the API lets through, asked from inside the cluster as an actor would:
# the Context API's own paths, and nothing else of the API.
probe=$(kubectl -n justpass-agents run probe --rm -i --restart=Never --quiet \
  --image=docker.io/curlimages/curl:8.11.1 --command -- sh -c '
  base=http://context-api.justpass-agents.svc.cluster.local:8000
  echo "jobs $(curl -s -o /dev/null -w "%{http_code}" "$base/jobs")"
  echo "context $(curl -s -o /dev/null -w "%{http_code}" -X POST "$base/v1/context/get_task")"
' 2> /dev/null)
expect "the relay's /jobs" "$(awk '$1 == "jobs" { print $2 }' <<< "$probe")" 404
expect "the relay's Context API without a token" "$(awk '$1 == "context" { print $2 }' <<< "$probe")" 401

# More jobs at once than the pool has workers: the later ones wait for a free one.
ids=()
for n in 1 2 3 4; do ids+=("$(post /jobs "{\"text\": \"Job $n of 4\"}" | jq -r .id)"); done
for job_id in "${ids[@]}"; do
  wait_for_job "$job_id" > /dev/null
  expect "job ${job_id:0:8} of four at once" "$(curl -fsS "$api/jobs/$job_id" | jq -r .status)" done
  post "/jobs/$job_id/accept" '{"round": 1}' > /dev/null
done

echo "All good."
