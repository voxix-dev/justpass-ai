# The code sandbox: run_code in its own actor

The solver checks its maths with `run_code`. Model-written code is untrusted (homework text can
steer the model), so it never runs in the agent actor, which holds the job's Context API tokens.
Each job gets its own sandbox actor instead, with no network, no tokens and no user data.

| File | What |
|---|---|
| `../../apps/sandbox/` | The server (`POST /run`, `GET /readyz` on port 80) and the runner with the limits |
| `../../apps/sandbox/Dockerfile` | Python 3.14 + numpy, scipy, sympy |
| `sandbox-pool.yaml` | Namespace `justpass-sandbox` and its gVisor `WorkerPool` |
| `sandbox-template.yaml` | The `ActorTemplate`: no env, no volumes, and its actors get no `EgressPolicy` |

Bodies are `CodeRun` and `CodeRunResult` in `packages/shared/src/shared/code_run.py`.

## How the agent reaches it

```
solver ──run_code──▶ API: POST /v1/context/run_code ──mTLS──▶ atenet router ──▶ sandbox actor
       (solver's job token)         (identity homework/api, allowlisted)       sbx-<job_id>
```

An actor can't call another actor, and no router allowlist entry can change that. The router
only accepts mTLS clients, and an actor never holds a client certificate: its egress tunnel
holds the key, and `systemInfo` volumes only offer a trust bundle and the actor's metadata. We
tried it on kind. With a `tlsPassthrough` rule to the router, the router answers
`certificate required`. With an `https` rule, the gateway connects without a client certificate
and doesn't trust the router's CA, so the actor gets 503. Allowlisting the gateway's identity
would let every actor with such a rule reach every actor.

So the API relays `run_code`. It already is an allowlisted router client
(`spiffe://cluster.local/ns/homework/sa/api` in `ATE_INGRESS_ALLOWED_SPIFFE_IDS`), and it
already creates the job's agent actor. `deploy/kind/env.sh` needs no change. The agent's egress
policy doesn't change either, because the relay is on the Context API host. The relay (not
built yet, see the PR's follow-ups) must:

1. accept the call only with a token for the solver role (roles.py, checked again server side),
   and take the job id from that token, never from the request;
2. create `sbx-<job_id>` in atespace `justpass-sandbox` from template `sandbox` on first use,
   with no egress policy. Parallel solvers race here, so "already exists" counts as success;
3. validate the body as `CodeRun`, send it to the router's `/run` with header
   `ate-target-actor: justpass-sandbox/sbx-<job_id>`, and treat the answer as hostile: a read
   timeout (runs queue, so allow a few of them), a size cap (2 × 16 kB plus JSON), and a
   `CodeRunResult` check before anything goes back to the agent;
4. if the actor is `ACTOR_STATE_CRASHED` (see the limits below), delete it and create it again;
5. delete the sandbox when the job leaves `processing`.

Step 3 matters because a script can leave a process behind that tampers with its job's later
runs (see below). That only gets it its own job's results, which its code could change anyway.

## Limits

Set in `apps/sandbox/src/sandbox/runner.py`, inside the child before the script starts. The
caller can't change them.

| Limit | Value | When it is hit |
|---|---|---|
| Wall clock | 10 s | The process group is killed: `timed_out: true` |
| Output | 16 kB per stream (it goes back into a model's context) | Writes fail with "File too large": `output_truncated: true` |
| Memory | 512 MiB address space per process, 768 MiB for the actor | `MemoryError` |
| Processes | 16 processes and threads for the script's user | `fork` fails with `BlockingIOError` |
| Runs | One at a time per actor (a single-threaded server) | Later calls wait their turn |

A script that forks up to the process limit and fills memory in each process can still crash
its actor. Only that job's runs are hurt, and the relay recreates the sandbox (step 4).

## Decisions

- **Scripts run as `nobody`.** Substrate runs every actor process as root inside gVisor and
  ignores the image's `USER`. The template adds `SETUID`, `SETGID` and `DAC_OVERRIDE` so the
  server can start each script as `nobody` (without any capability) and delete what it leaves.
  So a script can't signal the server, read its memory or environment, or use up the process
  limit for it.
- **One fresh sandbox per job, and a fresh process for each run.** Jobs never share a sandbox,
  so code shaped by one student's homework never runs next to another student's: the job is the
  trust boundary. Each run gets a new process, an empty temp dir and a clean environment, and
  its leftover processes are killed. A script can still keep one alive (with `setsid`), but that
  only reaches its own job's later runs, so we never reset the sandbox between runs. A new actor
  starts from the golden snapshot: on kind, creating one and running a first script took about
  a second. Between runs it can be suspended like any actor.
- **No egress policy at all.** An actor without a policy gets no egress. Checked on kind from
  inside the sandbox: DNS for any name fails (also `kubernetes.default`), TCP to a public IP or
  to a cluster IP (the Kubernetes API, the router) is accepted and closed with no byte returned,
  `169.254.169.254` has no route, and UDP is dropped.
- **numpy, scipy, sympy.** Symbolic algebra and calculus (sympy, with mpmath), arrays and linear
  algebra (numpy), and statistics, integration, optimisation and ODEs (scipy) cover high school and
  first-year college maths, physics and stats homework. No plotting or data libraries: `run_code`
  returns text only.

## Deploy and test

```bash
docker build -f apps/sandbox/Dockerfile -t $REGISTRY/justpass-sandbox:dev .
docker push $REGISTRY/justpass-sandbox:dev
export SANDBOX_IMAGE=$(docker inspect --format '{{index .RepoDigests 0}}' $REGISTRY/justpass-sandbox:dev)
envsubst < deploy/substrate/sandbox-pool.yaml | kubectl apply -f -   # needs ATEWORKER_GVISOR_IMAGE
kubectl ate create atespace justpass-sandbox
envsubst < deploy/substrate/sandbox-template.yaml | kubectl ate create actor-template -f -
kubectl ate create actor sbx-test -a justpass-sandbox --template sandbox

curl $MTLS -X POST -H "ate-target-actor: justpass-sandbox/sbx-test" $ROUTER/run \
  -d '{"code": "import sympy; print(sympy.factor(\"x**2 - 1\"))"}'
```

`apps/agent/tests/test_sandbox_integration.py` runs real code through the agent's
`SandboxClient` and checks the network, the user and the limits. It is skipped unless
`SANDBOX_URL` is set. It needs a port-forward to the router and an allowlisted client's
credential bundle (certificate and key in one PEM):

```bash
kubectl -n ate-system port-forward svc/atenet-router 8443:443 &
SANDBOX_URL=https://127.0.0.1:8443/run SANDBOX_TLS_NAME=atenet-router.ate-system.svc \
SANDBOX_ACTOR=justpass-sandbox/sbx-test SANDBOX_CERT=client.pem SANDBOX_CA=router-ca.pem \
  uv run pytest -m integration
```
