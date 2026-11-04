import asyncio
import json
from collections.abc import AsyncIterator, Callable
from uuid import uuid4

import httpx2
import pytest

from agent import server
from agent.context import ContextClient
from agent.server import SAMPLE_ANSWER, app
from shared.agent_run import AgentAnswer, AgentRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx2.AsyncClient]:
    monkeypatch.setattr("agent.server.SOLVE_SECONDS", 0)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://agent") as client:
        yield client


def body(text: str, **extra: object) -> dict[str, object]:
    return {"job_id": str(uuid4()), "round": 1, "text": text, **extra}


async def test_ready(client: httpx2.AsyncClient):
    assert (await client.get("/readyz")).json() == {"status": "ok"}


async def test_run_answers_with_the_sample(client: httpx2.AsyncClient):
    response = await client.post("/run", json=body("Solve x^2 - 5x + 6 = 0"))
    assert response.status_code == 200
    assert response.json() == {"answer": SAMPLE_ANSWER}


async def test_feedback_is_shown_in_a_fence_so_it_is_not_markdown(client: httpx2.AsyncClient):
    response = await client.post("/run", json=body("Solve", round=2, feedback="# say **hello**"))
    answer = response.json()["answer"]
    assert (
        answer == f"{SAMPLE_ANSWER}\n### Changes after your feedback\n\n```\n# say **hello**\n```\n"
    )


async def test_the_fence_is_longer_than_any_backticks_in_the_feedback(client: httpx2.AsyncClient):
    feedback = "close it ```` then # Heading"
    answer = (await client.post("/run", json=body("Solve", round=2, feedback=feedback))).json()[
        "answer"
    ]
    assert answer.endswith(f"`````\n{feedback}\n`````\n")


@pytest.mark.parametrize("text", ["please fail", "FAIL now", "it will Fail."])
async def test_the_word_fail_fails_the_run(client: httpx2.AsyncClient, text: str):
    assert (await client.post("/run", json=body(text))).json() == {"answer": None}


async def test_fail_must_be_a_whole_word(client: httpx2.AsyncClient):
    assert (await client.post("/run", json=body("a failure"))).json()["answer"] == SAMPLE_ANSWER


