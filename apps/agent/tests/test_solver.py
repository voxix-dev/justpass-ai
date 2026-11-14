import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterator
from typing import cast
from uuid import uuid4

import httpx2
import pytest
from pydantic import SecretStr
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent import server, solver
from agent.context import ContextClient
from agent.server import SAMPLE_ANSWER, app
from shared.agent_run import AgentRun

pytestmark = pytest.mark.anyio

KEY = "sk-or-secret-key"
Script = Callable[[list[ModelMessage], AgentInfo], ModelResponse]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def fresh_memory() -> Iterator[None]:
    server.runs.clear()
    yield
    server.runs.clear()


@pytest.fixture
async def client() -> AsyncIterator[httpx2.AsyncClient]:
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://agent") as client:
        yield client


@pytest.fixture
def use_model(monkeypatch: pytest.MonkeyPatch) -> Callable[[Script], list[list[ModelMessage]]]:
    """Replace the model with a script. Returns the messages each request carried."""

    def install(script: Script) -> list[list[ModelMessage]]:
        requests: list[list[ModelMessage]] = []

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            requests.append(list(messages))
            return script(messages, info)

        model = FunctionModel(respond)

        def make_model(key: SecretStr) -> Model:
            return model

        monkeypatch.setattr(solver, "make_model", make_model)
        return requests

    return install


@pytest.fixture
def context_api(monkeypatch: pytest.MonkeyPatch) -> list[httpx2.Request]:
    """A Context API that fails the test if it is called."""
    seen: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        raise AssertionError("the Context API was called")

    transport = httpx2.MockTransport(handle)
    real = ContextClient

    def client_with(url: str, token: str) -> ContextClient:
        return real(url, token, transport)

    monkeypatch.setattr(server, "ContextClient", client_with)
    return seen


def run_body(text: str = "Solve x^2 = 4", **extra: object) -> dict[str, object]:
    return {"job_id": str(uuid4()), "round": 1, "text": text, "openrouter_key": KEY, **extra}


def grant() -> dict[str, str]:
    return {"url": "http://api:8000", "token": "tok"}


def tool_call(name: str, **args: object) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name=name, args=args)])


def text(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])


def tool_returns(messages: list[ModelMessage]) -> dict[str, str]:
    return {
        p.tool_name: str(p.content)
        for m in messages
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart)
    }


def prompts(messages: list[ModelMessage]) -> list[str]:
    return [
        str(p.content)
        for m in messages
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, UserPromptPart)
    ]


async def test_the_answer_is_the_text_of_the_model(
    client: httpx2.AsyncClient, use_model: Callable[[Script], list[list[ModelMessage]]]
):
    requests = use_model(lambda messages, info: text("The roots are $x=\\pm 2$."))
    response = await client.post("/run", json=run_body())
    assert response.json() == {"answer": "The roots are $x=\\pm 2$."}
    first = requests[0]
    assert "Solve x^2 = 4" in prompts(first)[0]
    assert KEY not in repr(first)


async def test_the_model_can_call_a_math_tool(
    client: httpx2.AsyncClient, use_model: Callable[[Script], list[list[ModelMessage]]]
):
    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not tool_returns(messages):
            return tool_call("solve", equation="x**2 - 4", variable="x")
        return text("Done")

    requests = use_model(script)
    response = await client.post("/run", json=run_body())
    assert response.json() == {"answer": "Done"}
    result = tool_returns(requests[-1])["solve"]
    assert "[-2, 2]" in result
    assert "error=None" in result


async def test_all_tools_are_offered(
    client: httpx2.AsyncClient, use_model: Callable[[Script], list[list[ModelMessage]]]
):
    offered: list[str] = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        offered.extend(t.name for t in info.function_tools)
        return text("ok")

    use_model(script)
    await client.post("/run", json=run_body())
    assert set(offered) == {"evaluate", "simplify", "solve", "differentiate", "integrate"}


async def test_the_context_api_is_not_used_even_with_a_context(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
):
    requests = use_model(lambda messages, info: text("Done"))
    response = await client.post("/run", json=run_body(context=grant()))
    assert response.json() == {"answer": "Done"}
    assert prompts(requests[0]) == ["Solve x^2 = 4"]
    assert context_api == []


@pytest.mark.parametrize(
    "extra", [{"round": 2}, {"feedback": "More"}, {"round": 2, "feedback": "More"}]
)
async def test_a_feedback_round_returns_the_sample_without_the_model(
    client: httpx2.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    context_api: list[httpx2.Request],
    extra: dict[str, object],
):
    def never(key: SecretStr) -> Model:
        raise AssertionError("the model was built")

    monkeypatch.setattr(solver, "make_model", never)
    response = await client.post("/run", json={**run_body(context=grant()), **extra})
    answer = response.json()["answer"]
    assert answer.startswith(SAMPLE_ANSWER)
    assert ("More" in answer) == bool(extra.get("feedback"))
    assert context_api == []


