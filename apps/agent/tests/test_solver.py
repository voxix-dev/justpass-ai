import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from typing import cast
from uuid import UUID, uuid4

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
from agent.roles import general
from agent.server import SAMPLE_ANSWER, app
from shared.agent_run import AgentRun, ContextGrant
from shared.context import Profile

pytestmark = pytest.mark.anyio

KEY = "sk-or-secret-key"
Script = Callable[[list[ModelMessage], AgentInfo], ModelResponse]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def fresh_memory() -> Iterator[None]:
    solver.histories.clear()
    solver.job_subjects.clear()
    solver.newest_runs.clear()
    server.runs.clear()
    yield
    solver.histories.clear()
    solver.job_subjects.clear()
    solver.newest_runs.clear()
    server.runs.clear()


@pytest.fixture
async def client() -> AsyncIterator[httpx2.AsyncClient]:
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://agent") as client:
        yield client


@dataclass
class CuratorStub:
    """What the curator step gets from the fake model: its requests, and what it answers."""

    requests: list[list[ModelMessage]] = field(default_factory=lambda: [])
    curation: dict[str, object] = field(default_factory=lambda: {"profile": {}, "subject": {}})
    error: Exception | None = None
    delay: float = 0
    on_request: Callable[[], None] | None = None


@dataclass
class PickerStub:
    """What the subject step gets from the fake model: its requests, and what it answers."""

    requests: list[list[ModelMessage]] = field(default_factory=lambda: [])
    name: str = "math"
    error: Exception | None = None


def is_picker(info: AgentInfo) -> bool:
    return any("name" in t.parameters_json_schema.get("properties", {}) for t in info.output_tools)


def is_curator(info: AgentInfo) -> bool:
    return any(t.name == "final_result" for t in info.output_tools) and not is_picker(info)


def pick_reply(info: AgentInfo, name: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"name": name})])


def curation_reply(info: AgentInfo, curation: dict[str, object]) -> ModelResponse:
    return tool_call(info.output_tools[0].name, **curation)


@pytest.fixture
def curator() -> CuratorStub:
    return CuratorStub()


@pytest.fixture
def picker() -> PickerStub:
    return PickerStub()


@pytest.fixture
def use_model(
    monkeypatch: pytest.MonkeyPatch, curator: CuratorStub, picker: PickerStub
) -> Callable[[Script], list[list[ModelMessage]]]:
    """Replace the model with a script. Returns the messages each answering request carried.

    The curator's requests go to the stub instead, which returns no change unless a test sets one,
    and the requests that name the subject go to the picker stub, which names math.
    """

    def install(script: Script) -> list[list[ModelMessage]]:
        requests: list[list[ModelMessage]] = []

        async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if is_picker(info):
                picker.requests.append(list(messages))
                if picker.error:
                    raise picker.error
                return pick_reply(info, picker.name)
            if is_curator(info):
                curator.requests.append(list(messages))
                if curator.on_request:
                    curator.on_request()
                await asyncio.sleep(curator.delay)
                if curator.error:
                    raise curator.error
                return curation_reply(info, curator.curation)
            requests.append(list(messages))
            return script(messages, info)

        model = FunctionModel(respond)

        def make_model(key: SecretStr) -> Model:
            return model

        monkeypatch.setattr(solver, "make_model", make_model)
        return requests

    return install


@pytest.fixture
def task_rounds() -> list[dict[str, object]]:
    """The rounds the fake Context API reports; a test may change them between rounds."""
    return [
        {"number": 1, "feedback": None, "outcome": "answered", "artifacts": []},
        {"number": 2, "feedback": "Show the check", "outcome": "running", "artifacts": []},
    ]


@pytest.fixture
def notes() -> dict[str, str | int]:
    """The notes the fake Context API reports; a test may change them between rounds."""
    return {
        "profile_md": "Likes short steps",
        "profile_version": 3,
        "subject_md": "Writes in pencil",
        "subject_version": 5,
    }


