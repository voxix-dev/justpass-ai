"""The HTTP server inside the sandbox actor. It runs model-written Python and nothing else.

The actor holds no tokens and no user data, and has no egress policy, so it has no network. The
server handles one request at a time, so runs never overlap: the memory limit holds for the whole
actor, and later requests wait in the socket's backlog. Run with `python -m sandbox.server`.
"""

import logging
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer

from pydantic import BaseModel

from sandbox.runner import Limits, run_code
from shared.code_run import CodeRun

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 100_000  # 20k characters of code, as UTF-8 JSON


class Detail(BaseModel):
    detail: str


class SandboxServer(HTTPServer):
    def __init__(self, port: int, limits: Limits | None = None) -> None:
        super().__init__(("", port), Handler)
        self.limits = limits or Limits()


class Handler(BaseHTTPRequestHandler):
    timeout = 30  # per socket read/write, so a client that sends too little can't hang the server

    def do_GET(self) -> None:
        if self.path == "/readyz":  # Substrate's wakeup probe
            return self._send(HTTPStatus.OK, Detail(detail="ok"))
        self._send(HTTPStatus.NOT_FOUND, Detail(detail="not found"))

    def do_POST(self) -> None:
        if self.path != "/run":
            return self._send(HTTPStatus.NOT_FOUND, Detail(detail="not found"))
        assert isinstance(self.server, SandboxServer)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 <= length <= MAX_BODY_BYTES:
                return self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, Detail(detail="bad length"))
            run = CodeRun.model_validate_json(self.rfile.read(length))
        except ValueError as e:  # includes a ValidationError
            return self._send(HTTPStatus.BAD_REQUEST, Detail(detail=str(e)))
        try:
            result = run_code(run.code, self.server.limits)
        except Exception:
            log.exception("POST /run crashed")
            return self._send(HTTPStatus.INTERNAL_SERVER_ERROR, Detail(detail="internal error"))
        self._send(HTTPStatus.OK, result)

    def _send(self, status: HTTPStatus, body: BaseModel) -> None:
        data = body.model_dump_json().encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    if os.geteuid() == 0:
        os.chmod("/", 0o755)  # Substrate's root dir is 0700: let scripts, run as nobody, reach /usr
    SandboxServer(int(os.environ.get("PORT", "80"))).serve_forever()


if __name__ == "__main__":
    main()
