import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from typing import Protocol, cast
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
from pydantic_ai.models.function import AgentInfo, FunctionDef, FunctionModel
from pydantic_ai.usage import RequestUsage

from agent import server, solver
from agent.context import ContextClient, ContextStatusError
from agent.models import MODEL_TIMEOUT_SECONDS, ROLE_MODELS, Role, make_models
from agent.roles import REQUEST_LIMITS, general
from agent.roles.curator import pick_subject
from agent.roles.orchestrator import WorkResult
from agent.server import SAMPLE_ANSWER, app
from shared.agent_run import AgentRun, ContextGrant
from shared.context import Profile

pytestmark = pytest.mark.anyio

KEY = "sk-or-secret-key"
Script = Callable[[list[ModelMessage], AgentInfo], ModelResponse]


class UseModel(Protocol):
    def __call__(
        self, script: Script, orchestrator: Script = ..., /
    ) -> list[list[ModelMessage]]: ...


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


def pick_reply(info: AgentInfo, name: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"name": name})])


def curation_reply(info: AgentInfo, curation: dict[str, object]) -> ModelResponse:
    return tool_call(info.output_tools[0].name, **curation)


@dataclass
class ReaderStub:
    """What the reader step gets from the fake model: its requests, and what it answers."""

    requests: list[list[ModelMessage]] = field(default_factory=lambda: [])
    answer: str = "Transcribed page"
    error: Exception | None = None


@pytest.fixture
def file_reader() -> ReaderStub:
    return ReaderStub()


@pytest.fixture
def curator() -> CuratorStub:
    return CuratorStub()


@pytest.fixture
def picker() -> PickerStub:
    return PickerStub()


def final_reply(info: AgentInfo, *ids: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"answer": list(ids)})])