@pytest.fixture
def context_api(
    monkeypatch: pytest.MonkeyPatch,
    task_rounds: list[dict[str, object]],
    notes: dict[str, str | int],
) -> list[httpx2.Request]:
    seen: list[httpx2.Request] = []
    job_id = str(uuid4())

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        tool = request.url.path.rsplit("/", 1)[-1]
        match tool:
            case "get_task":
                task: dict[str, object] = {
                    "job_id": job_id,
                    "subject": "math",
                    "level": "high_school",
                    "language": "pl",
                    "text": "Solve x^2 = 4",
                    "round": task_rounds[-1]["number"],
                    "rounds": task_rounds,
                }
                return httpx2.Response(200, json=task)
            case "get_profile":
                profile = {
                    "level": "high_school",
                    "language": "pl",
                    "subject": "math",
                    "subjects": ["physics", "math"],
                }
                return httpx2.Response(
                    200,
                    json=profile
                    | {
                        "profile_md": notes["profile_md"],
                        "profile_version": notes["profile_version"],
                    },
                )
            case "get_subject_memory":
                memory = {
                    "subject": json.loads(request.content)["subject"] or "math",
                    "content_md": notes["subject_md"],
                    "version": notes["subject_version"],
                }
                return httpx2.Response(200, json=memory)
            case "update_memory":
                body = json.loads(request.content)
                scope = body["scope"]
                if body["base_version"] != notes[f"{scope}_version"]:
                    return httpx2.Response(412, json={"detail": "The notes changed"})
                notes[f"{scope}_md"] = body["content_md"]
                notes[f"{scope}_version"] = body["base_version"] + 1
                saved = {
                    "subject": body["subject"],
                    "content_md": body["content_md"],
                    "version": notes[f"{scope}_version"],
                }
                return httpx2.Response(200, json=saved)
            case "search_history":
                return httpx2.Response(
                    200, json=[{"subject": "physics", "content_md": "Forgets units", "version": 1}]
                )
            case "list_history":
                item = {
                    "job_id": str(uuid4()),
                    "subject": "physics",
                    "status": "accepted",
                    "created_at": "2026-01-01T00:00:00Z",
                    "grade": "5",
                    "grade_comment": None,
                }
                return httpx2.Response(200, json=[item])
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
        "read_memory",
        "search_memory",
        "list_history",
    }


async def test_write_document_has_a_flat_schema_with_the_limits(
    client: httpx2.AsyncClient, use_model: Callable[[Script], list[list[ModelMessage]]]
):
    schemas: dict[str, dict[str, object]] = {}

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        schemas.update({t.name: t.parameters_json_schema for t in info.function_tools})
        return text("ok")

    use_model(script)
    await client.post("/run", json=run_body())
    properties = cast(dict[str, dict[str, object]], schemas["write_document"]["properties"])
    assert set(properties) == {"title", "content_md"}
    assert properties["title"]["maxLength"] == 200
    assert properties["content_md"]["maxLength"] == 100_000


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
        "/v1/context/get_subject_memory",
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
    assert general.DOCUMENTS_UNAVAILABLE in returned


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
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level("INFO", logger="agent.solver")
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
    assert f"Round 2 of job {job['job_id']} continues round 1" in caplog.text
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


def round_row(number: int, outcome: str, feedback: str | None) -> dict[str, object]:
    return {"number": number, "feedback": feedback, "outcome": outcome, "artifacts": []}


