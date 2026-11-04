import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import grpc
import httpx2
import pytest
from pydantic import ValidationError

from api import ate_pb2
from api.agents import Control, SingleAgent, SubstrateAgents, agents_from_env
from shared.agent_run import AgentAnswer, AgentRun

pytestmark = pytest.mark.anyio

JOB = uuid.UUID("00000000-0000-4000-8000-000000000001")
RUN = AgentRun(job_id=JOB, round=1, text="Solve")
ACTOR = f"job-{JOB}"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class FakeAteApi:
    """A real gRPC server answering like ate-api, so the bytes on the wire are what gets tested."""

    port: int = 0
    calls: list[tuple[str, bytes, str]] = field(default_factory=list[tuple[str, bytes, str]])
    errors: dict[str, grpc.StatusCode] = field(default_factory=dict[str, grpc.StatusCode])

    def handler(self, method: str):
        async def handle(request: bytes, context: grpc.aio.ServicerContext) -> bytes:
            token = dict(context.invocation_metadata() or [])["authorization"]
            self.calls.append((method, request, str(token)))
            if method in self.errors:
                await context.abort(self.errors[method], "refused")
            return b""

        return grpc.unary_unary_rpc_method_handler(handle)


@pytest.fixture
async def ate_api() -> AsyncIterator[FakeAteApi]:
    fake = FakeAteApi()
    server = grpc.aio.server()
    methods = {m: fake.handler(m) for m in ("CreateActor", "SuspendActor", "DeleteActor")}
    server.add_generic_rpc_handlers(
        [grpc.method_handlers_generic_handler("ateapi.Control", methods)]
    )
    fake.port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    yield fake
    await server.stop(None)


@pytest.fixture
def substrate(ate_api: FakeAteApi, tmp_path: Path) -> SubstrateAgents:
    token = tmp_path / "token"
    token.write_text("the-token\n")

    def answer(request: httpx2.Request) -> httpx2.Response:
        assert request.url == "https://router/run"
        assert request.headers["ate-target-actor"] == f"homework/{ACTOR}"
        assert AgentRun.model_validate_json(request.content) == RUN
        return httpx2.Response(200, json={"answer": "x = 2"})

    return SubstrateAgents(
        control=Control(
            lambda: grpc.aio.insecure_channel(f"127.0.0.1:{ate_api.port}"),
            token,
        ),
        router=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(answer)),
        router_url="https://router",
        atespace="homework",
        template="agent-v1",
    )


async def test_solve_creates_the_actor_and_asks_it(substrate: SubstrateAgents, ate_api: FakeAteApi):
    assert await substrate.solve(RUN) == AgentAnswer(answer="x = 2")

    [(method, request, token)] = ate_api.calls
    assert (method, token) == ("CreateActor", "Bearer the-token")
    actor = ate_pb2.CreateActorRequest.FromString(request).actor
    assert (actor.metadata.atespace, actor.metadata.name) == ("homework", ACTOR)
    assert (actor.actor_template.atespace, actor.actor_template.name) == ("homework", "agent-v1")


async def test_solve_accepts_heartbeat_newlines_before_the_answer(substrate: SubstrateAgents):
    substrate.router = lambda: httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda _: httpx2.Response(200, content=b'\n\n\n{"answer": "x = 2"}')
        )
    )
    assert await substrate.solve(RUN) == AgentAnswer(answer="x = 2")


async def test_solve_fails_when_the_stream_ends_without_an_answer(substrate: SubstrateAgents):
    substrate.router = lambda: httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, content=b"\n\n"))
    )
    with pytest.raises(ValidationError):
        await substrate.solve(RUN)


async def test_solve_waits_for_a_free_worker(
    substrate: SubstrateAgents, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("api.agents.BUSY_RETRY_DELAY", 0)
    answers = iter([503, 503, 200])

    def answer(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(next(answers), json={"answer": "x = 2"})

    substrate.router = lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(answer))
    assert await substrate.solve(RUN) == AgentAnswer(answer="x = 2")


async def test_solve_stops_waiting_at_the_deadline(substrate: SubstrateAgents):
    substrate.busy_wait = 0
    substrate.router = lambda: httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(503))
    )
    with pytest.raises(httpx2.HTTPStatusError):
        await substrate.solve(RUN)