async def test_the_model_is_closed_after_the_round(monkeypatch: pytest.MonkeyPatch):
    class Closing(FunctionModel):
        exits = 0

        async def __aexit__(self, *args: object) -> None:
            Closing.exits += 1

    failing = False

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if failing:
            raise RuntimeError("model down")
        return text("ok")

    def make_model(key: SecretStr) -> Model:
        return Closing(script)

    monkeypatch.setattr(solver, "make_model", make_model)
    job = AgentRun(job_id=uuid4(), round=1, text="Solve", openrouter_key=SecretStr(KEY))
    await solver.solve_with_model(job)
    failing = True
    with pytest.raises(RuntimeError):
        await solver.solve_with_model(job)
    assert Closing.exits == 2


async def test_a_model_that_loops_on_its_tools_is_stopped(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(solver, "MAX_MODEL_REQUESTS", 3)
    use_model(lambda messages, info: tool_call("evaluate", expression="1+1"))
    with pytest.raises(Exception, match="request_limit"):
        await client.post("/run", json=run_body())


async def test_a_round_that_overruns_its_time_is_stopped(monkeypatch: pytest.MonkeyPatch):
    async def slow(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await asyncio.sleep(5)
        return text("late")

    def make_model(key: SecretStr) -> Model:
        return FunctionModel(slow)

    monkeypatch.setattr(solver, "make_model", make_model)
    monkeypatch.setattr(solver, "ROUND_TIMEOUT_SECONDS", 0.05)
    job = AgentRun(job_id=uuid4(), round=1, text="Solve", openrouter_key=SecretStr(KEY))
    with pytest.raises(TimeoutError):
        await solver.solve_with_model(job)


async def test_the_model_settings_bound_reasoning_tokens_and_time():
    model = solver.make_model(SecretStr(KEY))
    settings = cast(dict[str, object], model.settings)
    assert settings.get("openrouter_reasoning") == solver.REASONING
    assert settings.get("max_tokens") == solver.MAX_OUTPUT_TOKENS
    assert settings.get("timeout") == solver.MODEL_TIMEOUT_SECONDS
    assert solver.MODEL_TIMEOUT_SECONDS < solver.ROUND_TIMEOUT_SECONDS


async def test_an_empty_reply_is_no_answer(
    client: httpx2.AsyncClient, use_model: Callable[[Script], list[list[ModelMessage]]]
):
    use_model(lambda messages, info: text("  \n"))
    assert (await client.post("/run", json=run_body())).json() == {"answer": None}


async def test_without_a_key_the_fake_answers(
    client: httpx2.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(server, "SOLVE_SECONDS", 0)

    def never(key: SecretStr) -> Model:
        raise AssertionError("the model was built")

    monkeypatch.setattr(solver, "make_model", never)
    job = {"job_id": str(uuid4()), "round": 1, "text": "Solve"}
    assert (await client.post("/run", json=job)).json() == {"answer": SAMPLE_ANSWER}
    assert (await client.post("/run", json={**job, "openrouter_key": None})).json() == {
        "answer": SAMPLE_ANSWER
    }


async def test_a_bad_request_does_not_echo_the_key(client: httpx2.AsyncClient):
    response = await client.post("/run", json=run_body(extra=1))
    assert response.status_code == 422
    assert KEY not in response.text


async def test_the_request_to_openrouter_asks_for_the_cheapest_provider(
    caplog: pytest.LogCaptureFixture,
):
    sent: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        completion = {
            "id": "gen-1",
            "object": "chat.completion",
            "created": 1,
            "model": solver.MODEL_ID,
            "provider": "SomeHost",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "Hello"},
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }
        return httpx2.Response(200, json=completion)

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
    model = solver.make_model(SecretStr(KEY), http)
    caplog.set_level("INFO", logger="agent.solver")
    result = await solver.agent.run("Hi", model=model)
    assert result.output == "Hello"

    (request,) = sent
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    payload = json.loads(request.content)
    assert payload["model"] == solver.MODEL_ID == "deepseek/deepseek-v4.1-flash"
    assert payload["provider"] == {"sort": "price"}
    assert payload["reasoning"] == solver.REASONING == {"max_tokens": 4_000}
    assert payload["max_tokens"] == solver.MAX_OUTPUT_TOKENS
    assert solver.REASONING.get("max_tokens", 0) < solver.MAX_OUTPUT_TOKENS
    assert {t["function"]["name"] for t in payload["tools"]} >= {"evaluate"}
    assert "write_document" not in {t["function"]["name"] for t in payload["tools"]}


async def test_the_log_names_tools_and_tokens_but_not_the_key(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    caplog: pytest.LogCaptureFixture,
):
    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not tool_returns(messages):
            return tool_call("evaluate", expression="2+3")
        return text("5")

    use_model(script)
    caplog.set_level("INFO", logger="agent.solver")
    await client.post("/run", json=run_body())
    line = next(r.getMessage() for r in caplog.records if r.name == "agent.solver")
    assert "['evaluate']" in line
    assert "tokens in" in line
    assert KEY not in caplog.text