async def test_a_round_that_failed_on_the_api_is_not_continued(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
    task_rounds: list[dict[str, object]],
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level("INFO", logger="agent.solver")
    requests = use_model(lambda messages, info: text("ok"))
    job = run_body(context=grant())
    await client.post("/run", json=job)
    await client.post("/run", json={**job, "round": 2, "feedback": "Show the check"})
    # Round 2 finished here, but the API marked it failed and the student sent round 3.
    task_rounds[:] = [
        round_row(1, "answered", None),
        round_row(2, "failed", "Show the check"),
        round_row(3, "running", "Third change"),
    ]
    await client.post("/run", json={**job, "round": 3, "feedback": "Third change"})
    assert f"Round 3 of job {job['job_id']} continues round 1" in caplog.text
    assert not any("Show the check" in p for p in prompts(requests[2]))
    assert any("Third change" in p for p in prompts(requests[2]))


async def test_a_round_without_a_checkpoint_rebuilds_with_the_feedback_of_the_answered(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
    task_rounds: list[dict[str, object]],
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level("INFO", logger="agent.solver")
    requests = use_model(lambda messages, info: text("ok"))
    job = run_body(context=grant())
    job_id = UUID(str(job["job_id"]))
    await client.post("/run", json=job)
    # Round 2 ran while another lease owned the job, so it stored no checkpoint.
    solver.newest_runs[job_id] = (3, uuid4())
    await client.post("/run", json={**job, "round": 2, "feedback": "Show the check"})
    assert sorted(solver.histories[job_id]) == [1]
    task_rounds[:] = [
        round_row(1, "answered", None),
        round_row(2, "answered", "Show the check"),
        round_row(3, "running", "Third change"),
    ]
    await client.post("/run", json={**job, "round": 3, "feedback": "Third change"})
    assert f"Round 3 of job {job_id} starts from the task and 2 feedback" in caplog.text
    assert len(prompts(requests[2])) == 1
    assert "Show the check" in prompts(requests[2])[0]
    assert "Third change" in prompts(requests[2])[0]
    assert solver.histories[job_id][3].rounds == (1, 2, 3)


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
        for m in solver.histories[job.job_id][2].messages
        if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, TextPart)
    ]
    assert kept == ["new", "next 2"]


async def test_an_older_lease_delayed_before_its_run_does_not_take_over(
    monkeypatch: pytest.MonkeyPatch, context_api: list[httpx2.Request]
):
    old_waiting, old_may_go = anyio.Event(), anyio.Event()
    real = ContextClient.get_profile
    delayed: list[ContextClient] = []

    async def get_profile(self: ContextClient) -> Profile:
        if not delayed:  # the first call, made by the old lease
            delayed.append(self)
            old_waiting.set()
            await old_may_go.wait()
        return await real(self)

    monkeypatch.setattr(ContextClient, "get_profile", get_profile)
    calls = 0

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        if is_picker(info):
            return pick_reply(info, "math")
        if is_curator(info):
            return curation_reply(info, {"profile": {}, "subject": {}})
        calls += 1
        return text("new" if calls == 1 else "old")  # the new lease answers first

    def make_model(key: SecretStr) -> Model:
        return FunctionModel(respond)

    monkeypatch.setattr(solver, "make_model", make_model)
    job = AgentRun(
        job_id=uuid4(),
        round=2,
        text="Solve",
        feedback="More",
        openrouter_key=SecretStr(KEY),
        context=ContextGrant(url="http://api:8000", token="tok"),
    )
    old = job.model_copy(update={"lease_id": uuid4()})
    new = job.model_copy(update={"lease_id": uuid4()})

    async with anyio.create_task_group() as group:
        group.start_soon(solver.solve_with_model, old)
        await old_waiting.wait()
        await solver.solve_with_model(new)
        old_may_go.set()
    # The old lease reached its answer last, but the new one owns the job.
    assert solver.newest_runs[job.job_id] == (2, new.lease_id)
    kept = [
        p.content
        for m in solver.histories[job.job_id][2].messages
        if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, TextPart)
    ]
    assert kept == ["new"]


async def test_a_round_that_runs_again_keeps_the_feedback_of_earlier_rounds(
    client: httpx2.AsyncClient, use_model: Callable[[Script], list[list[ModelMessage]]]
):
    requests = use_model(lambda messages, info: text(f"Answer {len(requests)}"))
    job = run_body()
    await client.post("/run", json=job)
    await client.post("/run", json={**job, "round": 2, "feedback": "Feedback A"})
    await client.post(
        "/run", json={**job, "round": 3, "feedback": "Feedback B", "lease_id": str(uuid4())}
    )
    await client.post(
        "/run", json={**job, "round": 3, "feedback": "Feedback B", "lease_id": str(uuid4())}
    )
    again = prompts(requests[3])
    assert any("Feedback A" in p for p in again)
    assert sum("Feedback B" in p for p in again) == 1
    sent = [
        p.content
        for m in requests[3]
        if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, TextPart)
    ]
    assert sent == ["Answer 1", "Answer 2"]  # not the answer of the first attempt at round 3
    assert sorted(solver.histories[UUID(str(job["job_id"]))]) == [1, 2, 3]


