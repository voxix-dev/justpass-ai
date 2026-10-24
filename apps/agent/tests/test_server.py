import http.client
import threading
from collections.abc import Iterator

import httpx2
import pytest

from agent import server
from agent.fakes import FakeContextClient, ScriptedModel
from agent.job import Deps
from agent.server import ActorServer
from shared.agent_run import RunRequest

JOB = {"job_id": "job-1", "playbook": "essay", "task": "Essay on WW1, 300 words."}


@pytest.fixture
def actor_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[ActorServer]:
    monkeypatch.setenv("AGENT_FAKES", "1")
    srv = ActorServer(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def actor(actor_server: ActorServer) -> Iterator[httpx2.Client]:
    with httpx2.Client(base_url=f"http://127.0.0.1:{actor_server.server_port}") as client:
        yield client


def count_runs(monkeypatch: pytest.MonkeyPatch) -> list[RunRequest]:
    runs: list[RunRequest] = []
    real_deps_for = server.deps_for

    def counting_deps_for(request: RunRequest) -> Deps:
        runs.append(request)
        return real_deps_for(request)

    monkeypatch.setattr(server, "deps_for", counting_deps_for)
    return runs


def test_readyz(actor: httpx2.Client):
    assert actor.get("/readyz").json() == {"detail": "ok"}


def test_run_stores_the_result(actor: httpx2.Client, monkeypatch: pytest.MonkeyPatch):
    runs = count_runs(monkeypatch)
    assert actor.get("/jobs/job-1").status_code == 404
    first = actor.post("/run", json={"job": JOB})
    assert first.status_code == 200
    assert first.json()["status"] == "ok", first.json()["error"]
    assert first.json()["final_artifact_id"] == "art-2"
    assert actor.get("/jobs/job-1").json() == first.json()
    # A retry gets the stored result instead of paying for the job again.
    assert actor.post("/run", json={"job": JOB}).json() == first.json()
    assert len(runs) == 1


def test_failed_jobs_are_not_stored(actor: httpx2.Client, monkeypatch: pytest.MonkeyPatch):
    def no_replies(_: RunRequest) -> Deps:  # the model crashes, so the job fails
        return Deps(llm=ScriptedModel({}), context=lambda _: FakeContextClient())

    monkeypatch.setattr(server, "deps_for", no_replies)
    assert actor.post("/run", json={"job": JOB}).json()["status"] == "failed"
    assert actor.get("/jobs/job-1").status_code == 404


def test_a_running_job_is_not_started_twice(actor: httpx2.Client, actor_server: ActorServer):
    actor_server.jobs.running.add("job-1")
    assert actor.post("/run", json={"job": JOB}).status_code == 409
    assert actor.get("/jobs/job-1").status_code == 409


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ({"job": JOB, "extra": 1}, "Extra inputs are not permitted"),
        ({"job": {**JOB, "playbook": "nope"}}, "unknown playbook"),
    ],
)
def test_bad_requests(actor: httpx2.Client, body: dict[str, object], error: str):
    resp = actor.post("/run", json=body)
    assert resp.status_code == 400
    assert error in resp.json()["detail"]


@pytest.mark.parametrize("length", ["-1", "1000001"])
def test_bad_content_length(actor_server: ActorServer, length: str):
    conn = http.client.HTTPConnection("127.0.0.1", actor_server.server_port, timeout=5)
    conn.putrequest("POST", "/run")
    conn.putheader("Content-Length", length)
    conn.endheaders()
    assert conn.getresponse().status == 413


def test_real_mode_needs_context_tokens(actor: httpx2.Client, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AGENT_FAKES")
    resp = actor.post("/run", json={"job": JOB})
    assert "no Context API token" in resp.json()["detail"]


def test_crashes_answer_500(actor: httpx2.Client, monkeypatch: pytest.MonkeyPatch):
    def broken(_: RunRequest) -> Deps:
        raise KeyError("CONTEXT_API_URL")

    monkeypatch.setattr(server, "deps_for", broken)
    resp = actor.post("/run", json={"job": JOB})
    assert (resp.status_code, resp.json()) == (500, {"detail": "internal error"})


def test_unknown_paths(actor: httpx2.Client):
    assert actor.get("/nope").status_code == 404
    assert actor.post("/nope").status_code == 404
