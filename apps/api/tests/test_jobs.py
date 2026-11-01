import asyncio
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import get_session
from api.dispatcher import Dispatcher
from api.jobs import _STATUS, FAILED_MESSAGE, get_dispatcher
from api.main import MAX_BODY, MAX_ERRORS, app, lifespan
from api.tables import Artifact, Job, JobRound, User
from shared.agent_run import AgentAnswer, AgentRun
from shared.models import ArtifactKind, JobStatus, Level

pytestmark = pytest.mark.anyio

ANSWER = "## Solution"


class FakeAgents:
    """Answers at once and remembers what it was asked. The word fail gets no answer."""

    def __init__(self) -> None:
        self.runs: list[AgentRun] = []
        self.unreachable = 0  # how many of the next solve() calls raise
        self.broken_clean_up = 0  # how many of the next hibernate() calls raise
        self.hibernated: list[uuid.UUID] = []
        self.released: list[uuid.UUID] = []
        self.status_at_clean_up: list[JobStatus | None] = []
        self.open_session: Callable[[], AsyncSession]  # set by the dispatcher fixture
        self.during_solve: Callable[[], Coroutine[Any, Any, None]] | None = None
        self.solving = asyncio.Event()

    async def _note_status(self, job_id: uuid.UUID) -> None:
        async with self.open_session() as session:
            status = await session.scalar(select(Job.status).where(Job.id == job_id))
        self.status_at_clean_up.append(status)

    async def solve(self, run: AgentRun) -> AgentAnswer:
        self.runs.append(run)
        self.solving.set()
        if self.during_solve:
            during, self.during_solve = self.during_solve, None
            await during()
        if self.unreachable:
            self.unreachable -= 1
            raise OSError("no route to the agent")
        return AgentAnswer(answer=None if "fail" in run.text.lower().split() else ANSWER)

    async def hibernate(self, job_id: uuid.UUID) -> None:
        await self._note_status(job_id)
        self.hibernated.append(job_id)
        if self.broken_clean_up:
            self.broken_clean_up -= 1
            raise OSError("no route to Substrate")

    async def release(self, job_id: uuid.UUID) -> None:
        await self._note_status(job_id)
        self.released.append(job_id)


class StepDispatcher(Dispatcher):
    """Runs background work only inside drain(): the test's requests all share one connection."""

    def __init__(self, agents: FakeAgents, open_session: Callable[[], AsyncSession]) -> None:
        super().__init__(agents, open_session)
        self.pending: list[Coroutine[Any, Any, None]] = []

    def _spawn(self, work: Coroutine[Any, Any, None]) -> None:
        self.pending.append(work)

    async def drain(self) -> None:
        while self.pending:
            await self.pending.pop(0)


@pytest.fixture
def agents() -> FakeAgents:
    return FakeAgents()


@pytest.fixture
def dispatcher(agents: FakeAgents, session: AsyncSession) -> StepDispatcher:
    def open_session() -> AsyncSession:
        """Its own session, as in production, so a missing commit is lost."""
        return AsyncSession(
            session.bind, join_transaction_mode="create_savepoint", expire_on_commit=False
        )

    agents.open_session = open_session
    return StepDispatcher(agents, open_session)


@pytest.fixture
async def client(dispatcher: StepDispatcher) -> AsyncIterator[httpx2.AsyncClient]:
    async def request_session() -> AsyncIterator[AsyncSession]:
        """Each request gets its own session, as in production, so a missing commit is lost."""
        async with dispatcher.open_session() as s:
            yield s

    app.dependency_overrides[get_session] = request_session
    app.dependency_overrides[get_dispatcher] = lambda: dispatcher
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
async def create(
    client: httpx2.AsyncClient, dispatcher: StepDispatcher
) -> Callable[..., Coroutine[Any, Any, dict]]:
    """Send a task and let the agent finish. Returns the job as it reads afterwards."""

    async def send(text: str = "Solve x^2 - 5x + 6 = 0") -> dict:
        response = await client.post("/jobs", json={"text": text})
        assert response.status_code == 201
        await dispatcher.drain()
        return (await client.get(f"/jobs/{response.json()['id']}")).json()

    return send


