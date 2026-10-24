"""The HTTP server inside the Substrate actor. The dispatcher POSTs a job, the actor runs it.

Results of finished jobs stay in memory. The actor template snapshots memory, so after a
suspend and resume GET /jobs/<id> still answers, and a retried POST /run returns the stored
result instead of paying for the job twice. A failed job is not stored: a retry runs it again.

Run with `python -m agent.server`. AGENT_FAKES=1 swaps in a scripted model and an in-memory
Context API: no network, no paid calls.
"""

import asyncio
import logging
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pydantic import BaseModel

from agent.fakes import FakeContextClient, essay_demo
from agent.job import Deps, run_job
from shared.agent_run import JobResult, RunRequest

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 1_000_000


class Detail(BaseModel):
    detail: str


def deps_for(request: RunRequest) -> Deps:
    if os.environ.get("AGENT_FAKES") == "1":
        context = FakeContextClient()
        return Deps(llm=essay_demo(), context=lambda _: context)
    return Deps.from_env(request.context_tokens)


class Jobs:
    def __init__(self) -> None:
        self.results: dict[str, JobResult] = {}
        self.running: set[str] = set()
        self._lock = threading.Lock()

    def run(self, request: RunRequest) -> JobResult | None:
        """The job's result, or None while another request is running the same job."""
        job_id = request.job.job_id
        with self._lock:
            if job_id in self.results:
                return self.results[job_id]
            if job_id in self.running:
                return None
            self.running.add(job_id)
        try:
            result = asyncio.run(run_job(request.job, deps_for(request)))
            if result.status == "ok":
                with self._lock:
                    self.results[job_id] = result
            return result
        finally:
            with self._lock:
                self.running.discard(job_id)


class ActorServer(ThreadingHTTPServer):
    def __init__(self, port: int) -> None:
        super().__init__(("", port), Handler)
        self.jobs = Jobs()


class Handler(BaseHTTPRequestHandler):
    timeout = 30  # per socket read/write, so a client that sends too little can't hang a thread

    def do_GET(self) -> None:
        if self.path == "/readyz":  # Substrate's wakeup probe
            return self._send(HTTPStatus.OK, Detail(detail="ok"))
        jobs, job_id = self._jobs(), self.path.removeprefix("/jobs/")
        if not self.path.startswith("/jobs/"):
            return self._send(HTTPStatus.NOT_FOUND, Detail(detail="not found"))
        if job_id in jobs.running:
            return self._send(HTTPStatus.CONFLICT, Detail(detail="this job is still running"))
        if job_id not in jobs.results:
            return self._send(HTTPStatus.NOT_FOUND, Detail(detail="not found"))
        self._send(HTTPStatus.OK, jobs.results[job_id])

    def do_POST(self) -> None:
        if self.path != "/run":
            return self._send(HTTPStatus.NOT_FOUND, Detail(detail="not found"))
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 <= length <= MAX_BODY_BYTES:
                return self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, Detail(detail="bad length"))
            request = RunRequest.model_validate_json(self.rfile.read(length))
            result = self._jobs().run(request)
        except ValueError as e:  # a bad body, unknown playbook or missing token
            return self._send(HTTPStatus.BAD_REQUEST, Detail(detail=str(e)))
        except Exception:
            log.exception("POST /run crashed")
            return self._send(HTTPStatus.INTERNAL_SERVER_ERROR, Detail(detail="internal error"))
        if result is None:
            return self._send(HTTPStatus.CONFLICT, Detail(detail="this job is already running"))
        self._send(HTTPStatus.OK, result)

    def _jobs(self) -> Jobs:
        assert isinstance(self.server, ActorServer)
        return self.server.jobs

    def _send(self, status: HTTPStatus, body: BaseModel) -> None:
        data = body.model_dump_json().encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    ActorServer(int(os.environ.get("PORT", "80"))).serve_forever()


if __name__ == "__main__":
    main()