async def test_solve_does_not_wait_on_other_errors(substrate: SubstrateAgents):
    substrate.router = lambda: httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(500))
    )
    with pytest.raises(httpx2.HTTPStatusError):
        await substrate.solve(RUN)


async def test_solve_reuses_the_actor_of_an_earlier_round(
    substrate: SubstrateAgents, ate_api: FakeAteApi
):
    ate_api.errors["CreateActor"] = grpc.StatusCode.ALREADY_EXISTS
    assert await substrate.solve(RUN) == AgentAnswer(answer="x = 2")


async def test_solve_raises_when_the_actor_cannot_be_created(
    substrate: SubstrateAgents, ate_api: FakeAteApi
):
    ate_api.errors["CreateActor"] = grpc.StatusCode.PERMISSION_DENIED
    with pytest.raises(grpc.aio.AioRpcError):
        await substrate.solve(RUN)


async def test_hibernate_suspends_the_actor(substrate: SubstrateAgents, ate_api: FakeAteApi):
    await substrate.hibernate(JOB)

    [(method, request, _)] = ate_api.calls
    assert method == "SuspendActor"
    actor = ate_pb2.SuspendActorRequest.FromString(request).actor
    assert (actor.atespace, actor.name) == ("homework", ACTOR)


async def test_release_deletes_the_actor(substrate: SubstrateAgents, ate_api: FakeAteApi):
    await substrate.release(JOB)

    [(method, request, _)] = ate_api.calls
    assert method == "DeleteActor"
    actor = ate_pb2.DeleteActorRequest.FromString(request).actor
    assert (actor.atespace, actor.name) == ("homework", ACTOR)


async def test_release_of_a_missing_actor_is_fine(substrate: SubstrateAgents, ate_api: FakeAteApi):
    ate_api.errors["DeleteActor"] = grpc.StatusCode.NOT_FOUND
    await substrate.release(JOB)


async def test_release_raises_on_other_errors(substrate: SubstrateAgents, ate_api: FakeAteApi):
    ate_api.errors["DeleteActor"] = grpc.StatusCode.UNAVAILABLE
    with pytest.raises(grpc.aio.AioRpcError):
        await substrate.release(JOB)


async def test_single_agent_posts_the_run():
    def answer(request: httpx2.Request) -> httpx2.Response:
        assert request.url == "http://agent/run"
        assert json.loads(request.content)["text"] == "Solve"
        return httpx2.Response(200, json={"answer": None})

    agent = SingleAgent("http://agent", httpx2.MockTransport(answer))
    assert await agent.solve(RUN) == AgentAnswer(answer=None)
    await agent.hibernate(JOB)
    await agent.release(JOB)


async def test_an_answer_with_nul_raises():
    """Postgres would refuse it when saving, which leaves the job running instead of failing it."""
    transport = httpx2.MockTransport(lambda _: httpx2.Response(200, json={"answer": "a\x00b"}))
    with pytest.raises(ValidationError):
        await SingleAgent("http://agent", transport).solve(RUN)


async def test_an_error_from_the_agent_raises():
    transport = httpx2.MockTransport(lambda _: httpx2.Response(503))
    with pytest.raises(httpx2.HTTPStatusError):
        await SingleAgent("http://agent", transport).solve(RUN)


def test_agents_from_env_prefers_the_single_agent(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_URL", "http://agent")
    monkeypatch.setenv("ATE_ROUTER_URL", "https://router")
    assert isinstance(agents_from_env(), SingleAgent)


def test_agents_from_env_reads_the_substrate_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AGENT_URL", raising=False)
    for name, value in {
        "ATE_ROUTER_URL": "https://router",
        "ATE_API_TARGET": "api:443",
        "ATE_CA_FILE": "/ca",
        "ATE_CLIENT_CERT_FILE": "/cert",
        "ATE_TOKEN_FILE": "/token",
        "ATE_ATESPACE": "homework",
        "ATE_TEMPLATE": "agent-v1",
    }.items():
        monkeypatch.setenv(name, value)
    agents = agents_from_env()
    assert isinstance(agents, SubstrateAgents)
    assert (agents.atespace, agents.template) == ("homework", "agent-v1")


def test_agents_from_env_needs_one_or_the_other(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AGENT_URL", raising=False)
    monkeypatch.delenv("ATE_ROUTER_URL", raising=False)
    with pytest.raises(RuntimeError, match="AGENT_URL"):
        agents_from_env()