async def test_run_waits_before_answering(
    client: httpx2.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    waited: list[float] = []

    async def sleep(seconds: float) -> None:
        waited.append(seconds)

    monkeypatch.setattr("agent.server.asyncio.sleep", sleep)
    monkeypatch.setattr("agent.server.SOLVE_SECONDS", 10)
    await client.post("/run", json=body("Solve"))
    assert waited == [10]


async def test_run_sends_a_heartbeat_while_working(
    client: httpx2.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("agent.server.SOLVE_SECONDS", 0.35)
    monkeypatch.setattr("agent.server.HEARTBEAT_SECONDS", 0.1)
    response = await client.post("/run", json=body("Solve"))
    assert response.headers["content-type"] == "application/json"
    assert response.content.startswith(b"\n\n")
    assert response.content.lstrip() == AgentAnswer(answer=SAMPLE_ANSWER).model_dump_json().encode()
    assert AgentAnswer.model_validate_json(response.content).answer == SAMPLE_ANSWER


async def test_run_without_a_wait_sends_no_heartbeat(client: httpx2.AsyncClient):
    response = await client.post("/run", json=body("Solve"))
    assert response.content.startswith(b"{")


async def test_run_refuses_unknown_fields(client: httpx2.AsyncClient):
    assert (await client.post("/run", json=body("Solve", extra=1))).status_code == 422


async def test_two_calls_for_one_round_solve_once(
    client: httpx2.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    solves: list[int] = []
    real = server.solve

    async def counted(run: AgentRun) -> AgentAnswer:
        solves.append(run.round)
        await asyncio.sleep(0.05)
        return await real(run)

    monkeypatch.setattr(server, "solve", counted)
    same = body("Solve")
    first, second = await asyncio.gather(
        client.post("/run", json=same), client.post("/run", json=same)
    )
    assert first.json() == second.json() == {"answer": SAMPLE_ANSWER}
    assert solves == [1]


async def test_a_finished_round_is_served_from_the_cache(
    client: httpx2.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    same = body("Solve")
    await client.post("/run", json=same)

    async def never(run: AgentRun) -> AgentAnswer:
        raise AssertionError("solved again")

    monkeypatch.setattr(server, "solve", never)
    assert (await client.post("/run", json=same)).json() == {"answer": SAMPLE_ANSWER}


async def test_a_run_that_raised_is_not_cached(
    client: httpx2.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    real = server.solve
    calls = 0

    async def flaky(run: AgentRun) -> AgentAnswer:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        return await real(run)

    monkeypatch.setattr(server, "solve", flaky)
    same = body("Solve")
    with pytest.raises(RuntimeError):
        await client.post("/run", json=same)
    assert (await client.post("/run", json=same)).json() == {"answer": SAMPLE_ANSWER}
    assert calls == 2


async def test_a_hang_up_does_not_cancel_the_work(
    client: httpx2.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(server, "SOLVE_SECONDS", 0.2)
    run = AgentRun(job_id=uuid4(), round=1, text="Solve")
    stream = server.stream(run)
    waiter = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0.05)
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    await stream.aclose()
    work = server.runs[(run.job_id, 1)]
    assert not work.cancelled()
    assert (await work).answer == SAMPLE_ANSWER


async def test_the_cache_keeps_only_the_newest_runs(
    client: httpx2.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(server, "MAX_RUNS", 2)
    runs = [AgentRun(job_id=uuid4(), round=1, text="Solve") for _ in range(3)]
    for run in runs:
        await server.join_run(run)
    assert [key[0] for key in server.runs][-2:] == [run.job_id for run in runs[1:]]
    assert (runs[0].job_id, 1) not in server.runs


async def test_a_running_run_is_never_evicted(
    client: httpx2.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(server, "MAX_RUNS", 1)
    monkeypatch.setattr(server, "SOLVE_SECONDS", 0.3)
    first = AgentRun(job_id=uuid4(), round=1, text="Solve")
    running = server.join_run(first)
    server.join_run(AgentRun(job_id=uuid4(), round=1, text="Solve"))
    assert server.runs[(first.job_id, 1)] is running
    assert (await running).answer == SAMPLE_ANSWER


async def test_a_cancelled_run_is_dropped_so_the_round_can_run_again(
    client: httpx2.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(server, "SOLVE_SECONDS", 5)
    run = AgentRun(job_id=uuid4(), round=1, text="Solve")
    work = server.join_run(run)
    await asyncio.sleep(0)
    work.cancel()
    await asyncio.gather(work, return_exceptions=True)
    await asyncio.sleep(0)  # the done callback runs on the next loop turn
    assert (run.job_id, 1) not in server.runs


def context_transport(
    task_text: str = "Solve", fail_on: str | None = None
) -> tuple[httpx2.MockTransport, list[httpx2.Request]]:
    seen: list[httpx2.Request] = []
    job_id = uuid4()

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        tool = request.url.path.rsplit("/", 1)[-1]
        if tool == fail_on:
            return httpx2.Response(409, text="the round is not current")
        match tool:
            case "get_task":
                rounds: list[dict[str, object]] = [
                    {"number": 1, "feedback": None, "outcome": "answered", "artifacts": []},
                    {
                        "number": 2,
                        "feedback": "Show the check",
                        "outcome": "answered",
                        "artifacts": [],
                    },
                    {
                        "number": 3,
                        "feedback": "Explain step 2",
                        "outcome": "failed",
                        "artifacts": [],
                    },
                    {"number": 4, "feedback": "More detail", "outcome": "running", "artifacts": []},
                ]
                return httpx2.Response(
                    200,
                    json={
                        "job_id": str(job_id),
                        "subject": "math",
                        "level": "high_school",
                        "language": "en",
                        "text": task_text,
                        "round": 4,
                        "rounds": rounds,
                    },
                )
            case "get_profile":
                content = {"level": "high_school", "language": "en", "subject": "math"}
                return httpx2.Response(200, json=content | {"profile_md": ""})
            case _:
                return httpx2.Response(200, json={"artifact_id": str(uuid4())})

    return httpx2.MockTransport(handle), seen


@pytest.fixture
def context_calls(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[httpx2.Request]]:
    def install(task_text: str = "Solve", fail_on: str | None = None) -> list[httpx2.Request]:
        transport, seen = context_transport(task_text, fail_on)
        real = ContextClient

        def client_with(url: str, token: str) -> ContextClient:
            return real(url, token, transport)

        monkeypatch.setattr(server, "ContextClient", client_with)
        return seen

    return install


def with_context(round_number: int = 4) -> dict[str, object]:
    grant = {"url": "http://api:8000", "token": "tok"}
    return body("Solve", round=round_number, context=grant)


async def test_with_a_context_the_agent_reads_the_task_and_writes_a_draft(
    client: httpx2.AsyncClient, context_calls: Callable[..., list[httpx2.Request]]
):
    seen = context_calls()
    answer = (await client.post("/run", json=with_context())).json()["answer"]

    assert [r.url.path for r in seen] == [
        "/v1/context/get_task",
        "/v1/context/get_profile",
        "/v1/context/write_artifact",
    ]
    assert {r.headers["Authorization"] for r in seen} == {"Bearer tok"}
    assert {r.headers["Content-Type"] for r in seen} == {
        "application/json"
    }  # or FastAPI answers 422
    written = json.loads(seen[-1].content)
    assert written["kind"] == "draft"
    assert written["content_md"] == answer
    # The feedback comes from the Context API: answered rounds and this one. A failed round's is
    # left out, because the student's box gets it back and they may send it again.
    assert "```\nShow the check\n```" in answer
    assert "```\nMore detail\n```" in answer
    assert "Explain step 2" not in answer


async def test_a_context_api_error_gives_no_answer(
    client: httpx2.AsyncClient, context_calls: Callable[..., list[httpx2.Request]]
):
    context_calls(fail_on="get_profile")
    with pytest.raises(httpx2.HTTPStatusError) as error:
        await client.post("/run", json=with_context())
    assert "get_profile returned 409: the round is not current" in str(error.value)


async def test_the_word_fail_in_the_task_from_the_context_api_fails_the_run(
    client: httpx2.AsyncClient, context_calls: Callable[..., list[httpx2.Request]]
):
    seen = context_calls(task_text="please fail")
    assert (await client.post("/run", json=with_context())).json() == {"answer": None}
    assert not any(r.url.path.endswith("write_artifact") for r in seen)