def default_orchestrator(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Delegates the whole task to general, then answers with its result."""
    last = messages[-1]
    if isinstance(last, ModelRequest):
        for part in last.parts:
            if (
                isinstance(part, ToolReturnPart)
                and isinstance(part.content, WorkResult)
                and part.content.id
            ):
                return final_reply(info, part.content.id)
    return tool_call("run_agent", role="general", brief="Answer the whole task.")


def quiet_side(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """The curator model: names math as the subject and asks for no change to the notes."""
    if is_picker(info):
        return pick_reply(info, "math")
    return curation_reply(info, {"profile": {}, "subject": {}})


def unused_reader(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    raise AssertionError("The reader model was not meant to be called")


def role_models(
    worker: FunctionDef,
    orchestrator: FunctionDef = default_orchestrator,
    curator: FunctionDef = quiet_side,
    reader: FunctionDef = unused_reader,
) -> dict[Role, Model]:
    """One model per role: the three workers share one script."""
    return {
        "orchestrator": FunctionModel(orchestrator),
        "general": FunctionModel(worker),
        "math": FunctionModel(worker),
        "writer": FunctionModel(worker),
        "curator": FunctionModel(curator),
        "reader": FunctionModel(reader),
    }


def patch_models(
    monkeypatch: pytest.MonkeyPatch,
    worker: FunctionDef,
    orchestrator: FunctionDef = default_orchestrator,
    curator: FunctionDef = quiet_side,
    reader: FunctionDef = unused_reader,
) -> None:
    def make_models(
        key: SecretStr, http_client: httpx2.AsyncClient | None = None
    ) -> dict[Role, Model]:
        return role_models(worker, orchestrator, curator, reader)

    monkeypatch.setattr(solver, "make_models", make_models)


@pytest.fixture
def orchestrator_requests() -> list[list[ModelMessage]]:
    """The messages each orchestrator request carried."""
    return []


@pytest.fixture
def use_model(
    monkeypatch: pytest.MonkeyPatch,
    curator: CuratorStub,
    picker: PickerStub,
    file_reader: ReaderStub,
    orchestrator_requests: list[list[ModelMessage]],
) -> UseModel:
    """Replace the models with scripts. Returns the messages each worker request carried.

    The script answers for the workers (general, math and writer). The orchestrator delegates the
    whole task to general and answers with its result, unless a test gives its own script. The
    curator's requests go to the stub instead, which returns no change unless a test sets one, and
    the requests that name the subject go to the picker stub, which names math.
    """

    def install(
        script: Script, orchestrator: Script = default_orchestrator
    ) -> list[list[ModelMessage]]:
        requests: list[list[ModelMessage]] = []

        def work(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            requests.append(list(messages))
            return script(messages, info)

        def lead(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            orchestrator_requests.append(list(messages))
            return orchestrator(messages, info)

        async def side(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if is_picker(info):
                picker.requests.append(list(messages))
                if picker.error:
                    raise picker.error
                return pick_reply(info, picker.name)
            curator.requests.append(list(messages))
            if curator.on_request:
                curator.on_request()
            await asyncio.sleep(curator.delay)
            if curator.error:
                raise curator.error
            return curation_reply(info, curator.curation)

        def read(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            file_reader.requests.append(list(messages))
            if file_reader.error:
                raise file_reader.error
            return text(file_reader.answer)

        patch_models(monkeypatch, work, lead, side, read)
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
def task_files() -> list[dict[str, object]]:
    """The files the fake Context API reports on the task."""
    return []


@pytest.fixture
def stored_files() -> dict[str, tuple[bytes, str]]:
    """The bytes and type the fake Context API serves for each file id."""
    return {}


@pytest.fixture
def context_failures() -> dict[str, int]:
    """The Context API tools that fail, and the status each answers with."""
    return {}


@pytest.fixture
def context_api(
    monkeypatch: pytest.MonkeyPatch,
    task_rounds: list[dict[str, object]],
    notes: dict[str, str | int],
    context_failures: dict[str, int],
    task_files: list[dict[str, object]],
    stored_files: dict[str, tuple[bytes, str]],
) -> list[httpx2.Request]:
    seen: list[httpx2.Request] = []
    job_id = str(uuid4())

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        tool = request.url.path.rsplit("/", 1)[-1]
        if tool in context_failures:
            return httpx2.Response(context_failures[tool], json={"detail": "down"})
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
                    "files": task_files,
                }
                return httpx2.Response(200, json=task)
            case "get_file":
                data, media_type = stored_files[json.loads(request.content)["file_id"]]
                return httpx2.Response(200, content=data, headers={"content-type": media_type})
            case "save_file_text":
                return httpx2.Response(
                    200, json={"file_id": json.loads(request.content)["file_id"]}
                )
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


async def test_the_answer_is_the_text_of_the_model(client: httpx2.AsyncClient, use_model: UseModel):
    requests = use_model(lambda messages, info: text("The roots are $x=\\pm 2$."))
    response = await client.post("/run", json=run_body())
    assert response.json() == {"answer": "The roots are $x=\\pm 2$."}
    first = requests[0]
    assert "Solve x^2 = 4" in prompts(first)[0]
    assert KEY not in repr(first)


async def test_the_model_can_call_a_math_tool(client: httpx2.AsyncClient, use_model: UseModel):
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


async def test_all_tools_are_offered(client: httpx2.AsyncClient, use_model: UseModel):
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
    client: httpx2.AsyncClient, use_model: UseModel
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
    use_model: UseModel,
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
    client: httpx2.AsyncClient, use_model: UseModel
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
    use_model: UseModel,
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


async def test_a_feedback_round_continues_the_orchestrator_conversation(
    client: httpx2.AsyncClient,
    use_model: UseModel,
    orchestrator_requests: list[list[ModelMessage]],
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level("INFO", logger="agent.solver")
    requests = use_model(lambda messages, info: text(f"Answer {len(requests)}"))
    job = run_body()
    first = await client.post("/run", json=job)
    second = await client.post("/run", json={**job, "round": 2, "feedback": "Show the check"})
    assert first.json() == {"answer": "Answer 1"}
    assert second.json() == {"answer": "Answer 2"}
    # Round 1 took two orchestrator requests: the second round's first one carries the first
    # prompt, round 1's delegation and its result, and the feedback.
    sent = prompts(orchestrator_requests[2])
    assert len(sent) == 2
    assert "Solve x^2 = 4" in sent[0]
    assert "Show the check" in sent[1]
    assert "Answer 1" in tool_returns(orchestrator_requests[2])["run_agent"]
    assert f"Round 2 of job {job['job_id']} continues round 1" in caplog.text
    # Workers keep no history: the second one starts from its own message.
    assert len(requests[1]) == 1
    assert "Show the check" in prompts(requests[1])[0]


async def test_a_fresh_process_rebuilds_the_job_from_the_task(
    client: httpx2.AsyncClient,
    use_model: UseModel,
    context_api: list[httpx2.Request],
    orchestrator_requests: list[list[ModelMessage]],
):
    requests = use_model(lambda messages, info: text("Again"))
    # No history for this job: round 2 starts from the task, with the feedback of the rounds.
    await client.post("/run", json=run_body(round=2, feedback="Show the check", context=grant()))
    for sent in (prompts(orchestrator_requests[0]), prompts(requests[0])):
        assert len(sent) == 1
        assert "Solve x^2 = 4" in sent[0]
        assert "```\nShow the check\n```" in sent[0]


def round_row(number: int, outcome: str, feedback: str | None) -> dict[str, object]:
    return {"number": number, "feedback": feedback, "outcome": outcome, "artifacts": []}


async def test_a_round_that_failed_on_the_api_is_not_continued(
    client: httpx2.AsyncClient,
    use_model: UseModel,
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
    use_model: UseModel,
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
    client: httpx2.AsyncClient,
    use_model: UseModel,
    orchestrator_requests: list[list[ModelMessage]],
):
    use_model(lambda messages, info: text("ok"))
    job = {**run_body(), "feedback": "More", "round": 2}
    await client.post("/run", json={**job, "lease_id": str(uuid4())})
    await client.post("/run", json={**job, "lease_id": str(uuid4())})
    assert len(prompts(orchestrator_requests[2])) == 1


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
        return text("new" if calls == 2 else "next")

    patch_models(monkeypatch, respond)
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

    # The feedback round continues the newer conversation and its results.
    feedback = new.model_copy(update={"round": 2, "feedback": "More"})
    assert (await solver.solve_with_model(feedback)).answer == "next"
    kept = solver.histories[job.job_id][2].results
    assert {i: r.text for i, r in kept.items()} == {"r1": "new", "r2": "next"}


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
        calls += 1
        return text("new" if calls == 1 else "old")  # the new lease answers first

    patch_models(monkeypatch, respond)
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
    kept = solver.histories[job.job_id][2].results
    assert {i: r.text for i, r in kept.items()} == {"r1": "new"}


async def test_a_round_that_runs_again_keeps_the_feedback_of_earlier_rounds(
    client: httpx2.AsyncClient,
    use_model: UseModel,
    orchestrator_requests: list[list[ModelMessage]],
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
    again = prompts(orchestrator_requests[6])  # each earlier orchestrator run took two requests
    assert sum("Feedback A" in p for p in again) == 1
    assert sum("Feedback B" in p for p in again) == 1
    seen = [
        str(p.content)
        for m in orchestrator_requests[6]
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart) and p.tool_name == "run_agent"
    ]
    assert [("Answer 1" in r, "Answer 2" in r) for r in seen] == [(True, False), (False, True)]
    # not the answer of the first attempt at round 3
    kept = solver.histories[UUID(str(job["job_id"]))]
    assert sorted(kept) == [1, 2, 3]
    assert {i: r.text for i, r in kept[3].results.items()} == {
        "r1": "Answer 1",
        "r2": "Answer 2",
        "r3": "Answer 4",
    }


async def test_a_rerun_round_drops_the_checkpoints_after_it(
    client: httpx2.AsyncClient, use_model: UseModel
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


async def test_the_http_client_is_closed_after_the_round(monkeypatch: pytest.MonkeyPatch):
    failing = False
    clients: list[httpx2.AsyncClient] = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if failing:
            raise RuntimeError("model down")
        return text("ok")

    def make_models(key: SecretStr, http_client: httpx2.AsyncClient) -> dict[Role, Model]:
        clients.append(http_client)
        return role_models(script)

    monkeypatch.setattr(solver, "make_models", make_models)
    job = AgentRun(job_id=uuid4(), round=1, text="Solve", openrouter_key=SecretStr(KEY))
    await solver.solve_with_model(job)
    failing = True
    with pytest.raises(RuntimeError):
        await solver.solve_with_model(job)
    assert len(clients) == 2
    assert all(c.is_closed for c in clients)


async def test_a_worker_that_loops_on_its_tools_fails_and_the_fallback_is_stopped_too(
    client: httpx2.AsyncClient,
    use_model: UseModel,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setitem(REQUEST_LIMITS, "general", 3)
    requests = use_model(lambda messages, info: tool_call("evaluate", expression="1+1"))
    with pytest.raises(Exception, match="UsageLimitExceeded"):
        await client.post("/run", json=run_body())
    # Each attempt stops at the limit: the worker's results fail, then the fallback fails.
    assert len(requests) >= 6
    assert len(requests) % 3 == 0


async def test_a_round_that_overruns_its_time_is_stopped(monkeypatch: pytest.MonkeyPatch):
    async def slow(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await asyncio.sleep(5)
        return text("late")

    patch_models(monkeypatch, slow, slow)
    monkeypatch.setattr(solver, "ROUND_TIMEOUT_SECONDS", 0.05)
    job = AgentRun(job_id=uuid4(), round=1, text="Solve", openrouter_key=SecretStr(KEY))
    with pytest.raises(TimeoutError):
        await solver.solve_with_model(job)


async def test_a_round_that_overruns_answers_with_a_done_result_else_raises(
    monkeypatch: pytest.MonkeyPatch,
):
    async def slow_lead(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        last = messages[-1]
        if isinstance(last, ModelRequest) and any(
            isinstance(p, ToolReturnPart) for p in last.parts
        ):
            await asyncio.sleep(5)
        return default_orchestrator(messages, info)

    def work(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return text("General answer")

    patch_models(monkeypatch, work, slow_lead)
    monkeypatch.setattr(solver, "ROUND_TIMEOUT_SECONDS", 0.5)
    job = AgentRun(job_id=uuid4(), round=1, text="Solve", openrouter_key=SecretStr(KEY))
    assert (await solver.solve_with_model(job)).answer == "General answer"
    assert job.job_id not in solver.histories

    async def slow_all(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await asyncio.sleep(5)
        return text("late")

    patch_models(monkeypatch, work, slow_all)
    with pytest.raises(TimeoutError):
        await solver.solve_with_model(job.model_copy(update={"job_id": uuid4()}))


async def test_the_model_settings_bound_reasoning_tokens_and_time():
    models = make_models(SecretStr(KEY))
    assert set(models) == set(ROLE_MODELS)
    for role, spec in ROLE_MODELS.items():
        settings = cast(dict[str, object], models[role].settings)
        reasoning = {"max_tokens": spec.reasoning_tokens} if spec.reasoning_tokens else None
        assert settings.get("openrouter_reasoning") == reasoning
        assert settings.get("max_tokens") == spec.max_output_tokens
        assert settings.get("timeout") == spec.timeout_seconds
        assert spec.reasoning_tokens < spec.max_output_tokens
        assert spec.timeout_seconds < solver.ROUND_TIMEOUT_SECONDS
    assert MODEL_TIMEOUT_SECONDS < solver.ROUND_TIMEOUT_SECONDS
    assert ROLE_MODELS["reader"].reasoning_tokens == 0


async def test_an_empty_reply_is_no_answer(client: httpx2.AsyncClient, use_model: UseModel):
    use_model(lambda messages, info: text("  \n"))
    assert (await client.post("/run", json=run_body())).json() == {"answer": None}


async def test_without_a_key_the_fake_answers(
    client: httpx2.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(server, "SOLVE_SECONDS", 0)

    def never(key: SecretStr, http_client: httpx2.AsyncClient) -> dict[Role, Model]:
        raise AssertionError("the models were built")

    monkeypatch.setattr(solver, "make_models", never)
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
            "model": ROLE_MODELS["general"].model_id,
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
    model = make_models(SecretStr(KEY), http)["general"]
    caplog.set_level("INFO", logger="agent.solver")
    result = await general.agent.run("Hi", model=model, deps=general.Deps(None))
    assert result.output == "Hello"

    (request,) = sent
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    payload = json.loads(request.content)
    spec = ROLE_MODELS["general"]
    assert payload["model"] == spec.model_id == "deepseek/deepseek-v4.1-flash"
    assert payload["provider"] == {"sort": "price"}
    assert payload["reasoning"] == {"max_tokens": spec.reasoning_tokens}
    assert payload["max_tokens"] == spec.max_output_tokens
    assert spec.reasoning_tokens < spec.max_output_tokens
    functions = {t["function"]["name"] for t in payload["tools"] if t.get("type") == "function"}
    assert functions >= {"evaluate", "write_document"}


async def test_the_round_log_names_each_role_and_the_cost_but_not_the_key(
    client: httpx2.AsyncClient, use_model: UseModel, caplog: pytest.LogCaptureFixture
):
    def worker(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not tool_returns(messages) and any(t.name == "evaluate" for t in info.function_tools):
            return tool_call("evaluate", expression="2+3")
        return ModelResponse(parts=[TextPart("5")], provider_details={"cost": 0.25})

    def lead(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart("run_agent", {"role": "math", "brief": "Add"}),
                    ToolCallPart("run_agent", {"role": "writer", "brief": "Explain"}),
                ]
            )
        return final_reply(info, "r1", "r2")

    use_model(worker, lead)
    caplog.set_level("INFO", logger="agent")
    await client.post("/run", json=run_body())
    lines = [r.getMessage() for r in caplog.records]
    assert any("starts from the task and 0 feedback" in line for line in lines)
    for role in ("math", "writer"):
        line = next(line for line in lines if line.startswith(f"[{role}] "))
        assert "tokens in" in line
    assert "['evaluate']" in next(line for line in lines if line.startswith("[math] "))
    orchestrator_line = next(
        line for line in lines if line.startswith("[orchestrator]") and "model requests" in line
    )
    assert "calls ['math', 'writer']" in orchestrator_line
    assert "tokens in" in orchestrator_line
    assert lines[-1].startswith("[round] Round 1 of job ")
    assert "all roles" in lines[-1]
    assert "cost $0.5000" in lines[-1]  # the two workers, as OpenRouter reported
    assert KEY not in caplog.text


async def test_the_subject_notes_are_in_the_first_message(
    client: httpx2.AsyncClient,
    use_model: UseModel,
    context_api: list[httpx2.Request],
):
    requests = use_model(lambda messages, info: text("ok"))
    await client.post("/run", json=run_body(context=grant()))
    assert "Notes on the student in math:\n\n```\nWrites in pencil\n```" in prompts(requests[0])[0]


async def test_memory_tools_read_search_and_list(
    client: httpx2.AsyncClient,
    use_model: UseModel,
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
    client: httpx2.AsyncClient, use_model: UseModel
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
    use_model: UseModel,
    context_api: list[httpx2.Request],
    notes: dict[str, str | int],
    orchestrator_requests: list[list[ModelMessage]],
):
    use_model(lambda messages, info: text("ok"))
    job = run_body(context=grant())
    await client.post("/run", json=job)
    notes.update(profile_md="Now prefers long proofs", subject_md="Now uses a pen")
    await client.post("/run", json={**job, "round": 2, "feedback": "Show the check"})
    sent = prompts(orchestrator_requests[2])  # the first request of round 2
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
    use_model: UseModel,
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
    use_model: UseModel,
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
    use_model: UseModel,
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
    use_model: UseModel,
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
    use_model: UseModel,
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
    use_model: UseModel,
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
    use_model: UseModel,
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
    use_model: UseModel,
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
    use_model: UseModel,
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
    use_model: UseModel,
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
    use_model: UseModel,
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
    use_model: UseModel,
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
    use_model: UseModel,
    context_api: list[httpx2.Request],
    notes: dict[str, str | int],
    orchestrator_requests: list[list[ModelMessage]],
):
    use_model(lambda messages, info: text("ok"))
    job = run_body(context=grant())
    await client.post("/run", json=job)
    notes.update(profile_md="", subject_md="")
    await client.post("/run", json={**job, "round": 2, "feedback": "Show the check"})
    followup = prompts(orchestrator_requests[2])[1]
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
    use_model: UseModel,
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
    use_model: UseModel,
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
    use_model: UseModel,
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

    patch_models(monkeypatch, lambda messages, info: text("ok"))
    response = await client.post("/run", json=run_body(context=grant()))
    assert response.json() == {"answer": "ok"}
    assert memory_reads(context_api) == ["math"]


async def test_read_memory_defaults_to_the_subject_of_the_job(
    client: httpx2.AsyncClient,
    use_model: UseModel,
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
    use_model: UseModel,
    context_api: list[httpx2.Request],
):
    use_model(lambda messages, info: text("ok"))
    job = run_body(context=grant())
    solver.newest_runs[UUID(str(job["job_id"]))] = (3, uuid4())
    await client.post("/run", json={**job, "round": 2})
    assert solver.job_subjects == {}


def two_parts(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """An orchestrator that gives two parts to workers and answers with both results."""
    if len(messages) == 1:
        return ModelResponse(
            parts=[
                ToolCallPart("run_agent", {"role": "math", "brief": "Part one"}),
                ToolCallPart("run_agent", {"role": "writer", "brief": "Part two"}),
            ]
        )
    return final_reply(info, "r1", "r2")


async def test_the_results_the_orchestrator_lists_are_the_answer_in_order(
    client: httpx2.AsyncClient, use_model: UseModel
):
    def lead(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart("run_agent", {"role": "math", "brief": "Part one"}),
                    ToolCallPart("run_agent", {"role": "writer", "brief": "Part two"}),
                ]
            )
        return final_reply(info, "r2", "r1")

    def worker(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # Named by the brief: the two run at once, so their requests come in either order.
        return text("Worker 1" if "Part one" in "".join(prompts(messages)) else "Worker 2")

    use_model(worker, lead)
    response = await client.post("/run", json=run_body())
    assert response.json() == {"answer": "Worker 2\n\nWorker 1"}


async def test_a_feedback_round_can_use_a_result_of_round_1(
    client: httpx2.AsyncClient, use_model: UseModel
):
    def lead(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        last = messages[-1]
        if isinstance(last, ModelRequest):
            ids = [
                p.content.id
                for p in last.parts
                if isinstance(p, ToolReturnPart)
                and p.tool_name == "run_agent"
                and isinstance(p.content, WorkResult)
                and p.content.id
            ]
            if ids:
                return final_reply(info, ids[-1])
        feedback = len(prompts(messages)) > 1
        call: dict[str, object] = {"role": "writer", "brief": "Shorten it" if feedback else "Write"}
        if feedback:
            call["inputs"] = ["r1"]  # the result round 1 made
        return tool_call("run_agent", **call)

    def worker(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return text(f"Version {len(requests)}")

    requests = use_model(worker, lead)
    job = run_body()
    await client.post("/run", json=job)
    response = await client.post("/run", json={**job, "round": 2, "feedback": "Shorter"})
    assert response.json() == {"answer": "Version 2"}
    second = prompts(requests[1])[0]
    assert "Shorten it" in second
    assert "Earlier result r1" in second
    assert "Version 1" in second  # the full text of round 1's result


async def test_a_run_that_lost_the_lease_does_not_replace_the_checkpoint(
    client: httpx2.AsyncClient, use_model: UseModel
):
    job = run_body()
    job_id = UUID(str(job["job_id"]))
    taken_over = False

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if taken_over:  # a newer lease takes the job while this run works
            solver.newest_runs[job_id] = (1, uuid4())
            return text("Overtaken")
        return text("Kept")

    use_model(script)
    await client.post("/run", json=job)
    taken_over = True
    response = await client.post("/run", json={**job, "lease_id": str(uuid4())})
    assert response.json() == {"answer": "Overtaken"}
    kept = solver.histories[job_id][1]
    assert {i: r.text for i, r in kept.results.items()} == {"r1": "Kept"}


async def test_the_curator_runs_once_for_the_newest_run_only(
    client: httpx2.AsyncClient,
    use_model: UseModel,
    context_api: list[httpx2.Request],
    curator: CuratorStub,
):
    use_model(lambda messages, info: text("Part"), two_parts)
    job = run_body(context=grant())
    await client.post("/run", json=job)
    assert len(curator.requests) == 1  # two workers ran, one curator step
    solver.newest_runs[UUID(str(job["job_id"]))] = (3, uuid4())
    await client.post("/run", json={**job, "round": 2})
    assert len(curator.requests) == 1  # the overtaken run does not curate


async def test_the_curator_and_the_subject_run_on_the_curator_model(
    monkeypatch: pytest.MonkeyPatch, context_api: list[httpx2.Request]
):
    seen: list[str] = []

    def side(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append("subject" if is_picker(info) else "curator")
        return quiet_side(messages, info)

    patch_models(monkeypatch, lambda messages, info: text("ok"), curator=side)
    job = AgentRun(
        job_id=uuid4(),
        round=1,
        text="Solve",
        openrouter_key=SecretStr(KEY),
        context=ContextGrant(url="http://api:8000", token="tok"),
    )
    await solver.solve_with_model(job)
    assert seen == ["subject", "curator"]


async def test_the_curator_model_keeps_its_settings_and_is_metered():
    sent: list[dict[str, object]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        call = {
            "id": "c1",
            "type": "function",
            "function": {"name": "final_result", "arguments": '{"name": "physics"}'},
        }
        message: dict[str, object] = {"role": "assistant", "tool_calls": [call], "content": None}
        completion: dict[str, object] = {
            "id": "gen-1",
            "object": "chat.completion",
            "created": 1,
            "model": ROLE_MODELS["curator"].model_id,
            "provider": "SomeHost",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": message}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }
        return httpx2.Response(200, json=completion)

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
    model = solver.Metered(make_models(SecretStr(KEY), http)["curator"])
    assert await pick_subject(model, "A task", []) == "physics"
    assert sent[0]["reasoning"] == {"max_tokens": 300}  # the subject step's own budget
    assert sent[0]["max_tokens"] == ROLE_MODELS["curator"].max_output_tokens
    assert model.spend.tokens == 18


async def test_an_orchestrator_that_fails_after_a_worker_answers_with_that_result(
    client: httpx2.AsyncClient,
    use_model: UseModel,
    caplog: pytest.LogCaptureFixture,
):
    def lead(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return tool_call("run_agent", role="general", brief="Do it")
        raise RuntimeError("orchestrator down")

    use_model(lambda messages, info: text("The worker's text"), lead)
    caplog.set_level("INFO", logger="agent")
    job = run_body()
    response = await client.post("/run", json=job)
    assert response.json() == {"answer": "The worker's text"}
    assert any(
        r.levelname == "WARNING" and "fell back to general result r1" in r.getMessage()
        for r in caplog.records
    )
    assert UUID(str(job["job_id"])) not in solver.histories  # a fallback round keeps no checkpoint


async def test_an_orchestrator_that_fails_at_once_leaves_the_task_to_general_alone(
    client: httpx2.AsyncClient, use_model: UseModel
):
    def lead(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("orchestrator down")

    requests = use_model(lambda messages, info: text("Alone"), lead)
    response = await client.post("/run", json=run_body())
    assert response.json() == {"answer": "Alone"}
    (request,) = requests
    assert "Answer the whole task." in prompts(request)[0]
    assert "Solve x^2 = 4" in prompts(request)[0]


async def test_an_orchestrator_that_never_lists_a_usable_result_falls_back(
    client: httpx2.AsyncClient, use_model: UseModel
):
    def lead(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return final_reply(info, "r9")  # no such result, until the retries run out

    use_model(lambda messages, info: text("Alone"), lead)
    response = await client.post("/run", json=run_body())
    assert response.json() == {"answer": "Alone"}


async def test_a_fallback_that_fails_too_fails_the_round(
    client: httpx2.AsyncClient, use_model: UseModel
):
    def lead(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("orchestrator down")

    def worker(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("worker down")

    use_model(worker, lead)
    with pytest.raises(RuntimeError, match="the fallback"):
        await client.post("/run", json=run_body())


async def test_the_round_total_counts_the_responses_of_an_orchestrator_that_failed(
    client: httpx2.AsyncClient, use_model: UseModel, caplog: pytest.LogCaptureFixture
):
    def lead(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[ToolCallPart("run_agent", {"role": "general", "brief": "Do it"})],
                usage=RequestUsage(input_tokens=1000, output_tokens=500),
            )
        raise RuntimeError("orchestrator down")

    def worker(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart("Done")], usage=RequestUsage(input_tokens=100, output_tokens=20)
        )

    use_model(worker, lead)
    caplog.set_level("INFO", logger="agent")
    response = await client.post("/run", json=run_body())
    assert response.json() == {"answer": "Done"}
    assert "tokens 1620 (all roles)" in caplog.records[-1].getMessage()  # 1500 + 120


async def test_a_context_api_call_that_fails_in_a_worker_fails_the_round(
    use_model: UseModel,
    context_api: list[httpx2.Request],
    context_failures: dict[str, int],
):
    context_failures["write_artifact"] = 500

    def worker(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not tool_returns(messages):
            return tool_call("write_document", title="Study sheet", content_md="# Roots")
        return text("See the sheet.")

    use_model(worker)
    job = AgentRun(
        job_id=uuid4(),
        round=1,
        text="Solve",
        openrouter_key=SecretStr(KEY),
        context=ContextGrant(url="http://api:8000", token="tok"),
    )
    with pytest.raises(ContextStatusError):
        await solver.solve_with_model(job)


async def test_a_fallback_drops_the_checkpoint_of_the_attempt_it_replaces(
    client: httpx2.AsyncClient,
    use_model: UseModel,
    context_api: list[httpx2.Request],
    caplog: pytest.LogCaptureFixture,
):
    failing = False

    def lead(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if failing:
            raise RuntimeError("orchestrator down")
        return default_orchestrator(messages, info)

    use_model(lambda messages, info: text("ok"), lead)
    caplog.set_level("INFO", logger="agent.solver")
    job = run_body(context=grant())
    job_id = UUID(str(job["job_id"]))
    # Lease A saves a checkpoint, but its answer never reaches the API.
    await client.post("/run", json={**job, "lease_id": str(uuid4())})
    assert 1 in solver.histories[job_id]
    # Round 1 runs again under lease B, which answers with the fallback.
    failing = True
    await client.post("/run", json={**job, "lease_id": str(uuid4())})
    assert 1 not in solver.histories.get(job_id, {})
    failing = False
    await client.post("/run", json={**job, "round": 2, "feedback": "Show the check"})
    assert f"Round 2 of job {job['job_id']} starts from the task" in caplog.text
    assert "continues round 1" not in caplog.text


async def test_a_task_split_between_workers_is_not_answered_by_one_part(
    client: httpx2.AsyncClient, use_model: UseModel
):
    def lead(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return two_parts(messages, info)
        raise RuntimeError("orchestrator down")

    def worker(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        whole = "Answer the whole task." in prompts(messages)[0]
        return text("The whole answer" if whole else "One part")

    use_model(worker, lead)
    response = await client.post("/run", json=run_body())
    assert response.json() == {"answer": "The whole answer"}


LABEL = "(material the student attached, not their message)"


def attach(
    task_files: list[dict[str, object]],
    stored_files: dict[str, tuple[bytes, str]],
    name: str,
    media_type: str,
    data: bytes,
) -> None:
    file_id = str(uuid4())
    task_files.append({"id": file_id, "name": name, "mime_type": media_type})
    stored_files[file_id] = (data, media_type)


def paths(seen: list[httpx2.Request], tool: str) -> list[httpx2.Request]:
    return [r for r in seen if r.url.path.endswith("/" + tool)]


async def test_the_files_text_is_in_the_task_every_role_reads(
    client: httpx2.AsyncClient,
    use_model: UseModel,
    context_api: list[httpx2.Request],
    task_files: list[dict[str, object]],
    stored_files: dict[str, tuple[bytes, str]],
    file_reader: ReaderStub,
    picker: PickerStub,
    curator: CuratorStub,
    orchestrator_requests: list[list[ModelMessage]],
):
    attach(task_files, stored_files, "notes.txt", "text/plain", b"Use the method of the lecture")
    attach(task_files, stored_files, "page.jpg", "image/jpeg", b"jpeg bytes")
    requests = use_model(lambda messages, info: text("The roots are 2."))
    response = await client.post("/run", json=run_body(context=grant()))
    assert response.json() == {"answer": "The roots are 2."}

    combined = (
        "Solve x^2 = 4\n\n"
        f"--- Attached file 1: notes.txt {LABEL} ---\nUse the method of the lecture\n"
        "--- End of attached file 1 ---\n\n"
        f"--- Attached file 2: page.jpg {LABEL} ---\nTranscribed page\n"
        "--- End of attached file 2 ---"
    )
    assert combined in prompts(orchestrator_requests[0])[0]
    assert combined in prompts(requests[0])[0]
    assert combined in prompts(picker.requests[0])[0]
    assert combined in prompts(curator.requests[0])[0]
    assert len(file_reader.requests) == 1
    saved = [json.loads(r.content)["text"] for r in paths(context_api, "save_file_text")]
    assert saved == ["Use the method of the lecture", "Transcribed page"]


async def test_a_failed_read_does_not_fail_the_round(
    client: httpx2.AsyncClient,
    use_model: UseModel,
    context_api: list[httpx2.Request],
    task_files: list[dict[str, object]],
    stored_files: dict[str, tuple[bytes, str]],
    file_reader: ReaderStub,
):
    attach(task_files, stored_files, "page.png", "image/png", b"png bytes")
    attach(task_files, stored_files, "sheet.xlsx", "application/vnd.ms-excel", b"xls")
    file_reader.error = RuntimeError("reader down")
    requests = use_model(lambda messages, info: text("Answer"))
    response = await client.post("/run", json=run_body(context=grant()))
    assert response.json() == {"answer": "Answer"}
    prompt = prompts(requests[0])[0]
    assert '[The student attached "page.png" (image/png), which could not be read.]' in prompt
    assert (
        '[The student attached "sheet.xlsx" (application/vnd.ms-excel), which could not be read.]'
        in prompt
    )
    assert paths(context_api, "save_file_text") == []


async def test_text_saved_earlier_is_used_and_nothing_is_fetched(
    client: httpx2.AsyncClient,
    use_model: UseModel,
    context_api: list[httpx2.Request],
    task_files: list[dict[str, object]],
    file_reader: ReaderStub,
):
    task_files.append(
        {"id": str(uuid4()), "name": "page.png", "mime_type": "image/png", "text": "Saved text"}
    )
    requests = use_model(lambda messages, info: text("Answer"))
    await client.post("/run", json=run_body(context=grant()))
    assert f"--- Attached file 1: page.png {LABEL} ---\nSaved text" in prompts(requests[0])[0]
    assert paths(context_api, "get_file") == []
    assert paths(context_api, "save_file_text") == []
    assert file_reader.requests == []


async def test_only_the_newest_run_saves_the_text_of_files(
    client: httpx2.AsyncClient,
    use_model: UseModel,
    context_api: list[httpx2.Request],
    task_files: list[dict[str, object]],
    stored_files: dict[str, tuple[bytes, str]],
):
    attach(task_files, stored_files, "notes.txt", "text/plain", b"Body")
    use_model(lambda messages, info: text("Answer"))
    job = run_body(context=grant())
    solver.newest_runs[UUID(str(job["job_id"]))] = (3, uuid4())  # a newer round owns the job
    response = await client.post("/run", json=job)
    assert response.json() == {"answer": "Answer"}
    assert len(paths(context_api, "get_file")) == 1
    assert paths(context_api, "save_file_text") == []


async def test_the_round_without_a_context_api_is_unchanged_by_files(
    client: httpx2.AsyncClient, use_model: UseModel, file_reader: ReaderStub
):
    requests = use_model(lambda messages, info: text("Answer"))
    await client.post("/run", json=run_body("Just text"))
    assert "Attached file" not in prompts(requests[0])[0]
    assert file_reader.requests == []


async def test_the_readers_spend_counts_in_the_rounds_tokens(
    client: httpx2.AsyncClient,
    use_model: UseModel,
    task_files: list[dict[str, object]],
    stored_files: dict[str, tuple[bytes, str]],
    context_api: list[httpx2.Request],
    file_reader: ReaderStub,
    caplog: pytest.LogCaptureFixture,
):
    use_model(lambda messages, info: text("Answer"))
    caplog.set_level("INFO", logger="agent.solver")

    def round_tokens() -> int:
        line = next(r.getMessage() for r in reversed(caplog.records) if "[round]" in r.getMessage())
        return int(line.split("tokens ")[1].split(" ")[0].rstrip(","))

    await client.post("/run", json=run_body(context=grant()))
    without = round_tokens()
    attach(task_files, stored_files, "page.png", "image/png", b"png bytes")
    file_reader.answer = "word " * 400
    await client.post("/run", json=run_body(context=grant()))
    assert len(file_reader.requests) == 1
    assert round_tokens() > without + 400


async def test_the_picker_and_curator_read_the_typed_text_and_only_the_heads_of_files(
    client: httpx2.AsyncClient,
    use_model: UseModel,
    context_api: list[httpx2.Request],
    task_files: list[dict[str, object]],
    stored_files: dict[str, tuple[bytes, str]],
    picker: PickerStub,
    curator: CuratorStub,
    orchestrator_requests: list[list[ModelMessage]],
):
    attach(task_files, stored_files, "notes.txt", "text/plain", b"a" * 1_500 + b"b" * 1_500)
    use_model(lambda messages, info: text("Answer"))
    await client.post("/run", json=run_body(context=grant()))

    picked = prompts(picker.requests[0])[0]
    assert f"--- Attached file 1: notes.txt {LABEL} ---\n" + "a" * 1_000 + "\n--- End" in picked
    assert "a" * 1_001 not in picked
    assert "Solve x^2 = 4" in picked

    curated = prompts(curator.requests[0])[0]
    assert "a" * 1_500 + "b" * 500 + "\n--- End of attached file 1 ---" in curated
    assert "b" * 501 not in curated
    assert "Solve x^2 = 4" in curated
    assert "b" * 1_500 in prompts(orchestrator_requests[0])[0]  # the roles that answer read it all


async def test_reading_files_is_outside_the_rounds_deadline(
    client: httpx2.AsyncClient,
    context_api: list[httpx2.Request],
    task_files: list[dict[str, object]],
    stored_files: dict[str, tuple[bytes, str]],
    monkeypatch: pytest.MonkeyPatch,
):
    async def slow_reader(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await asyncio.sleep(0.6)
        return text("Transcribed page")

    patch_models(monkeypatch, lambda messages, info: text("Answer"), reader=slow_reader)
    monkeypatch.setattr(solver, "ROUND_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(solver, "ROUND_GRACE_SECONDS", 0)
    attach(task_files, stored_files, "page.png", "image/png", b"png bytes")
    response = await client.post("/run", json=run_body(context=grant()))
    assert response.json() == {"answer": "Answer"}


async def test_the_readers_spend_is_logged_when_the_round_fails(
    client: httpx2.AsyncClient,
    context_api: list[httpx2.Request],
    task_files: list[dict[str, object]],
    stored_files: dict[str, tuple[bytes, str]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    async def slow(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await asyncio.sleep(5)
        return text("late")

    patch_models(monkeypatch, slow, slow, reader=lambda messages, info: text("word " * 300))
    monkeypatch.setattr(solver, "ROUND_TIMEOUT_SECONDS", 0.05)
    caplog.set_level("INFO", logger="agent.solver")
    attach(task_files, stored_files, "page.png", "image/png", b"png bytes")
    with pytest.raises(TimeoutError):
        await client.post("/run", json=run_body(context=grant()))
    lines = [r.getMessage() for r in caplog.records if "[reader] Round" in r.getMessage()]
    assert len(lines) == 1
    assert int(lines[0].split("tokens ")[1].split(",")[0]) > 300
    assert not any("[round]" in r.getMessage() for r in caplog.records)


async def test_the_readers_spend_is_logged_when_reading_itself_is_cancelled(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    spent = solver.Spend(tokens=7, cost=0.25)
    caplog.set_level("INFO", logger="agent.solver")
    solver._log_reading(AgentRun(job_id=uuid4(), round=1, text="x"), spent)  # pyright: ignore[reportPrivateUsage]
    assert "tokens 7, cost $0.2500" in caplog.records[-1].getMessage()
