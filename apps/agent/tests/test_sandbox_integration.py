"""The real SandboxClient against a live sandbox actor, through the atenet router. Opt-in: skipped
unless SANDBOX_URL is set. See deploy/substrate/sandbox.md."""

import asyncio
import os
import ssl

import httpx2
import pytest
from pydantic import JsonValue

from agent.tools import RunCode, SandboxClient

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif("SANDBOX_URL" not in os.environ, reason="needs a live sandbox actor"),
]


class _RouterName(httpx2.AsyncHTTPTransport):
    """Check the router's certificate against its cluster name, also through a port-forward."""

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        request.extensions["sni_hostname"] = os.environ["SANDBOX_TLS_NAME"]
        return await super().handle_async_request(request)


def run(code: str) -> dict[str, JsonValue]:
    tls = ssl.create_default_context(cafile=os.environ["SANDBOX_CA"])
    tls.load_cert_chain(os.environ["SANDBOX_CERT"])  # an allowlisted client: cert and key
    headers = {"ate-target-actor": os.environ["SANDBOX_ACTOR"]}  # <atespace>/<actor>

    async def call() -> JsonValue:
        async with httpx2.AsyncClient(transport=_RouterName(verify=tls), timeout=60) as http:
            client = SandboxClient(os.environ["SANDBOX_URL"], headers, http)
            return await client.call("run_code", RunCode(code=code))

    result = asyncio.run(call())
    assert isinstance(result, dict)
    return result


def test_math_libraries():
    code = "import numpy, scipy.stats, sympy\nx = sympy.symbols('x')\n"
    code += "print(sympy.diff(x**3, x), numpy.linalg.det([[2, 1], [1, 3]]).round(6))"
    assert run(code)["stdout"] == "3*x**2 5.0\n"


def test_no_network():
    code = """\
import socket
for host in ("example.com", "kubernetes.default.svc.cluster.local"):
    try:
        socket.gethostbyname(host)
        print(host, "resolved")
    except socket.gaierror:
        print(host, "no dns")
for address in (("1.1.1.1", 80), ("10.96.0.1", 443)):
    try:
        with socket.create_connection(address, timeout=5) as s:
            s.sendall(b"GET / HTTP/1.0\\r\\n\\r\\n")
            print(address[0], "got", len(s.recv(100)), "bytes")
    except OSError as e:
        print(address[0], "refused", type(e).__name__)
"""
    lines = str(run(code)["stdout"]).splitlines()
    assert lines[:2] == ["example.com no dns", "kubernetes.default.svc.cluster.local no dns"]
    for line in lines[2:]:  # atunnel accepts and closes, or resets: no byte ever comes back
        assert line.endswith("got 0 bytes") or " refused " in line, line


def test_limits_and_the_sandbox_survives_them():
    assert run("while True: pass")["timed_out"] is True
    assert run("while True: print('x' * 100)")["output_truncated"] is True
    assert "MemoryError" in str(run("x = bytearray(1 << 30)")["stderr"])
    fork_bomb = (
        "import os\nwhile True:\n    if os.fork() == 0:\n        import time; time.sleep(60)"
    )
    assert "BlockingIOError" in str(run(fork_bomb)["stderr"])
    # Its zombies were reaped, so the next run may fork again.
    assert (
        run("import os\nif os.fork() == 0:\n    os._exit(0)\nprint('forked')")["stdout"]
        == "forked\n"
    )
    assert run("print('still here')")["stdout"] == "still here\n"


def test_the_script_runs_as_nobody_and_cannot_touch_the_server():
    code = "import os\nprint(os.getuid())\nos.kill(1, 9)"
    result = run(code)
    assert result["stdout"] == "65534\n"
    assert "PermissionError" in str(result["stderr"])
    assert run("print(open('/proc/1/environ').read())")["exit_code"] == 1