async def test_a_rerun_round_drops_the_checkpoints_after_it(
    client: httpx2.AsyncClient, use_model: Callable[[Script], list[list[ModelMessage]]]
):
    use_model(lambda messages, info: text("ok"))
    job = run_body()
    for number in (1, 2, 3):
        await client.post(
            "/run", json={**job, "round": number, "feedback": f"F{number}" if number > 1 else None}
        )
    solver.newest_runs.clear()  # the job is no longer owned by round 3
    await client.post("/run", json={**job, "round": 2, "feedback": "F2", "lease_id": str(uuid4())})
    assert sorted(solver.histories[UUID(str(job["job_id"]))]) == [1, 2]


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
    result = await general.agent.run("Hi", model=model, deps=general.Deps(None))
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
    lines = [r.getMessage() for r in caplog.records if r.name == "agent.solver"]
    assert any("starts from the task and 0 feedback" in line for line in lines)
    line = next(line for line in lines if "tokens in" in line)
    assert "['evaluate']" in line
    assert "tokens in" in line
    assert KEY not in caplog.text


async def test_the_subject_notes_are_in_the_first_message(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
):
    requests = use_model(lambda messages, info: text("ok"))
    await client.post("/run", json=run_body(context=grant()))
    assert "Notes on the student in math:\n\n```\nWrites in pencil\n```" in prompts(requests[0])[0]


async def test_memory_tools_read_search_and_list(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
):
    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not tool_returns(messages):
            return ModelResponse(
                parts=[
                    ToolCallPart("read_memory", {"request": {"subject": "physics"}}),
                    ToolCallPart("search_memory", {"request": {"query": "units"}}),
                    ToolCallPart("list_history", {"request": {"limit": 3}}),
                ]
            )
        return text("ok")

    requests = use_model(script)
    await client.post("/run", json=run_body(context=grant()))
    returns = tool_returns(requests[-1])
    assert "Writes in pencil" in returns["read_memory"]
    assert "Forgets units" in returns["search_memory"]
    assert "grade='5'" in returns["list_history"]
    sent = {r.url.path.rsplit("/", 1)[-1]: json.loads(r.content) for r in context_api}
    assert sent["search_history"] == {"query": "units"}
    assert sent["list_history"] == {"limit": 3}


async def test_memory_is_unavailable_without_a_context_api(
    client: httpx2.AsyncClient, use_model: Callable[[Script], list[list[ModelMessage]]]
):
    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not tool_returns(messages):
            return tool_call("search_memory", request={"query": "units"})
        return text("ok")

    requests = use_model(script)
    await client.post("/run", json=run_body())
    assert "unavailable" in tool_returns(requests[-1])["search_memory"]


async def test_a_continued_round_shows_the_current_notes(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
    notes: dict[str, str | int],
):
    requests = use_model(lambda messages, info: text("ok"))
    job = run_body(context=grant())
    await client.post("/run", json=job)
    notes.update(profile_md="Now prefers long proofs", subject_md="Now uses a pen")
    await client.post("/run", json={**job, "round": 2, "feedback": "Show the check"})
    sent = prompts(requests[1])
    assert "Likes short steps" in sent[0]
    assert "Notes on the student now:\n\n```\nNow prefers long proofs\n```" in sent[1]
    assert "Notes on the student in math now:\n\n```\nNow uses a pen\n```" in sent[1]


def saved_bodies(context_api: list[httpx2.Request]) -> list[dict[str, object]]:
    return [json.loads(r.content) for r in context_api if r.url.path.endswith("/update_memory")]


def change(**scopes: dict[str, list[str]]) -> dict[str, object]:
    empty: dict[str, object] = {"profile": {}, "subject": {}}
    return empty | dict[str, object](scopes)