@pytest.fixture
async def feedback(
    client: httpx2.AsyncClient, dispatcher: StepDispatcher
) -> Callable[..., Coroutine[Any, Any, dict]]:
    """Send feedback and let the agent finish. Returns the job as it reads afterwards."""

    async def send(job: dict, text: str = "more steps", seen: int = 1) -> dict:
        response = await client.post(
            f"/jobs/{job['id']}/feedback", json={"text": text, "round": seen}
        )
        assert response.status_code == 200
        await dispatcher.drain()
        return (await client.get(f"/jobs/{job['id']}")).json()

    return send


Create = Callable[..., Coroutine[Any, Any, dict]]


async def test_a_new_job_is_queued_until_the_agent_answers(
    client: httpx2.AsyncClient, dispatcher: StepDispatcher, agents: FakeAgents
):
    response = await client.post("/jobs", json={"text": "Solve x"})
    queued = response.json()
    assert (queued["status"], queued["result"], queued["round"]) == ("queued", None, 1)
    assert (await client.get(f"/jobs/{queued['id']}")).json() == queued

    await dispatcher.drain()
    assert agents.runs == [
        AgentRun(job_id=uuid.UUID(queued["id"]), round=1, text="Solve x", feedback=None)
    ]
    done = (await client.get(f"/jobs/{queued['id']}")).json()
    assert (done["status"], done["result"]) == ("done", ANSWER)


async def test_create_solves_the_job(create: Create, session: AsyncSession):
    job = await create(f"  {'a' * 90}\nsecond line  ")
    assert job.keys() == {
        "id", "status", "createdAt", "title", "text", "result", "error", "accepted", "round",
        "feedback",
    }  # fmt: skip
    assert job["status"] == "done"
    assert (job["round"], job["feedback"], job["error"], job["accepted"]) == (1, None, None, False)
    assert job["result"] == ANSWER
    assert job["text"] == f"{'a' * 90}\nsecond line"
    assert job["title"] == "a" * 79 + "…"
    row = await session.get(Job, uuid.UUID(job["id"]), populate_existing=True)
    assert row is not None
    assert row.status == JobStatus.AWAITING_REVIEW
    assert row.review_deadline is not None


@pytest.mark.parametrize(
    "body",
    [
        {"text": ""},
        {"text": "   "},
        {"text": "a\u0000"},
        {"text": "\u200b\u2060"},
        {"text": "hi", "extra": 1},
        {},
    ],
)
async def test_create_rejects_bad_bodies(client: httpx2.AsyncClient, body: dict):
    assert (await client.post("/jobs", json=body)).status_code == 422


@pytest.mark.parametrize("text", ["please fail", "FAIL now"])
async def test_no_answer_fails_the_job(create: Create, agents: FakeAgents, text: str):
    job = await create(text.lower())
    assert (job["status"], job["error"], job["result"]) == ("failed", FAILED_MESSAGE, None)
    assert agents.released == [uuid.UUID(job["id"])]
    assert agents.hibernated == []


async def test_get_job(client: httpx2.AsyncClient, create: Create):
    job = await create()
    response = await client.get(f"/jobs/{job['id']}")
    assert response.status_code == 200
    assert response.json() == job


