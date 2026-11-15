#!/usr/bin/env bash
# Test the production setup on this machine against floci-gcp, a GCP emulator that runs Cloud SQL, Cloud Run and
# GKE (a k3s) as containers: the Terraform modules, then the deploy script, then the API serving requests from its
# database.
#
#   deploy/gcp/local-e2e.sh        from a clean state: start floci-gcp, build, apply, deploy, check, leave it up
#   deploy/gcp/local-e2e.sh down   remove floci-gcp, its containers and the local Terraform state
#
# ENGINE=docker (default) or podman. Needs the engine's socket, curl, jq, kubectl and Terraform.
set -euo pipefail
cd "$(dirname "$0")/../.."

ENGINE="${ENGINE:-docker}"
FLOCI_IMAGE=docker.io/floci/floci-gcp:0.12.0
FLOCI_URL=http://localhost:4588
FLOCI_NAME=justpass-floci-gcp
NAMESPACE=justpass-e2e
IMAGE=localhost/justpass-api
tf_dir=deploy/terraform/local

tf() { terraform -chdir="$tf_dir" "$@"; }

down() {
  "$ENGINE" ps -aq --filter "name=^$FLOCI_NAME\$" --filter "name=^floci-gcp-$NAMESPACE-" | xargs -r "$ENGINE" rm -fv >/dev/null
  "$ENGINE" volume ls -q --filter "name=^floci-gcp-$NAMESPACE-" | xargs -r "$ENGINE" volume rm >/dev/null
  rm -f "$tf_dir/terraform.tfstate" "$tf_dir/terraform.tfstate.backup"
}

if [ "${1:-}" = down ]; then down; exit; fi
down

# floci-gcp starts the databases and the API as sibling containers through the engine's socket, and it must
# reach them over the engine's bridge network (rootless podman would otherwise give it pasta networking).
if [ "$ENGINE" = podman ]; then
  socket=$(podman info --format '{{.Host.RemoteSocket.Path}}')
  engine_args=(--network podman --security-opt label=disable)
  # The kubelet of the GKE k3s needs this config to start in a rootless container.
  k3s_image=localhost/justpass-k3s-rootless
  podman build -q -t $k3s_image - >/dev/null <<'EOF'
FROM docker.io/rancher/k3s:v1.34.1-k3s1
COPY <<CONF /etc/rancher/k3s/config.yaml
kubelet-arg:
  - feature-gates=KubeletInUserNamespace=true
  - cgroups-per-qos=false
  - enforce-node-allocatable=
CONF
EOF
  engine_args+=(-e "FLOCI_GCP_SERVICES_GKE_DEFAULT_IMAGE=$k3s_image")
else
  socket=/var/run/docker.sock
  engine_args=(--network bridge)
fi
"$ENGINE" run -d --name $FLOCI_NAME "${engine_args[@]}" -p 127.0.0.1:4588:4588 \
  -e FLOCI_GCP_DOCKER_RESOURCE_NAMESPACE=$NAMESPACE -v "$socket:/var/run/docker.sock" "$FLOCI_IMAGE" >/dev/null
for i in $(seq 60); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' $FLOCI_URL/)" != 000 ] && break
  [ "$i" -lt 60 ] || { echo "floci-gcp did not answer on $FLOCI_URL within 60 s" >&2; exit 1; }
  sleep 1
done

"$ENGINE" build -t $IMAGE:dev -f apps/api/Dockerfile .
"$ENGINE" tag $IMAGE:dev $IMAGE:e2e

tf init -input=false
tf apply -input=false -auto-approve -var "image=$IMAGE:dev"

# The GKE cluster is a k3s that takes any bearer token as cluster-admin.
cluster=$(tf output -json cluster)
cluster_url="$FLOCI_URL/container/v1/projects/$(jq -r .project_id <<<"$cluster")/locations/$(jq -r .location <<<"$cluster")/clusters/$(jq -r .name <<<"$cluster")"
kube_server=$(curl -fsS "$cluster_url" | jq -r .endpoint)
for i in $(seq 60); do
  kubectl --server "https://$kube_server" --token floci --insecure-skip-tls-verify get nodes >/dev/null 2>&1 && break
  [ "$i" -lt 60 ] || { echo "the GKE cluster did not answer kubectl at $kube_server within 2 minutes" >&2; exit 1; }
  sleep 2
done

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
version=$("$ENGINE" exec "floci-gcp-$NAMESPACE-cloudsql-local-project-$(tf output -raw cloud_sql_instance_name)" psql -U postgres -d app -tAc 'select version_num from alembic_version')
[ -n "$version" ] || { echo "migrations did not run" >&2; exit 1; }
echo "e2e passed: the API serves from Cloud SQL (migration $version) on image $IMAGE:e2e"
