import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

import pytest
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

from agent.context import ContextClient, ContextError
from agent.models import Role
from agent.roles import orchestrator
from agent.roles.orchestrator import (
    MAX_CALLS,
    PREVIEW_CHARS,
    ROUND_TOKEN_LIMIT,
    Final,
    Job,
    Result,
    RoundState,
    WorkResult,
    answer_of,
    fallback_result,
    orchestrate,
    run_worker,
)
from agent.solver import Metered
from shared.context import Profile
from shared.models import Level

pytestmark = pytest.mark.anyio

Step = Callable[[AgentInfo], ModelResponse]
Reply = Callable[[str], Awaitable[str]]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def delegate(*calls: Mapping[str, object]) -> Step:
    """One orchestrator response with a run_agent call for each argument dict."""
    return lambda info: ModelResponse(parts=[ToolCallPart("run_agent", dict(c)) for c in calls])


def final(*ids: str) -> Step:
    return lambda info: ModelResponse(
        parts=[ToolCallPart(info.output_tools[0].name, {"answer": list(ids)})]
    )


@dataclass
class Worker:
    """A scripted worker model: what each request carried, and how it replies to the prompt."""

    reply: Reply
    requests: list[list[ModelMessage]] = field(default_factory=lambda: [])

    async def respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.requests.append(list(messages))
        return ModelResponse(parts=[TextPart(await self.reply(prompt_of(messages)))])


def prompt_of(messages: list[ModelMessage]) -> str:
    return "\n".join(
        str(p.content)
        for m in messages
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, UserPromptPart)
    )


def echo(answer: str) -> Reply:
    async def reply(prompt: str) -> str:
        return answer

    return reply


@dataclass
class Setup:
    state: RoundState
    orchestrator_requests: list[list[ModelMessage]]
    workers: dict[str, Worker]


def setup(
    steps: list[Step],
    replies: dict[str, Reply] | None = None,
    results: dict[str, Result] | None = None,
    round_number: int = 2,
) -> Setup:
    """A round state whose orchestrator follows the steps and whose workers reply as given."""
    requests: list[list[ModelMessage]] = []
    remaining = iter(steps)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        requests.append(list(messages))
        return next(remaining)(info)

    replies = {"general": echo("General"), "math": echo("Math"), "writer": echo("Writer")} | (
        replies or {}
    )
    workers = {role: Worker(reply) for role, reply in replies.items()}
    models: dict[Role, Model] = {"orchestrator": FunctionModel(respond)}
    for role in ("general", "math", "writer"):
        models[role] = FunctionModel(workers[role].respond)
    profile = Profile(
        level=Level.HIGH_SCHOOL, language="pl", subject="math", profile_md="Likes short steps",
        profile_version=3,
    )  # fmt: skip
    job = Job("Solve x^2 = 4", profile, "Writes in pencil", ["Show the check"], None, round_number)
    deadline = asyncio.get_running_loop().time() + 60
    state = RoundState(job, models, dict(results or {}), deadline, log_prefix="Round 2 of job x")
    return Setup(state, requests, workers)


def work_results(messages: list[ModelMessage]) -> list[WorkResult]:
    return [
        p.content
        for m in messages
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart) and isinstance(p.content, WorkResult)
    ]


async def test_a_delegation_reaches_the_worker_with_brief_job_and_full_inputs():
    earlier = Result("r1", "general", 1, "done", "The earlier answer, in full")
    s = setup(
        [delegate({"role": "writer", "brief": "Make it shorter", "inputs": ["r1"]}), final("r2")],
        results={"r1": earlier},
    )
    result = await orchestrate("Task", None, s.state)
    assert result.output == Final(answer=["r2"])
    assert s.state.results["r2"] == Result("r2", "writer", 2, "done", "Writer", ("r1",))
    prompt = prompt_of(s.workers["writer"].requests[0])
    for part in (
        "Make it shorter",
        "Solve x^2 = 4",
        "Likes short steps",
        "Writes in pencil",
        "Show the check",
        "Earlier result r1",
        "The earlier answer, in full",
    ):
        assert part in prompt
    assert s.state.calls == 1


async def test_workers_get_no_message_history():
    s = setup(
        [
            delegate({"role": "general", "brief": "Part one"}),
            delegate({"role": "general", "brief": "Part two"}),
            final("r1", "r2"),
        ]
    )
    await orchestrate("Task", None, s.state)
    first, second = s.workers["general"].requests
    assert len(first) == len(second) == 1  # just the worker's own message
    assert "Part one" not in prompt_of(second)


async def test_final_with_two_ids_joins_them_in_order():
    s = setup(
        [
            delegate({"role": "math", "brief": "1a"}, {"role": "writer", "brief": "2"}),
            final("r2", "r1"),
        ]
    )
    result = await orchestrate("Task", None, s.state)
    assert [w.status for w in work_results(s.orchestrator_requests[1])] == ["done", "done"]
    assert answer_of(result.output, s.state.results) == "Writer\n\nMath"


