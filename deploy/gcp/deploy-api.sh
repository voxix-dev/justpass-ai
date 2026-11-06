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
call() { curl -fsS -H "Authorization: Bearer $ACCESS_TOKEN" -H 'Content-Type: application/json' "$@"; }

# Send the whole template back with only the image changed, so nothing else in it is reset.
template=$(call "$url" | jq --arg image "$IMAGE" '.template | .containers[0].image = $image')
operation=$(call -X PATCH "$url?updateMask=template" -d "{\"template\": $template}" | jq -r .name)

for _ in $(seq 120); do
  state=$(call "$RUN_ENDPOINT/v2/$operation")
  if [ "$(jq -r '.done // false' <<<"$state")" = true ]; then
    if jq -e .error <<<"$state" >/dev/null; then
      echo "deploy of $IMAGE failed: $(jq -c .error <<<"$state")" >&2
      exit 1
    fi
    echo "deployed $IMAGE to $(call "$url" | jq -r .uri)"
    exit 0
  fi
  sleep 5
done
echo "deploy of $IMAGE did not finish in 10 minutes" >&2
exit 1
