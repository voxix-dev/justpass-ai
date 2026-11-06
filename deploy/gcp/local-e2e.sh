#!/usr/bin/env bash
# Test the production setup on this machine against floci-gcp, a GCP emulator that runs Cloud SQL and Cloud Run
# as containers: the Terraform module, then the deploy script, then the API serving requests from its database.
#
#   deploy/gcp/local-e2e.sh        from a clean state: start floci-gcp, build, apply, deploy, check, leave it up
#   deploy/gcp/local-e2e.sh down   remove floci-gcp, its containers and the local Terraform state
#
# ENGINE=podman (default) or docker. Needs the engine's socket, curl, jq and Terraform (or the `devops` toolbox).
set -euo pipefail
cd "$(dirname "$0")/../.."

ENGINE="${ENGINE:-podman}"
FLOCI_IMAGE=docker.io/floci/floci-gcp:0.12.0
FLOCI_URL=http://localhost:4588
IMAGE=localhost/justpass-api
tf_dir=deploy/terraform/local

if command -v terraform >/dev/null; then terraform=(terraform); else terraform=(toolbox run -c devops terraform); fi
tf() { "${terraform[@]}" -chdir="$tf_dir" "$@"; }

down() {
  "$ENGINE" ps -aq --filter name=floci-gcp | xargs -r "$ENGINE" rm -f >/dev/null
  "$ENGINE" volume ls -q --filter name=floci-gcp | xargs -r "$ENGINE" volume rm >/dev/null
  rm -f "$tf_dir/terraform.tfstate" "$tf_dir/terraform.tfstate.backup"
}

if [ "${1:-}" = down ]; then down; exit; fi
down

# floci-gcp starts the databases and the API as sibling containers through the engine's socket, and it must
# reach them over the engine's bridge network (rootless podman would otherwise give it pasta networking).
if [ "$ENGINE" = podman ]; then
  socket=$(podman info --format '{{.Host.RemoteSocket.Path}}')
  engine_args=(--network podman --security-opt label=disable)
else
  socket=/var/run/docker.sock
  engine_args=(--network bridge)
fi
"$ENGINE" run -d --name floci-gcp "${engine_args[@]}" -p 127.0.0.1:4588:4588 \
  -v "$socket:/var/run/docker.sock" "$FLOCI_IMAGE" >/dev/null
for i in $(seq 60); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' $FLOCI_URL/)" != 000 ] && break
  [ "$i" -lt 60 ] || { echo "floci-gcp did not answer on $FLOCI_URL within 60 s" >&2; exit 1; }
  sleep 1
done

"$ENGINE" build -t $IMAGE:dev -f apps/api/Dockerfile .
"$ENGINE" tag $IMAGE:dev $IMAGE:e2e

tf init -input=false
tf apply -input=false -auto-approve -var "image=$IMAGE:dev"

# The deploy script is the one CI runs. floci-gcp's engine client can hold a connection the engine has closed
# while idle, which fails the first call with "Broken pipe": a second attempt works.
deploy() {
  PROJECT_ID=$(tf output -raw project_id) REGION=$(tf output -raw region) IMAGE=$IMAGE:e2e \
    ACCESS_TOKEN=floci RUN_ENDPOINT=$FLOCI_URL deploy/gcp/deploy-api.sh
}
deploy || deploy

# floci-gcp serves each service on its own host name at one port: send that name to 127.0.0.1.
host=$(tf output -raw service_uri | sed 's|^http://||')
for i in $(seq 60); do
  curl -fsS -H "Host: $host" "$FLOCI_URL/ready" >/dev/null 2>&1 && break
  [ "$i" -lt 60 ] || { echo "the API did not become ready within 2 minutes" >&2; exit 1; }
  sleep 2
done
curl -fsS -H "Host: $host" "$FLOCI_URL/ready"; echo

# /ready runs SELECT 1; also show that the container's start-up migrations ran in the Cloud SQL database.
version=$("$ENGINE" exec floci-gcp-cloudsql-local-project-api psql -U postgres -d app -tAc 'select version_num from alembic_version')
[ -n "$version" ] || { echo "migrations did not run" >&2; exit 1; }
echo "e2e passed: the API serves from Cloud SQL (migration $version) on image $IMAGE:e2e"