@pytest.mark.parametrize("bad", ["r9", "r1"])  # unknown, failed
async def test_an_unknown_or_failed_id_makes_the_model_retry(bad: str):
    results = {
        "r1": Result("r1", "general", 2, "failed", "TimeoutError"),
        "r2": Result("r2", "general", 2, "done", "Answer"),
    }
    s = setup([final(bad), final("r2")], results=results)
    result = await orchestrate("Task", None, s.state)
    assert result.output == Final(answer=["r2"])
    # The validator's ModelRetry comes back as the output tool's return.
    (retry,) = [
        str(p.content)
        for m in s.orchestrator_requests[1]
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart)
    ]
    assert f"Unknown or failed results: {bad}." in retry


async def test_calls_of_one_response_run_at_once_but_never_more_than_two():
    gate = asyncio.Event()
    running = 0
    most = 0

    async def reply(prompt: str) -> str:
        nonlocal running, most
        running += 1
        most = max(most, running)
        if running == 2:
            gate.set()
        # Run one at a time, and the first never sees a second: it times out and fails.
        await asyncio.wait_for(gate.wait(), 1)
        running -= 1
        return "Part"

    calls = [{"role": "general", "brief": f"Part {n}"} for n in range(3)]
    s = setup([delegate(*calls), final("r1", "r2", "r3")], replies={"general": reply})
    await orchestrate("Task", None, s.state)
    assert [w.status for w in work_results(s.orchestrator_requests[1])] == ["done"] * 3
    assert most == 2


async def test_the_seventh_call_is_refused():
    calls = [{"role": "writer", "brief": f"Part {n}"} for n in range(MAX_CALLS + 1)]
    s = setup([delegate(*calls), final("r1")])
    await orchestrate("Task", None, s.state)
    statuses = [w.status for w in work_results(s.orchestrator_requests[1])]
    assert sorted(statuses) == ["done"] * MAX_CALLS + ["refused"]
    assert s.state.calls == MAX_CALLS
    assert len(s.state.results) == MAX_CALLS  # a refused call stores nothing


async def test_the_token_cap_counts_the_orchestrator_and_refuses_new_work():
    s = setup([delegate({"role": "general", "brief": "All"}), final("r1")], results={})
    s.state.results["r1"] = Result("r1", "general", 2, "done", "Answer")
    # The workers' tokens alone are just under the cap; the orchestrator's own push it over.
    s.state.worker_tokens = ROUND_TOKEN_LIMIT - 1
    await orchestrate("Task", None, s.state)
    (refused,) = work_results(s.orchestrator_requests[1])
    assert refused.status == "refused" and refused.id is None
    assert "Token limit" in refused.preview
    assert s.workers["general"].requests == []


async def test_an_input_that_is_unknown_or_failed_is_refused():
    results = {
        "r1": Result("r1", "general", 2, "failed", "TimeoutError"),
        "r2": Result("r2", "general", 2, "done", "Answer"),
    }
    s = setup(
        [delegate({"role": "writer", "brief": "Fix", "inputs": ["r1", "r7"]}), final("r2")],
        results=results,
    )
    await orchestrate("Task", None, s.state)
    (refused,) = work_results(s.orchestrator_requests[1])
    assert refused.status == "refused" and "r1, r7" in refused.preview
    assert s.state.calls == 0 and s.workers["writer"].requests == []


async def test_a_slow_worker_times_out_as_failed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(orchestrator, "WORKER_TIMEOUT_SECONDS", 0.05)

    async def slow(prompt: str) -> str:
        await asyncio.sleep(10)
        return "Late"

    s = setup([], replies={"math": slow})
    result = await run_worker(s.state, "math", "Compute", [])
    assert result == Result("r1", "math", 2, "failed", "TimeoutError")
    assert s.state.results["r1"] is result


async def test_a_worker_that_raises_fails_with_the_error_type_name():
    async def broken(prompt: str) -> str:
        raise ValueError("secret detail")

    s = setup([], replies={"general": broken})
    result = await run_worker(s.state, "general", "Answer", [])
    assert (result.status, result.text) == ("failed", "ValueError")


async def test_an_empty_answer_fails():
    s = setup([], replies={"writer": echo(" \x00 ")})
    result = await run_worker(s.state, "writer", "Write", [])
    assert (result.status, result.text) == ("failed", "EmptyAnswer")


async def test_the_orchestrator_sees_only_the_preview_of_a_long_result():
    long = "a" * PREVIEW_CHARS + "b" * 5_000
    s = setup([delegate({"role": "writer", "brief": "Essay"}), final("r1")], {"writer": echo(long)})
    await orchestrate("Task", None, s.state)
    (done,) = work_results(s.orchestrator_requests[1])
    assert done.preview == "a" * PREVIEW_CHARS and done.length == len(long)
    assert "b" * 50 not in "".join(repr(m) for m in s.orchestrator_requests)
    assert s.state.results["r1"].text == long