async def test_the_curator_saves_a_task_line_and_a_standing_request(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
    curator: CuratorStub,
    notes: dict[str, str | int],
):
    notes["subject_md"] = "- Writes in pencil"
    curator.curation = change(
        profile={"add": ["Always answer in two sentences"]},
        subject={"add": ["Task: solve a quadratic equation"]},
    )
    use_model(lambda messages, info: text("The roots are $x=\\pm 2$."))
    response = await client.post("/run", json=run_body(context=grant()))
    assert response.json() == {"answer": "The roots are $x=\\pm 2$."}
    assert saved_bodies(context_api) == [
        {
            "scope": "profile",
            "subject": None,
            "content_md": "Likes short steps\n- Always answer in two sentences",
            "base_version": 3,
        },
        {
            "scope": "subject",
            "subject": "math",
            "content_md": "- Writes in pencil\n- Task: solve a quadratic equation",
            "base_version": 5,
        },
    ]


async def test_the_curator_reads_the_round_and_the_notes_as_they_are(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
    curator: CuratorStub,
):
    use_model(lambda messages, info: text("The roots are $x=\\pm 2$."))
    await client.post("/run", json=run_body(round=2, feedback="Show the check", context=grant()))
    (request,) = curator.requests
    sent = prompts(request)[0]
    assert "Subject: math\nRound: 2" in sent
    assert "```\nSolve x^2 = 4\n```" in sent
    assert "```\nShow the check\n```" in sent
    assert "```\nThe roots are $x=\\pm 2$.\n```" in sent
    assert "Profile notes now:\n\n```\nLikes short steps\n```" in sent
    assert "Subject notes now:\n\n```\nWrites in pencil\n```" in sent
    assert KEY not in repr(request)


async def test_the_curator_makes_one_request_per_round_and_the_model_has_no_save_tool(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
    curator: CuratorStub,
):
    use_model(lambda messages, info: text("ok"))
    job = run_body(context=grant())
    await client.post("/run", json=job)
    await client.post("/run", json={**job, "round": 2, "feedback": "Show the check"})
    assert len(curator.requests) == 2
    assert saved_bodies(context_api) == []  # no change asked, no save


async def test_a_save_over_notes_that_changed_is_applied_again_on_the_fresh_doc(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
    curator: CuratorStub,
    notes: dict[str, str | int],
):
    def other_writer() -> None:  # another job changes the doc after this round read it
        notes.update(profile_md="- Likes short steps\n- Uses a pen", profile_version=4)

    curator.on_request = other_writer
    curator.curation = change(profile={"add": ["Mine"]})
    use_model(lambda messages, info: text("ok"))
    response = await client.post("/run", json=run_body(context=grant()))
    assert response.json() == {"answer": "ok"}
    assert [s["base_version"] for s in saved_bodies(context_api)] == [3, 4]
    assert notes["profile_md"] == "- Likes short steps\n- Uses a pen\n- Mine"


async def test_a_save_that_keeps_finding_the_doc_changed_gives_up_after_three_tries(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
    curator: CuratorStub,
    notes: dict[str, str | int],
    monkeypatch: pytest.MonkeyPatch,
):
    real = ContextClient.get_profile

    async def busy(self: ContextClient) -> Profile:
        profile = await real(self)
        notes["profile_version"] = int(notes["profile_version"]) + 1  # changed again meanwhile
        return profile

    def other_writer() -> None:
        notes["profile_version"] = 4
        monkeypatch.setattr(ContextClient, "get_profile", busy)

    curator.on_request = other_writer
    curator.curation = change(profile={"add": ["Mine"]})
    use_model(lambda messages, info: text("ok"))
    response = await client.post("/run", json=run_body(context=grant()))
    assert response.json() == {"answer": "ok"}
    assert len(saved_bodies(context_api)) == 3
    assert "Mine" not in str(notes["profile_md"])


async def test_a_remove_that_matches_no_note_is_ignored(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
    curator: CuratorStub,
):
    curator.curation = change(profile={"remove": ["Likes boxes"], "add": ["Uses a pen"]})
    use_model(lambda messages, info: text("ok"))
    await client.post("/run", json=run_body(context=grant()))
    assert [b["content_md"] for b in saved_bodies(context_api)] == [
        "Likes short steps\n- Uses a pen"
    ]