async def test_get_unknown_job_is_404(client: httpx2.AsyncClient):
    response = await client.get(f"/jobs/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}


async def test_list_shows_only_the_dev_users_jobs_newest_first(
    client: httpx2.AsyncClient, session: AsyncSession, create: Create
):
    first, second = await create("first"), await create("second")
    # Both requests ran in the test's one transaction, so now() gave them the same created_at.
    row = await session.get(Job, uuid.UUID(first["id"]), populate_existing=True)
    assert row is not None
    row.created_at -= timedelta(minutes=1)
    session.add(User(id="someone-else", is_anonymous=True))
    await session.flush()
    other = Job(
        user_id="someone-else",
        subject="general",
        level=Level.HIGH_SCHOOL,
        language="en",
        input_text="not yours",
    )
    session.add(other)
    await session.flush()
    session.add(JobRound(job_id=other.id, number=1))
    await session.flush()

    listed = (await client.get("/jobs")).json()
    assert [j["id"] for j in listed] == [second["id"], first["id"]]
    assert (await client.get(f"/jobs/{other.id}")).status_code == 404
    assert (await client.post(f"/jobs/{other.id}/accept", json={"round": 1})).status_code == 404


async def test_list_behind_api_prefix(client: httpx2.AsyncClient, create: Create):
    job = await create()
    response = await client.get("/api/jobs")
    assert response.status_code == 200
    assert response.json() == [job]


async def test_accept(
    client: httpx2.AsyncClient,
    session: AsyncSession,
    create: Create,
    dispatcher: StepDispatcher,
    agents: FakeAgents,
):
    job = await create()
    assert agents.hibernated == [uuid.UUID(job["id"])]
    response = await client.post(f"/jobs/{job['id']}/accept", json={"round": 1})
    assert response.status_code == 200
    assert (response.json()["status"], response.json()["accepted"]) == ("done", True)
    await dispatcher.drain()
    assert agents.released == [uuid.UUID(job["id"])]
    row = await session.get(Job, uuid.UUID(job["id"]), populate_existing=True)
    assert row is not None
    assert row.status == JobStatus.ACCEPTED
    assert row.review_deadline is None
    assert (await client.post(f"/jobs/{job['id']}/accept", json={"round": 1})).status_code == 409


async def test_accept_failed_job_is_409(client: httpx2.AsyncClient, create: Create):
    job = await create("fail")
    assert (await client.post(f"/jobs/{job['id']}/accept", json={"round": 1})).status_code == 409


async def test_feedback_starts_the_next_round(
    client: httpx2.AsyncClient,
    session: AsyncSession,
    create: Create,
    dispatcher: StepDispatcher,
    agents: FakeAgents,
):
    job = await create()
    response = await client.post(
        f"/jobs/{job['id']}/feedback", json={"text": " more steps ", "round": 1}
    )
    assert response.status_code == 200
    running = response.json()
    assert (running["status"], running["result"], running["round"]) == ("running", None, 2)
    await dispatcher.drain()
    assert agents.runs[-1].feedback == "more steps"
    assert agents.runs[-1].round == 2

    revised = (await client.get(f"/jobs/{job['id']}")).json()
    assert (revised["id"], revised["round"], revised["feedback"]) == (job["id"], 2, "more steps")
    assert revised["result"] == ANSWER
    finals = await session.scalars(
        select(Artifact.round)
        .where(Artifact.job_id == uuid.UUID(job["id"]), Artifact.kind == ArtifactKind.FINAL)
        .order_by(Artifact.round)
    )
    assert list(finals) == [1, 2]


async def test_feedback_on_an_expired_job(session: AsyncSession, create: Create, feedback: Create):
    job = await create()
    row = await session.get(Job, uuid.UUID(job["id"]), populate_existing=True)
    assert row is not None
    row.move_to(JobStatus.EXPIRED, datetime.now(UTC))
    await session.flush()
    assert (await feedback(job, "again"))["round"] == 2
    await session.refresh(row)
    assert row.status == JobStatus.AWAITING_REVIEW


async def test_feedback_conflicts_and_validation(client: httpx2.AsyncClient, create: Create):
    accepted, failed = await create(), await create("fail")
    await client.post(f"/jobs/{accepted['id']}/accept", json={"round": 1})
    for job in (accepted, failed):
        response = await client.post(f"/jobs/{job['id']}/feedback", json={"text": "x", "round": 1})
        assert response.status_code == 409
    response = await client.post(
        f"/jobs/{accepted['id']}/feedback", json={"text": "  ", "round": 1}
    )
    assert response.status_code == 422
    response = await client.post(f"/jobs/{uuid.uuid4()}/feedback", json={"text": "x", "round": 1})
    assert response.status_code == 404


async def test_every_status_has_a_web_status():
    assert _STATUS.keys() == set(JobStatus)


@pytest.mark.parametrize(
    ("text", "title"),
    [
        ("\n\nSecond line\nThird", "Second line"),
        ("\u001cHello", "Hello"),  # splitlines() breaks here, strip() does not trim it
        ("\u200b\nHello", "Hello"),
        ("word " * 20, ("word " * 16).rstrip() + "…"),
    ],
)
async def test_title_is_the_first_line_with_something_to_see(create: Create, text: str, title: str):
    assert (await create(text))["title"] == title


@pytest.mark.parametrize(
    "body",
    [
        b'{"text":"\\ud800"}',  # a lone surrogate, which the default 422 cannot encode
        b'{"text":NaN}',
        b"[" * 1000 + b"]" * 1000,
    ],
)
async def test_undecodable_input_is_422(client: httpx2.AsyncClient, body: bytes):
    response = await client.post(
        "/jobs", content=body, headers={"content-type": "application/json"}
    )
    assert response.status_code == 422
    assert all("input" not in error for error in response.json()["detail"])


async def test_validation_errors_are_capped(client: httpx2.AsyncClient):
    response = await client.post("/jobs", json={f"k{i}": 1 for i in range(50)})
    assert (response.status_code, len(response.json()["detail"])) == (422, MAX_ERRORS)


async def test_body_over_the_limit_is_413(client: httpx2.AsyncClient):
    response = await client.post("/jobs", json={"text": "a" * (MAX_BODY + 1)})
    assert response.status_code == 413
    assert response.json() == {"detail": "The request is too large"}


async def test_answer_seen_in_a_stale_tab_cannot_be_accepted(
    client: httpx2.AsyncClient, session: AsyncSession, create: Create, feedback: Create
):
    job = await create()
    await feedback(job)

    stale = await client.post(f"/jobs/{job['id']}/accept", json={"round": 1})
    assert stale.status_code == 409
    row = await session.get(Job, uuid.UUID(job["id"]), populate_existing=True)
    assert row is not None
    assert row.status == JobStatus.AWAITING_REVIEW
    current = await client.post(f"/jobs/{job['id']}/accept", json={"round": 2})
    assert current.status_code == 200


async def test_stale_feedback_does_not_start_a_round(
    client: httpx2.AsyncClient, create: Create, feedback: Create
):
    job = await create()
    await feedback(job)

    stale = await client.post(f"/jobs/{job['id']}/feedback", json={"text": "x", "round": 1})
    assert stale.status_code == 409
    assert (await client.get(f"/jobs/{job['id']}")).json()["round"] == 2


@pytest.mark.parametrize("status", [JobStatus.RECEIVED, JobStatus.PROCESSING])
async def test_a_job_cannot_be_reviewed_before_it_is_answered(
    client: httpx2.AsyncClient, session: AsyncSession, dispatcher: StepDispatcher, status: JobStatus
):
    job = (await client.post("/jobs", json={"text": "Solve"})).json()
    dispatcher.pending.pop().close()
    if status == JobStatus.PROCESSING:
        row = await session.get(Job, uuid.UUID(job["id"]), populate_existing=True)
        assert row is not None
        row.move_to(status, datetime.now(UTC))
        await session.flush()
    assert (await client.post(f"/jobs/{job['id']}/accept", json={"round": 1})).status_code == 409
    response = await client.post(f"/jobs/{job['id']}/feedback", json={"text": "x", "round": 1})
    assert response.status_code == 409


async def test_the_actor_is_put_away_before_the_student_can_reply(
    create: Create, agents: FakeAgents
):
    await create()
    await create("fail")
    assert agents.status_at_clean_up == [JobStatus.PROCESSING, JobStatus.PROCESSING]


async def test_an_unreachable_agent_gets_one_retry(create: Create, agents: FakeAgents):
    agents.unreachable = 1
    job = await create()
    assert job["status"] == "done"
    assert len(agents.runs) == 2


async def test_an_agent_unreachable_twice_fails_the_job(create: Create, agents: FakeAgents):
    agents.unreachable = 2
    job = await create()
    assert (job["status"], job["error"]) == ("failed", FAILED_MESSAGE)
    assert len(agents.runs) == 2
    assert agents.released == [uuid.UUID(job["id"])]


async def test_a_failed_clean_up_keeps_the_answer(
    create: Create, agents: FakeAgents, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("api.dispatcher.CLEAN_UP_DELAY", 0)
    agents.broken_clean_up = 3
    assert (await create())["result"] == ANSWER
    assert len(agents.hibernated) == 3


async def test_a_refused_clean_up_is_tried_again(
    create: Create, agents: FakeAgents, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("api.dispatcher.CLEAN_UP_DELAY", 0)
    agents.broken_clean_up = 1
    await create()
    assert len(agents.hibernated) == 2


async def test_a_failed_revision_keeps_the_last_answer(
    client: httpx2.AsyncClient, create: Create, feedback: Create, agents: FakeAgents
):
    job = await create()
    agents.unreachable = 2
    after = await feedback(job)
    assert (after["status"], after["result"], after["round"]) == ("done", ANSWER, 1)
    assert after["feedback"] is None
    assert agents.released == [uuid.UUID(job["id"])]  # the next try gets a fresh actor

    assert (await feedback(after, "try again"))["round"] == 2
    accepted = await client.post(f"/jobs/{job['id']}/accept", json={"round": 2})
    assert accepted.status_code == 200


async def test_a_stale_run_leaves_a_newer_round_alone(
    client: httpx2.AsyncClient, dispatcher: StepDispatcher, agents: FakeAgents
):
    """A second run of round 1 fails while the first one's answer is already under feedback."""
    job = (await client.post("/jobs", json={"text": "Solve"})).json()
    job_id = uuid.UUID(job["id"])
    agents.unreachable = 2

    async def overtake() -> None:
        unreachable, agents.unreachable = agents.unreachable, 0
        await dispatcher._run(job_id, 1)
        response = await client.post(f"/jobs/{job_id}/feedback", json={"text": "x", "round": 1})
        assert response.status_code == 200
        agents.unreachable = unreachable

    agents.during_solve = overtake
    await dispatcher.drain()

    after = (await client.get(f"/jobs/{job_id}")).json()
    assert (after["status"], after["round"]) == ("done", 2)
    assert agents.released == []
    assert agents.hibernated == [job_id, job_id]  # the two real answers, not the stale run


async def test_a_second_finish_changes_nothing(
    client: httpx2.AsyncClient, create: Create, dispatcher: StepDispatcher
):
    job = await create()
    await dispatcher._finish(uuid.UUID(job["id"]), 1, "another answer")
    assert (await client.get(f"/jobs/{job['id']}")).json() == job


async def test_close_stops_running_rounds(
    client: httpx2.AsyncClient, dispatcher: StepDispatcher, agents: FakeAgents
):
    job = (await client.post("/jobs", json={"text": "Solve"})).json()
    dispatcher.pending.pop().close()
    real = Dispatcher(agents, dispatcher.open_session)

    async def hang() -> None:
        await asyncio.Event().wait()

    agents.during_solve = hang
    real.start(uuid.UUID(job["id"]), 1)
    await agents.solving.wait()
    await real.close()

    assert (await client.get(f"/jobs/{job['id']}")).json()["status"] == "running"


async def test_lifespan_restarts_unfinished_rounds(
    client: httpx2.AsyncClient,
    dispatcher: StepDispatcher,
    agents: FakeAgents,
    monkeypatch: pytest.MonkeyPatch,
):
    job = (await client.post("/jobs", json={"text": "Solve"})).json()
    dispatcher.pending.pop().close()
    monkeypatch.setattr("api.main.agents_from_env", lambda: agents)
    monkeypatch.setattr("api.main.open_session", dispatcher.open_session)

    async with lifespan(app):
        await agents.solving.wait()

    assert agents.runs[0].job_id == uuid.UUID(job["id"])


async def test_lifespan_starts_without_a_database(
    agents: FakeAgents, monkeypatch: pytest.MonkeyPatch
):
    def down() -> AsyncSession:
        raise ConnectionRefusedError("no database")

    monkeypatch.setattr("api.main.agents_from_env", lambda: agents)
    monkeypatch.setattr("api.main.open_session", down)
    async with lifespan(app):
        pass


async def test_recovery_retries_until_the_database_is_back(
    client: httpx2.AsyncClient,
    dispatcher: StepDispatcher,
    agents: FakeAgents,
    monkeypatch: pytest.MonkeyPatch,
):
    job = (await client.post("/jobs", json={"text": "Solve"})).json()
    dispatcher.pending.pop().close()
    monkeypatch.setattr("api.dispatcher.RECOVERY_DELAY", 0)
    outages = iter([OSError("no database"), OSError("no database")])
    real_open = dispatcher.open_session

    def flaky() -> AsyncSession:
        if failure := next(outages, None):
            raise failure
        return real_open()

    real = Dispatcher(agents, flaky)
    await real.recover_at_start_up()
    await agents.solving.wait()
    await real.close()

    assert agents.runs[0].job_id == uuid.UUID(job["id"])


async def test_recover_restarts_unfinished_rounds(
    client: httpx2.AsyncClient,
    session: AsyncSession,
    create: Create,
    dispatcher: StepDispatcher,
    agents: FakeAgents,
):
    """Both the queued job and the one that was running when the last process died."""
    queued = (await client.post("/jobs", json={"text": "queued"})).json()
    dispatcher.pending.pop().close()  # the process died before the round started
    running = await create("running")
    finished = await create("finished")
    row = await session.get(Job, uuid.UUID(running["id"]), populate_existing=True)
    assert row is not None
    row.move_to(JobStatus.PROCESSING, datetime.now(UTC))
    await session.flush()
    session.add(JobRound(job_id=row.id, number=2, feedback="again"))
    await session.flush()
    agents.runs.clear()

    await dispatcher.recover()
    await dispatcher.drain()

    assert {(r.text, r.round, r.feedback) for r in agents.runs} == {
        ("queued", 1, None),
        ("running", 2, "again"),
    }
    assert (await client.get(f"/jobs/{queued['id']}")).json()["status"] == "done"
    assert (await client.get(f"/jobs/{running['id']}")).json()["round"] == 2
    assert (await client.get(f"/jobs/{finished['id']}")).json()["round"] == 1


async def test_a_second_run_of_a_round_changes_nothing(
    client: httpx2.AsyncClient, create: Create, dispatcher: StepDispatcher, agents: FakeAgents
):
    job = await create()
    agents.runs.clear()

    dispatcher.start(uuid.UUID(job["id"]), 1)
    await dispatcher.drain()

    assert agents.runs == []
    assert (await client.get(f"/jobs/{job['id']}")).json() == job


async def test_a_job_that_is_gone_is_skipped(
    dispatcher: StepDispatcher, agents: FakeAgents, client: httpx2.AsyncClient
):
    dispatcher.start(uuid.uuid4(), 1)
    await dispatcher.drain()
    assert agents.runs == []


async def test_ready_checks_the_database(client: httpx2.AsyncClient):
    assert (await client.get("/ready")).json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/ready", "/jobs"])
async def test_database_down_is_503(path: str):
    async def down() -> AsyncIterator[AsyncSession]:
        raise ConnectionRefusedError("no database")
        yield

    app.dependency_overrides[get_session] = down
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path)
    app.dependency_overrides.clear()
    assert response.status_code == 503
    assert isinstance(response.json()["detail"], str)