def fallback_state(*results: Result) -> RoundState:
    return RoundState(Job("Task", None, "", [], None, 2), {}, {r.id: r for r in results}, 0)


def test_fallback_of_a_single_whole_result_is_that_result():
    results = [
        Result("r1", "general", 1, "done", "Round 1"),
        Result("r2", "general", 2, "done", "Answer"),
    ]
    state = fallback_state(*results)
    assert fallback_result(state) is results[1]
    state.job.round = 3
    assert fallback_result(state) is None


def test_fallback_of_a_writer_that_used_the_other_result_is_the_writer():
    results = [
        Result("r1", "math", 2, "done", "Math"),
        Result("r2", "writer", 2, "done", "Essay", ("r1",)),
        Result("r3", "math", 1, "done", "Round 1"),
    ]
    assert fallback_result(fallback_state(*results)) is results[1]


def test_fallback_of_two_independent_parts_is_none():
    state = fallback_state(
        Result("r1", "math", 2, "done", "Math"),
        Result("r2", "writer", 2, "done", "Essay"),
    )
    assert fallback_result(state) is None


def test_fallback_is_none_when_an_earlier_attempt_failed_and_is_not_an_input():
    state = fallback_state(
        Result("r1", "general", 2, "failed", "TimeoutError"),
        Result("r2", "general", 2, "done", "Part"),
    )
    assert fallback_result(state) is None


async def test_a_cost_that_is_not_a_number_leaves_the_result_done():
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("Fine")], provider_details={"cost": "free"})

    s = setup([])
    metered = Metered(FunctionModel(respond))
    s.state.models = {**s.state.models, "math": metered}
    result = await run_worker(s.state, "math", "Compute", [])
    assert (result.status, result.text) == ("done", "Fine")
    assert metered.spend.cost == 0 and metered.spend.tokens > 0


async def test_a_final_that_lists_a_result_twice_makes_the_model_retry():
    results = {"r1": Result("r1", "general", 2, "done", "Answer")}
    s = setup([final("r1", "r1"), final("r1")], results=results)
    result = await orchestrate("Task", None, s.state)
    assert result.output == Final(answer=["r1"])
    retries = [
        str(p.content)
        for m in s.orchestrator_requests[1]
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart)
    ]
    assert "listed twice" in retries[0]


async def test_a_final_needs_a_result_of_this_round_but_may_keep_earlier_ones():
    results = {
        "r1": Result("r1", "general", 1, "done", "Old"),
        "r2": Result("r2", "general", 2, "done", "New"),
    }
    s = setup([final("r1"), final("r1", "r2")], results=results)
    result = await orchestrate("Task", None, s.state)
    assert result.output == Final(answer=["r1", "r2"])
    retries = [
        str(p.content)
        for m in s.orchestrator_requests[1]
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart)
    ]
    assert "produced in this round" in retries[0]


async def test_a_queued_worker_is_refused_when_the_limit_was_passed_while_it_waited():
    async def reply(prompt: str) -> str:
        s.state.worker_tokens = ROUND_TOKEN_LIMIT  # as if the workers just used everything
        return "Part"

    calls = [{"role": "general", "brief": f"Part {n}"} for n in range(3)]
    s = setup([delegate(*calls), final("r1")], replies={"general": reply})
    await orchestrate("Task", None, s.state)
    statuses = [w.status for w in work_results(s.orchestrator_requests[1])]
    assert sorted(statuses) == ["done", "done", "refused"]
    assert len(s.workers["general"].requests) == 2
    assert s.state.calls == 2  # the refused call is not counted
    assert len(s.state.results) == 2


class FailingContext(ContextClient):
    def __init__(self):  # no connection: write_draft always fails
        pass

    async def write_draft(self, title: str, content_md: str):
        raise ContextError("Context API returned 500")


async def test_a_context_error_in_a_worker_tool_ends_the_round_even_with_parallel_calls():
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            document = {"title": "Sheet", "content_md": "Text"}
            return ModelResponse(parts=[ToolCallPart("write_document", {"document": document})])
        return ModelResponse(parts=[TextPart("Done")])

    calls = [{"role": "general", "brief": "Sheet"}, {"role": "math", "brief": "Compute"}]
    s = setup([delegate(*calls), final("r1", "r2")])
    s.state.models = {**s.state.models, "general": FunctionModel(respond)}
    s.state.job.context = FailingContext()
    with pytest.raises(ContextError) as raised:
        await orchestrate("Task", None, s.state)
    assert type(raised.value) is ContextError  # plain, not an ExceptionGroup
    assert s.state.results["r1"].status == "failed"