async def test_the_curator_removes_exactly_the_matched_note(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
    curator: CuratorStub,
    notes: dict[str, str | int],
):
    notes["profile_md"] = "- Likes short steps\n- Uses a pen\n- Likes short steps in physics"
    curator.curation = change(
        profile={"remove": ["Likes short steps"], "add": ["likes long steps"]}
    )
    use_model(lambda messages, info: text("ok"))
    await client.post("/run", json=run_body(round=2, feedback="Wrong", context=grant()))
    assert [b["content_md"] for b in saved_bodies(context_api)] == [
        "- Uses a pen\n- Likes short steps in physics\n- likes long steps"
    ]


async def test_the_curator_does_not_add_a_note_twice(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
    curator: CuratorStub,
    notes: dict[str, str | int],
):
    notes["profile_md"] = "- Likes short steps"
    curator.curation = change(profile={"add": ["likes short steps"]})
    use_model(lambda messages, info: text("ok"))
    await client.post("/run", json=run_body(context=grant()))
    assert saved_bodies(context_api) == []


async def test_a_doc_that_is_too_long_drops_the_oldest_task_lines_first(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
    curator: CuratorStub,
    notes: dict[str, str | int],
):
    tasks = [f"- Task: {n:02d} " + "x" * 190 for n in range(48)]
    notes["subject_md"] = "\n".join(["- Oldest rule", *tasks, "- Newest rule"])
    curator.curation = change(subject={"add": ["Task: " + "y" * 400]})
    use_model(lambda messages, info: text("ok"))
    await client.post("/run", json=run_body(context=grant()))
    (saved,) = saved_bodies(context_api)
    body = str(saved["content_md"])
    assert len(body) <= 10_000
    lines = body.splitlines()
    assert lines[0] == "- Oldest rule"
    assert lines[-2:] == ["- Newest rule", "- Task: " + "y" * 400] or lines[-1].startswith(
        "- Task: yyy"
    )
    assert "- Task: 00 " not in body
    assert "- Task: 47 " in body


