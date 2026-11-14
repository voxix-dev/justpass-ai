import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import grpc
import httpx2
import pytest
from pydantic import SecretStr, ValidationError

from api import ate_pb2
from api.agents import Control, SingleAgent, SubstrateAgents, agents_from_env, egress_rules
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
    replies: dict[str, bytes] = field(default_factory=dict[str, bytes])
    # Every ate-api call and every router /run, in the order they arrived.
    order: list[str] = field(default_factory=list[str])

    def handler(self, method: str):
        async def handle(request: bytes, context: grpc.aio.ServicerContext) -> bytes:
            token = dict(context.invocation_metadata() or [])["authorization"]
            self.calls.append((method, request, str(token)))
            self.order.append(method)
            if method in self.errors:
                await context.abort(self.errors[method], "refused")
            return self.replies.get(method, b"")

        return grpc.unary_unary_rpc_method_handler(handle)


@pytest.fixture
async def ate_api() -> AsyncIterator[FakeAteApi]:
    fake = FakeAteApi()
    server = grpc.aio.server()
    methods = {
        m: fake.handler(m)
        for m in (
            "CreateActor",
            "CreateActorEgressPolicy",
            "GetActorEgressPolicy",
            "UpdateActorEgressPolicy",
            "SuspendActor",
            "DeleteActor",
        )
    }
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
        ate_api.order.append("router /run")
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

    [(method, request, token), _policy] = ate_api.calls
    assert (method, token) == ("CreateActor", "Bearer the-token")
    actor = ate_pb2.CreateActorRequest.FromString(request).actor
    assert (actor.metadata.atespace, actor.metadata.name) == ("homework", ACTOR)
    assert (actor.actor_template.atespace, actor.actor_template.name) == ("homework", "agent-v1")


CONTEXT_HTTP = "http://context-api.justpass-agents.svc.cluster.local:8000"


async def test_solve_allows_the_actor_only_the_context_api(
    substrate: SubstrateAgents, ate_api: FakeAteApi
):
    substrate.egress = egress_rules(CONTEXT_HTTP)
    assert await substrate.solve(RUN) == AgentAnswer(answer="x = 2")

    assert [method for method, _, _ in ate_api.calls] == ["CreateActor", "CreateActorEgressPolicy"]
    request = ate_pb2.CreateActorEgressPolicyRequest.FromString(ate_api.calls[1][1])
    assert (request.actor.atespace, request.actor.name) == ("homework", ACTOR)
    policy = request.egress_policy
    assert (policy.metadata.atespace, policy.metadata.name) == ("homework", "default")
    [rule] = policy.rules
    assert not rule.HasField("tls_passthrough")
    assert list(rule.http.hostnames) == ["context-api.justpass-agents.svc.cluster.local"]
    assert list(rule.http.ports.numbers) == [8000]
    assert not rule.http.ports.HasField("all")


async def test_solve_creates_the_policy_before_the_actor_is_woken(
    substrate: SubstrateAgents, ate_api: FakeAteApi
):
    """The router's first request wakes the actor, which then must already have its policy."""
    substrate.egress = egress_rules(CONTEXT_HTTP)
    await substrate.solve(RUN)
    assert ate_api.order == ["CreateActor", "CreateActorEgressPolicy", "router /run"]


async def test_solve_without_rules_creates_an_empty_policy(
    substrate: SubstrateAgents, ate_api: FakeAteApi
):
    substrate.egress = egress_rules("")
    await substrate.solve(RUN)
    assert [method for method, _, _ in ate_api.calls] == ["CreateActor", "CreateActorEgressPolicy"]
    request = ate_pb2.CreateActorEgressPolicyRequest.FromString(ate_api.calls[1][1])
    assert list(request.egress_policy.rules) == []


