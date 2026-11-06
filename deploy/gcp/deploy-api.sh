#!/usr/bin/env bash
# Deploy an image to the API's Cloud Run service through the Cloud Run v2 REST API, and wait until it is serving.
# The same script runs in CI against Google Cloud and in deploy/gcp/local-e2e.sh against floci-gcp.
#
#   PROJECT_ID=... REGION=... IMAGE=... [SERVICE=api] [ACCESS_TOKEN=...] [RUN_ENDPOINT=...] deploy-api.sh
#
# ACCESS_TOKEN defaults to `gcloud auth print-access-token`. RUN_ENDPOINT defaults to Google's.
set -euo pipefail

: "${PROJECT_ID:?}" "${REGION:?}" "${IMAGE:?}"
SERVICE="${SERVICE:-api}"
RUN_ENDPOINT="${RUN_ENDPOINT:-https://run.googleapis.com}"
ACCESS_TOKEN="${ACCESS_TOKEN:-$(gcloud auth print-access-token)}"

url="$RUN_ENDPOINT/v2/projects/$PROJECT_ID/locations/$REGION/services/$SERVICE"
# Prints the response body; on an HTTP error prints it to stderr and fails, so the reason is not lost.
call() {
  local out code
  out=$(curl -sS --max-time 30 --retry 3 --retry-all-errors -w '\n%{http_code}' \
    -H "Authorization: Bearer $ACCESS_TOKEN" -H 'Content-Type: application/json' "$@") || return 1
  code=${out##*$'\n'}
  out=${out%$'\n'*}
  if [ "$code" -ge 400 ]; then echo "HTTP $code from $*: $out" >&2; return 1; fi
  printf '%s\n' "$out"
}

# Send the whole template back with only the image changed, so nothing else in it is reset.
template=$(call "$url" | jq --arg image "$IMAGE" '.template | .containers[0].image = $image')
operation=$(call -X PATCH "$url?updateMask=template" -d "{\"template\": $template}" | jq -r .name)

# Wait at most 10 minutes; a few failed polls in a row are not a failed deploy.
failures=0
while [ "$SECONDS" -lt 600 ]; do
  if state=$(call "$RUN_ENDPOINT/v2/$operation"); then
    failures=0
    if [ "$(jq -r '.done // false' <<<"$state")" = true ]; then
      if jq -e .error <<<"$state" >/dev/null; then
        echo "deploy of $IMAGE failed: $(jq -c .error <<<"$state")" >&2
        exit 1
      fi
      echo "deployed $IMAGE to $(call "$url" | jq -r .uri)"
      exit 0
    fi
  elif [ $((++failures)) -ge 5 ]; then
    echo "gave up polling $operation after 5 failures in a row" >&2
    exit 1
  fi
  sleep 5
done
echo "deploy of $IMAGE did not finish in 10 minutes" >&2
exit 1