@pytest.mark.parametrize("failure", ["error", "timeout", "conflict"])
async def test_a_curator_failure_still_returns_the_answer(
    failure: str,
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
    curator: CuratorStub,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    curator.curation = change(profile={"add": ["Secret note text"]})
    if failure == "error":
        curator.error = RuntimeError("curator model down")
    elif failure == "timeout":
        curator.delay = 5
        monkeypatch.setattr(solver, "CURATOR_TIMEOUT_SECONDS", 0.05)
    else:

        async def refused(self: ContextClient, *args: object) -> None:
            request = httpx2.Request("POST", "http://api/v1/context/update_memory")
            response = httpx2.Response(409, request=request)
            raise httpx2.HTTPStatusError("round is not current", request=request, response=response)

        monkeypatch.setattr(ContextClient, "update_memory", refused)
    use_model(lambda messages, info: text("The answer"))
    job = run_body(context=grant())
    response = await client.post("/run", json=job)
    assert response.json() == {"answer": "The answer"}
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any(f"Round 1 of job {job['job_id']}: the notes were not saved" in w for w in warnings)
    assert "Secret note text" not in caplog.text


async def test_there_is_no_curator_without_a_context_api(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    curator: CuratorStub,
):
    curator.curation = change(profile={"add": ["Never saved"]})
    requests = use_model(lambda messages, info: text("ok"))
    response = await client.post("/run", json=run_body())
    assert response.json() == {"answer": "ok"}
    assert len(requests) == 1
    assert curator.requests == []


async def test_a_run_that_was_overtaken_does_not_curate(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
    curator: CuratorStub,
):
    curator.curation = change(profile={"add": ["Never saved"]})
    use_model(lambda messages, info: text("ok"))
    job = run_body(context=grant())
    solver.newest_runs[UUID(str(job["job_id"]))] = (3, uuid4())  # a newer round owns the job
    response = await client.post("/run", json={**job, "round": 2})
    assert response.json() == {"answer": "ok"}
    assert curator.requests == []
    assert saved_bodies(context_api) == []


async def test_a_continued_round_tells_the_model_that_cleared_notes_are_gone(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
    notes: dict[str, str | int],
):
    requests = use_model(lambda messages, info: text("ok"))
    job = run_body(context=grant())
    await client.post("/run", json=job)
    notes.update(profile_md="", subject_md="")
    await client.post("/run", json={**job, "round": 2, "feedback": "Show the check"})
    followup = prompts(requests[1])[1]
    assert "replace any earlier notes" in followup
    assert "Notes on the student now:\n\n(none)" in followup
    assert "Notes on the student in math now:\n\n(none)" in followup
    assert "update_memory" not in followup


def memory_reads(seen: list[httpx2.Request]) -> list[str | None]:
    return [
        json.loads(r.content)["subject"] for r in seen if r.url.path.endswith("/get_subject_memory")
    ]


async def test_the_subject_the_curator_names_is_used_to_read_and_save_the_notes(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
    curator: CuratorStub,
    picker: PickerStub,
):
    picker.name = "physics"
    curator.curation = change(subject={"add": ["Forgets units"]})
    requests = use_model(lambda messages, info: text("ok"))
    await client.post("/run", json=run_body(context=grant()))
    (pick,) = picker.requests
    assert "The student's known subjects: physics, math" in prompts(pick)[0]
    assert "```\nSolve x^2 = 4\n```" in prompts(pick)[0]
    assert memory_reads(context_api) == ["physics"]
    assert "Notes on the student in physics:" in prompts(requests[0])[0]
    assert "Subject: physics\nRound: 1" in prompts(curator.requests[0])[0]
    (saved,) = saved_bodies(context_api)
    assert saved["scope"] == "subject"
    assert saved["subject"] == "physics"


async def test_a_later_round_of_the_job_keeps_its_subject_without_naming_it_again(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
    picker: PickerStub,
):
    picker.name = "physics"
    use_model(lambda messages, info: text("ok"))
    job = run_body(context=grant())
    await client.post("/run", json=job)
    picker.name = "chemistry"
    await client.post("/run", json={**job, "round": 2, "feedback": "Show the check"})
    assert len(picker.requests) == 1
    assert memory_reads(context_api) == ["physics", "physics"]


async def test_a_curator_that_cannot_name_the_subject_leaves_the_subject_of_the_job(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
    picker: PickerStub,
    caplog: pytest.LogCaptureFixture,
):
    picker.error = RuntimeError("model down")
    use_model(lambda messages, info: text("The answer"))
    job = run_body(context=grant())
    response = await client.post("/run", json=job)
    assert response.json() == {"answer": "The answer"}
    assert memory_reads(context_api) == ["math"]
    assert UUID(str(job["job_id"])) not in solver.job_subjects
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any(f"Round 1 of job {job['job_id']}: no subject named" in w for w in warnings)


async def test_a_subject_that_takes_too_long_to_name_is_given_up(
    client: httpx2.AsyncClient,
    context_api: list[httpx2.Request],
    monkeypatch: pytest.MonkeyPatch,
):
    async def slow(model: Model, task_text: str, known: list[str]) -> str:
        await asyncio.sleep(5)
        return "physics"

    monkeypatch.setattr(solver, "pick_subject", slow)
    monkeypatch.setattr(solver, "SUBJECT_TIMEOUT_SECONDS", 0.05)

    def make_model(key: SecretStr) -> Model:
        return FunctionModel(lambda messages, info: text("ok"))

    monkeypatch.setattr(solver, "make_model", make_model)
    response = await client.post("/run", json=run_body(context=grant()))
    assert response.json() == {"answer": "ok"}
    assert memory_reads(context_api) == ["math"]


async def test_read_memory_defaults_to_the_subject_of_the_job(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
    picker: PickerStub,
):
    picker.name = "physics"

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not tool_returns(messages):
            return tool_call("read_memory", request={})
        return text("ok")

    requests = use_model(script)
    await client.post("/run", json=run_body(context=grant()))
    assert memory_reads(context_api) == ["physics", "physics"]
    assert "subject='physics'" in tool_returns(requests[-1])["read_memory"]


async def test_a_job_whose_subject_was_named_by_an_overtaken_run_is_not_remembered(
    client: httpx2.AsyncClient,
    use_model: Callable[[Script], list[list[ModelMessage]]],
    context_api: list[httpx2.Request],
):
    use_model(lambda messages, info: text("ok"))
    job = run_body(context=grant())
    solver.newest_runs[UUID(str(job["job_id"]))] = (3, uuid4())
    await client.post("/run", json={**job, "round": 2})
    assert solver.job_subjects == {}
