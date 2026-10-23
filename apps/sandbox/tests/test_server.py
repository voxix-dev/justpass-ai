import threading
from collections.abc import Iterator

import httpx2
import pytest

from sandbox.runner import Limits
from sandbox.server import SandboxServer
from shared.code_run import CodeRunResult


@pytest.fixture
def sandbox() -> Iterator[httpx2.Client]:
    srv = SandboxServer(0, Limits(timeout_s=2))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with httpx2.Client(base_url=f"http://127.0.0.1:{srv.server_port}") as client:
        yield client
    srv.shutdown()
    srv.server_close()


def test_readyz(sandbox: httpx2.Client):
    assert sandbox.get("/readyz").status_code == 200
    assert sandbox.get("/other").status_code == 404


def test_run_returns_the_result(sandbox: httpx2.Client):
    resp = sandbox.post("/run", json={"code": "print(2 ** 10)"})
    assert resp.status_code == 200
    assert CodeRunResult.model_validate_json(resp.content).stdout == "1024\n"


def test_run_rejects_bad_bodies(sandbox: httpx2.Client):
    assert sandbox.post("/run", json={"code": ""}).status_code == 400
    assert sandbox.post("/run", json={"code": "1", "env": {}}).status_code == 400
    assert sandbox.post("/run", content=b"x" * 100_001).status_code == 413
    assert sandbox.post("/nope", json={"code": "1"}).status_code == 404
