import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterator
from typing import cast
from uuid import uuid4

import anyio
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
    solver.histories.clear()
    solver.newest_runs.clear()
    server.runs.clear()
    yield
    solver.histories.clear()
    solver.newest_runs.clear()
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
    seen: list[httpx2.Request] = []
    job_id = str(uuid4())

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        tool = request.url.path.rsplit("/", 1)[-1]
        match tool:
            case "get_task":
                rounds: list[dict[str, object]] = [
                    {"number": 1, "feedback": None, "outcome": "answered", "artifacts": []},
                    {
                        "number": 2,
                        "feedback": "Show the check",
                        "outcome": "running",
                        "artifacts": [],
                    },
                ]
                task: dict[str, object] = {
                    "job_id": job_id,
                    "subject": "math",
                    "level": "high_school",
                    "language": "pl",
                    "text": "Solve x^2 = 4",
                    "round": 2,
                    "rounds": rounds,
                }
                return httpx2.Response(200, json=task)
            case "get_profile":
                profile = {"level": "high_school", "language": "pl", "subject": "math"}
                return httpx2.Response(200, json=profile | {"profile_md": "Likes short steps"})
            case _:
                return httpx2.Response(200, json={"artifact_id": str(uuid4())})

    transport = httpx2.MockTransport(handle)
    real = ContextClient

    def client_with(url: str, token: str) -> ContextClient:
        return real(url, token, transport)

    monkeypatch.setattr(solver, "ContextClient", client_with)
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
    assert set(offered) == {
        "evaluate",
        "simplify",
        "solve",
        "differentiate",
        "integrate",
        "write_document",
    }


async def test_write_document_saves_a_draft_through_the_context_api(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
):
    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not tool_returns(messages):
            return tool_call("write_document", title="Study sheet", content_md="# Roots\n$x^2$")
        return text("See the sheet.")

    requests = use_model(script)
    response = await client.post("/run", json=run_body(round=2, context=grant()))
    assert response.json() == {"answer": "See the sheet."}

    assert [r.url.path for r in context_api] == [
        "/v1/context/get_task",
        "/v1/context/get_profile",
        "/v1/context/write_artifact",
    ]
    written = json.loads(context_api[-1].content)
    assert written == {"kind": "draft", "title": "Study sheet", "content_md": "# Roots\n$x^2$"}
    assert "Study sheet" in tool_returns(requests[-1])["write_document"]
    # Task, profile and feedback of the Context API are in the prompt.
    prompt = prompts(requests[0])[0]
    assert "Solve x^2 = 4" in prompt
    assert "Likes short steps" in prompt
    assert "'pl'" in prompt
    assert "```\nShow the check\n```" in prompt


async def test_write_document_without_a_context_api_says_so(
    client: httpx2.AsyncClient, use_model: Callable[[Script], list[list[ModelMessage]]]
):
    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not tool_returns(messages):
            return tool_call("write_document", title="Sheet", content_md="text")
        return text("No document, here is the answer.")

    requests = use_model(script)
    response = await client.post("/run", json=run_body())
    assert response.json() == {"answer": "No document, here is the answer."}
    returned = tool_returns(requests[-1])["write_document"]
    assert "saved=False" in returned
    assert solver.DOCUMENTS_UNAVAILABLE in returned


async def test_a_refused_document_is_retried_by_the_model(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
):
    calls = 0

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return tool_call("write_document", title="T" * 300, content_md="x")
        if calls == 2:
            return tool_call("write_document", title="Short", content_md="x")
        return text("Fixed")

    use_model(script)
    response = await client.post("/run", json=run_body(round=2, context=grant()))
    assert response.json() == {"answer": "Fixed"}
    assert [r.url.path for r in context_api].count("/v1/context/write_artifact") == 1


async def test_a_feedback_round_continues_the_conversation(
    client: httpx2.AsyncClient, use_model: Callable[[Script], list[list[ModelMessage]]]
):
    requests = use_model(lambda messages, info: text(f"Answer {len(prompts(messages))}"))
    job = run_body()
    first = await client.post("/run", json=job)
    second = await client.post("/run", json={**job, "round": 2, "feedback": "Show the check"})
    assert first.json() == {"answer": "Answer 1"}
    assert second.json() == {"answer": "Answer 2"}
    # The second request carries the first prompt, the first answer and the feedback.
    sent = prompts(requests[1])
    assert len(sent) == 2
    assert "Solve x^2 = 4" in sent[0]
    assert "Show the check" in sent[1]
    assert any(
        isinstance(p, TextPart) and p.content == "Answer 1"
        for m in requests[1]
        if isinstance(m, ModelResponse)
        for p in m.parts
    )


async def test_a_fresh_process_rebuilds_the_job_from_the_task(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
):
    requests = use_model(lambda messages, info: text("Again"))
    # No history for this job: round 2 starts from the task, with the feedback of the rounds.
    await client.post("/run", json=run_body(round=2, feedback="Show the check", context=grant()))
    sent = prompts(requests[0])
    assert len(sent) == 1
    assert "Solve x^2 = 4" in sent[0]
    assert "```\nShow the check\n```" in sent[0]


async def test_a_round_that_runs_again_starts_from_scratch(
    client: httpx2.AsyncClient, use_model: Callable[[Script], list[list[ModelMessage]]]
):
    requests = use_model(lambda messages, info: text("ok"))
    job = {**run_body(), "feedback": "More", "round": 2}
    await client.post("/run", json={**job, "lease_id": str(uuid4())})
    await client.post("/run", json={**job, "lease_id": str(uuid4())})
    assert len(prompts(requests[1])) == 1


async def test_an_older_lease_finishing_late_keeps_the_newer_conversation(
    monkeypatch: pytest.MonkeyPatch,
):
    old_started, old_may_finish = anyio.Event(), anyio.Event()
    calls = 0

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:  # the old lease waits until the new one is done
            old_started.set()
            await old_may_finish.wait()
            return text("old")
        return text("new" if calls == 2 else f"next {len(prompts(messages))}")

    def make_model(key: SecretStr) -> Model:
        return FunctionModel(respond)

    monkeypatch.setattr(solver, "make_model", make_model)
    job = AgentRun(job_id=uuid4(), round=1, text="Solve", openrouter_key=SecretStr(KEY))
    old = job.model_copy(update={"lease_id": uuid4()})
    new = job.model_copy(update={"lease_id": uuid4()})
    answers: list[str | None] = []

    async def run(body: AgentRun) -> None:
        answers.append((await solver.solve_with_model(body)).answer)

    async with anyio.create_task_group() as group:
        group.start_soon(run, old)
        await old_started.wait()
        await run(new)
        old_may_finish.set()
    assert answers == ["new", "old"]

    # The feedback round continues the newer conversation.
    feedback = new.model_copy(update={"round": 2, "feedback": "More"})
    assert (await solver.solve_with_model(feedback)).answer == "next 2"
    kept = [
        p.content
        for m in solver.histories[job.job_id][1]
        if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, TextPart)
    ]
    assert kept == ["new", "next 2"]


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
    result = await solver.agent.run("Hi", model=model, deps=solver.Deps(None))
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
    assert {t["function"]["name"] for t in payload["tools"]} >= {"evaluate", "write_document"}


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