async def test_solve_empties_the_policy_of_an_actor_when_no_rules_are_wanted(
    substrate: SubstrateAgents, ate_api: FakeAteApi
):
    """The key is gone and there is no Context API: the old openrouter.ai rule must not stay."""
    old = ate_pb2.EgressPolicy(
        metadata=ate_pb2.ResourceMetadata(
            atespace="homework", name="default", uid="the-uid", version=3
        ),
        rules=egress_rules("", openrouter=True),
    )
    substrate.egress = []
    ate_api.errors["CreateActorEgressPolicy"] = grpc.StatusCode.ALREADY_EXISTS
    ate_api.replies["GetActorEgressPolicy"] = old.SerializeToString()
    await substrate.solve(RUN)

    update = next(r for m, r, _ in ate_api.calls if m == "UpdateActorEgressPolicy")
    request = ate_pb2.UpdateActorEgressPolicyRequest.FromString(update)
    assert list(request.egress_policy.rules) == []
    assert request.egress_policy.metadata.uid == "the-uid"


async def test_solve_creates_the_policy_of_an_actor_that_exists(
    substrate: SubstrateAgents, ate_api: FakeAteApi
):
    """A retry after a create that stopped half way finds the actor, and still needs its policy."""
    substrate.egress = egress_rules(CONTEXT_HTTP)
    ate_api.errors["CreateActor"] = grpc.StatusCode.ALREADY_EXISTS
    await substrate.solve(RUN)
    assert [method for method, _, _ in ate_api.calls] == ["CreateActor", "CreateActorEgressPolicy"]


async def test_solve_ignores_a_policy_that_exists_and_raises_on_other_errors(
    substrate: SubstrateAgents, ate_api: FakeAteApi
):
    substrate.egress = egress_rules(CONTEXT_HTTP)
    ate_api.errors["CreateActorEgressPolicy"] = grpc.StatusCode.ALREADY_EXISTS
    ate_api.replies["GetActorEgressPolicy"] = ate_pb2.EgressPolicy(
        rules=substrate.egress
    ).SerializeToString()
    assert await substrate.solve(RUN) == AgentAnswer(answer="x = 2")
    assert "UpdateActorEgressPolicy" not in ate_api.order  # the rules are the wanted ones

    ate_api.order.clear()
    ate_api.errors["CreateActorEgressPolicy"] = grpc.StatusCode.PERMISSION_DENIED
    with pytest.raises(grpc.aio.AioRpcError):
        await substrate.solve(RUN)
    assert ate_api.order == ["CreateActor", "CreateActorEgressPolicy"]  # the router was not called


async def test_solve_replaces_the_rules_of_a_policy_made_for_another_context_api(
    substrate: SubstrateAgents, ate_api: FakeAteApi
):
    """CONTEXT_API_URL changed since the actor was made: its old rules must not outlive that."""
    substrate.egress = egress_rules(CONTEXT_HTTP)
    old = ate_pb2.EgressPolicy(
        metadata=ate_pb2.ResourceMetadata(
            atespace="homework", name="default", uid="the-uid", version=3
        ),
        rules=egress_rules("http://old-api:8000"),
    )
    ate_api.errors["CreateActorEgressPolicy"] = grpc.StatusCode.ALREADY_EXISTS
    ate_api.replies["GetActorEgressPolicy"] = old.SerializeToString()
    await substrate.solve(RUN)

    assert ate_api.order == [
        "CreateActor",
        "CreateActorEgressPolicy",
        "GetActorEgressPolicy",
        "UpdateActorEgressPolicy",
        "router /run",
    ]
    update = next(r for m, r, _ in ate_api.calls if m == "UpdateActorEgressPolicy")
    request = ate_pb2.UpdateActorEgressPolicyRequest.FromString(update)
    assert (request.actor.atespace, request.actor.name) == ("homework", ACTOR)
    assert (request.egress_policy.metadata.uid, request.egress_policy.metadata.version) == (
        "the-uid",
        3,
    )
    assert list(request.egress_policy.rules) == substrate.egress


