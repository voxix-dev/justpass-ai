# The agent as a Substrate actor

One gVisor actor per homework job. The dispatcher sends the job to the actor, the actor runs
`run_job` and keeps the result in memory, and then the actor is suspended. The next request
resumes it from the snapshot.

| File | What |
|---|---|
| `../../apps/agent/Dockerfile` | The image: `python -m agent.server` on port 80, user 65532 |
| `agent-pool.yaml` | Namespace `justpass-agents` and its gVisor `WorkerPool` |
| `agent-template.yaml` | The `ActorTemplate` (a Substrate resource, not a CRD) |
| `egress-policy.yaml` | What one actor may reach: OpenRouter and the Context API, nothing else |

The actor's HTTP API (bodies are the models in `packages/shared/src/shared/agent_run.py`):

- `GET /readyz`: Substrate's wakeup probe.
- `POST /run` with a `RunRequest`: runs the job and returns a `JobResult`. If the job finished
  with `"status": "ok"`, sending the same `job_id` again returns the stored result, so the job
  is not paid for twice. A failed job is not stored, so sending it again runs it again. You get
  409 while that job is still running.
- `GET /jobs/<job_id>`: the stored `JobResult`, 409 while the job is running, or 404.

## Decisions

- **MEMORY snapshots.** A suspend keeps the whole process, so the stored results come back on
  resume with no code to save or load them. Later, the job's conversation comes back the same
  way for feedback rounds. Postgres stays the source of truth: if a snapshot is lost, the job
  starts again in a fresh actor. The cost is that the job's Context API tokens are in the
  snapshot, so they must expire when the job's review window ends.
- **No OpenRouter key in the actor.** The actor sends a placeholder `Authorization` header. An
  `https` egress rule lets the gateway terminate TLS to `openrouter.ai` and put the real key from
  a Kubernetes Secret in that header. The actor trusts the gateway's CA through the `trust`
  volume (`SSL_CERT_FILE`).
- **Deny by default.** The policy allows HTTPS to `openrouter.ai` and plain HTTP to
  `api.homework.svc.cluster.local:80`. Everything else is refused, and DNS lookups for any other
  name fail too.

## Deploy

You need a cluster with Substrate installed, its egress gateway running with the `k8s.io`
credential provider, `kubectl ate`, `envsubst`, and a registry the cluster can pull from.

```bash
# 1. Build and push the image from the repo root, then pin it by digest (templates require it).
docker build -f apps/agent/Dockerfile -t $REGISTRY/justpass-agent:dev .
docker push $REGISTRY/justpass-agent:dev
export AGENT_IMAGE=$(docker inspect --format '{{index .RepoDigests 0}}' $REGISTRY/justpass-agent:dev)

# 2. Worker pool. Use the gVisor worker image your Substrate install uses.
export ATEWORKER_GVISOR_IMAGE=<registry>/ateworker-gvisor@sha256:...
envsubst < deploy/substrate/agent-pool.yaml | kubectl apply -f -
kubectl -n justpass-agents rollout status deploy/agent

# 3. Atespace and template. AGENT_FAKES=1 uses a scripted model and an in-memory Context API
#    (no paid calls); leave it empty for real jobs.
kubectl ate create atespace justpass-agents
AGENT_FAKES=1 envsubst < deploy/substrate/agent-template.yaml | kubectl ate create actor-template -f -
kubectl ate get actor-templates -a justpass-agents   # wait for the GOLDEN TAG column

# 4. Once per cluster: the OpenRouter key, and permission for the atespace to use it.
kubectl -n justpass-agents create secret generic openrouter --from-literal=api-key=$OPENROUTER_API_KEY
kubectl -n ate-system edit configmap k8s-credential-provider-namespace-policy
#   policies:
#   - atespace: justpass-agents
#     allowedNamespaces: [justpass-agents]
kubectl -n ate-system rollout restart deployment/k8s-credential-provider
```

Templates are immutable. To ship a new image, create the template under a new name (`agent-v2`)
and point new actors at it.

## Run a job

```bash
kubectl ate create actor job-1 -a justpass-agents --template agent
kubectl ate create egress-policy job-1 -a justpass-agents -f deploy/substrate/egress-policy.yaml
```

The atenet router only accepts mTLS from allowlisted client identities. Send these requests
from such a client (in production, the API). The `ate-target-actor` header picks the actor.
`$MTLS` stands for that client's certificate flags:
`--cert <client.pem> --key <client.key> --cacert <router-ca.pem>`.

```bash
ROUTER=https://atenet-router.ate-system.svc
TARGET="ate-target-actor: justpass-agents/job-1"

curl $MTLS -X POST -H "$TARGET" -H 'Content-Type: application/json' $ROUTER/run \
  -d '{"job": {"job_id": "job-1", "playbook": "essay", "task": "Essay on WW1"}, "context_tokens": {}}'
```

With `AGENT_FAKES=1` you get `"status": "ok"` and `"final_artifact_id": "art-2"`. For real jobs,
`context_tokens` needs one Context API token for each role that uses Context tools.

## Suspend, resume, delete

```bash
kubectl ate suspend actor job-1 -a justpass-agents --mode=sync   # waits for the snapshot upload
kubectl ate get actor job-1 -a justpass-agents                   # ACTOR_STATE_SUSPENDED

curl $MTLS -H "$TARGET" $ROUTER/jobs/job-1   # resumes the actor; the stored result is still there

kubectl ate delete actor job-1 -a justpass-agents   # once the job is accepted or expired
```