async def test_solve_adds_the_openrouter_rule_to_a_policy_made_without_it(
    substrate: SubstrateAgents, ate_api: FakeAteApi
):
    """The key was set after the actor was made: its policy must gain the rule."""
    old = ate_pb2.EgressPolicy(
        metadata=ate_pb2.ResourceMetadata(
            atespace="homework", name="default", uid="the-uid", version=3
        ),
        rules=egress_rules(CONTEXT_HTTP),
    )
    substrate.egress = egress_rules(CONTEXT_HTTP, openrouter=True)
    ate_api.errors["CreateActorEgressPolicy"] = grpc.StatusCode.ALREADY_EXISTS
    ate_api.replies["GetActorEgressPolicy"] = old.SerializeToString()
    await substrate.solve(RUN)

    update = next(r for m, r, _ in ate_api.calls if m == "UpdateActorEgressPolicy")
    rules = ate_pb2.UpdateActorEgressPolicyRequest.FromString(update).egress_policy.rules
    assert [rule.HasField("tls_passthrough") for rule in rules] == [False, True]


async def test_solve_removes_the_openrouter_rule_when_the_key_is_gone(
    substrate: SubstrateAgents, ate_api: FakeAteApi
):
    old = ate_pb2.EgressPolicy(
        metadata=ate_pb2.ResourceMetadata(
            atespace="homework", name="default", uid="the-uid", version=3
        ),
        rules=egress_rules(CONTEXT_HTTP, openrouter=True),
    )
    substrate.egress = egress_rules(CONTEXT_HTTP)
    ate_api.errors["CreateActorEgressPolicy"] = grpc.StatusCode.ALREADY_EXISTS
    ate_api.replies["GetActorEgressPolicy"] = old.SerializeToString()
    await substrate.solve(RUN)

    update = next(r for m, r, _ in ate_api.calls if m == "UpdateActorEgressPolicy")
    rules = ate_pb2.UpdateActorEgressPolicyRequest.FromString(update).egress_policy.rules
    assert [rule.HasField("tls_passthrough") for rule in rules] == [False]


async def test_solve_leaves_an_up_to_date_policy_with_openrouter_alone(
    substrate: SubstrateAgents, ate_api: FakeAteApi
):
    rules = egress_rules(CONTEXT_HTTP, openrouter=True)
    old = ate_pb2.EgressPolicy(
        metadata=ate_pb2.ResourceMetadata(atespace="homework", name="default"), rules=rules
    )
    substrate.egress = rules
    ate_api.errors["CreateActorEgressPolicy"] = grpc.StatusCode.ALREADY_EXISTS
    ate_api.replies["GetActorEgressPolicy"] = old.SerializeToString()
    await substrate.solve(RUN)
    assert "UpdateActorEgressPolicy" not in ate_api.order


def test_egress_rules_for_https_pass_the_tls_through():
    [rule] = egress_rules("https://context.example.com")
    assert not rule.HasField("http")
    assert list(rule.tls_passthrough.hostnames) == ["context.example.com"]
    assert list(rule.tls_passthrough.ports.numbers) == [443]


KEY = "sk-or-dummy-key-for-tests"


def test_egress_rules_add_openrouter_with_a_key():
    http, openrouter = egress_rules(CONTEXT_HTTP, openrouter=True)
    assert list(http.http.hostnames) == ["context-api.justpass-agents.svc.cluster.local"]
    assert not openrouter.HasField("http")
    assert list(openrouter.tls_passthrough.hostnames) == ["openrouter.ai"]
    assert list(openrouter.tls_passthrough.ports.numbers) == [443]

    https, openrouter = egress_rules("https://context.example.com", openrouter=True)
    assert list(https.tls_passthrough.hostnames) == ["context.example.com"]
    assert list(openrouter.tls_passthrough.hostnames) == ["openrouter.ai"]


def test_egress_rules_without_a_key_have_no_openrouter():
    assert egress_rules(CONTEXT_HTTP) == egress_rules(CONTEXT_HTTP, openrouter=False)
    assert len(egress_rules(CONTEXT_HTTP)) == 1
    assert egress_rules("") == []


def test_egress_rules_allow_openrouter_without_a_context_api():
    [rule] = egress_rules("", openrouter=True)
    assert list(rule.tls_passthrough.hostnames) == ["openrouter.ai"]


def test_egress_rules_default_ports_and_bad_urls():
    assert list(egress_rules("http://api").pop().http.ports.numbers) == [80]
    assert list(egress_rules("https://api:8443").pop().tls_passthrough.ports.numbers) == [8443]
    for bad in ("ftp://api", "api:8000", "http://"):
        with pytest.raises(ValueError, match="CONTEXT_API_URL"):
            egress_rules(bad)


@pytest.mark.parametrize(
    "url",
    [
        "http://10.89.5.4:8000",
        "https://[::1]:8443",
        "http://context-api.justpass-agents.svc.cluster.local.:8000",
        "http://under_score:8000",
        "http://-api:8000",
    ],
)
def test_egress_rules_refuse_the_hosts_substrate_refuses(url: str):
    with pytest.raises(ValueError, match="DNS name"):
        egress_rules(url)


def test_agents_from_env_refuses_an_ip_address_as_the_context_api(
    monkeypatch: pytest.MonkeyPatch,
):
    """Startup fails, rather than every job failing later with INVALID_ARGUMENT."""
    monkeypatch.delenv("AGENT_URL", raising=False)
    for name, value in {
        "ATE_ROUTER_URL": "https://router",
        "ATE_API_TARGET": "api:443",
        "ATE_CA_FILE": "/ca",
        "ATE_CLIENT_CERT_FILE": "/cert",
        "ATE_TOKEN_FILE": "/token",
        "ATE_ATESPACE": "homework",
        "ATE_TEMPLATE": "agent-v1",
        "CONTEXT_API_URL": "http://10.89.5.4:8000",
    }.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="CONTEXT_API_URL"):
        agents_from_env()


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


async def test_single_agent_sends_the_key_in_the_body():
    seen: list[dict] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(200, json={"answer": None})

    agent = SingleAgent("http://agent", httpx2.MockTransport(answer))
    await agent.solve(RUN.model_copy(update={"openrouter_key": SecretStr(KEY)}))
    await agent.solve(RUN)
    assert [body["openrouter_key"] for body in seen] == [KEY, None]


async def test_substrate_sends_the_key_in_the_body(substrate: SubstrateAgents):
    seen: list[dict] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(200, json={"answer": None})

    substrate.router = lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(answer))
    await substrate.solve(RUN.model_copy(update={"openrouter_key": SecretStr(KEY)}))
    assert seen[0]["openrouter_key"] == KEY


async def test_the_key_stays_out_of_reprs_logs_and_errors(caplog: pytest.LogCaptureFixture):
    run = RUN.model_copy(update={"openrouter_key": SecretStr(KEY)})
    transport = httpx2.MockTransport(lambda _: httpx2.Response(503))
    with caplog.at_level("DEBUG"), pytest.raises(httpx2.HTTPStatusError) as error:
        await SingleAgent("http://agent", transport).solve(run)
    for text in (
        repr(run),
        str(run),
        run.model_dump_json(),
        str(run.model_dump()),
        str(error.value),
        caplog.text,
    ):
        assert KEY not in text


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
    monkeypatch.setenv("CONTEXT_API_URL", CONTEXT_HTTP)
    agents = agents_from_env()
    assert isinstance(agents, SubstrateAgents)
    assert (agents.atespace, agents.template) == ("homework", "agent-v1")
    assert agents.egress == egress_rules(CONTEXT_HTTP)


def test_agents_from_env_allows_openrouter_with_a_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AGENT_URL", raising=False)
    for name, value in {
        "ATE_ROUTER_URL": "https://router",
        "ATE_API_TARGET": "api:443",
        "ATE_CA_FILE": "/ca",
        "ATE_CLIENT_CERT_FILE": "/cert",
        "ATE_TOKEN_FILE": "/token",
        "ATE_ATESPACE": "homework",
        "ATE_TEMPLATE": "agent-v1",
        "CONTEXT_API_URL": CONTEXT_HTTP,
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    agents = agents_from_env()
    assert isinstance(agents, SubstrateAgents)
    assert agents.egress == egress_rules(CONTEXT_HTTP, openrouter=True)
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    agents = agents_from_env()
    assert isinstance(agents, SubstrateAgents)
    assert agents.egress == egress_rules(CONTEXT_HTTP)


def test_agents_from_env_needs_one_or_the_other(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AGENT_URL", raising=False)
    monkeypatch.delenv("ATE_ROUTER_URL", raising=False)
    with pytest.raises(RuntimeError, match="AGENT_URL"):
        agents_from_env()
