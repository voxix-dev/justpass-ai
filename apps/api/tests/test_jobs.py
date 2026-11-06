import asyncio
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from api.context import Agent, _lock_current  # pyright: ignore[reportPrivateUsage]
from api.context_token import InvalidToken, verify_token
from api.db import get_session
from api.dispatcher import MAX_CLAIMS, Dispatcher
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

    def forget(self) -> None:
        """Drop the round waiting to run, as a process dying before it starts would."""
        self.pending.pop().close()


@pytest.fixture(autouse=True)
def no_context_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTEXT_API_URL", "")


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


async def expire_lease(dispatcher: Dispatcher, job_id: uuid.UUID, number: int) -> None:
    async with dispatcher.open_session() as session:
        await session.execute(
            update(JobRound)
            .where(JobRound.job_id == job_id, JobRound.number == number)
            .values(lease_until=func.clock_timestamp() - text("interval '1 second'"))
        )
        await session.commit()


async def round_row(session: AsyncSession, job_id: str, number: int = 1) -> JobRound:
    return await session.get_one(JobRound, (uuid.UUID(job_id), number), populate_existing=True)


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
        "feedback", "failedFeedback",
    }  # fmt: skip
    assert job["status"] == "done"
    assert (job["round"], job["feedback"], job["error"], job["accepted"]) == (1, None, None, False)
    assert job["failedFeedback"] is None
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
    dispatcher.forget()
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


async def test_a_failed_revision_keeps_the_last_answer_and_its_own_row(
    client: httpx2.AsyncClient,
    session: AsyncSession,
    create: Create,
    feedback: Create,
    agents: FakeAgents,
):
    job = await create()
    agents.unreachable = 2
    after = await feedback(job, "lost note")
    assert (after["status"], after["result"], after["round"]) == ("done", ANSWER, 1)
    assert (after["feedback"], after["failedFeedback"]) == (None, "lost note")
    assert (await client.get("/jobs")).json() == [after]
    assert agents.released == [uuid.UUID(job["id"])]  # the next try gets a fresh actor
    failed = await session.get_one(JobRound, (uuid.UUID(job["id"]), 2), populate_existing=True)
    assert (failed.feedback, failed.failed_at is not None) == ("lost note", True)
    assert (failed.lease_id, failed.lease_until) == (None, None)

    # The student still reads round 1, and there is no round 2 to accept yet.
    stale = await client.post(f"/jobs/{job['id']}/accept", json={"round": 2})
    assert stale.status_code == 409

    retried = await feedback(after, "try again", seen=1)
    # The student's second round, the third row: a failed round's number is never reused.
    assert (retried["round"], retried["feedback"]) == (2, "try again")
    assert agents.runs[-1].round == 3
    assert (retried["result"], retried["failedFeedback"]) == (ANSWER, None)
    assert (await client.get("/jobs")).json() == [retried]
    await session.refresh(failed)
    assert (failed.feedback, failed.failed_at is not None) == ("lost note", True)
    assert (await client.post(f"/jobs/{job['id']}/accept", json={"round": 3})).status_code == 409
    accepted = await client.post(f"/jobs/{job['id']}/accept", json={"round": 2})
    assert accepted.status_code == 200
    assert accepted.json()["round"] == 2


async def test_two_failed_revisions_in_a_row_leave_no_gap_for_the_student(
    client: httpx2.AsyncClient,
    session: AsyncSession,
    create: Create,
    feedback: Create,
    agents: FakeAgents,
):
    job = await create()
    shown = [job["round"]]
    for _ in range(2):
        agents.unreachable = 2
        failed = await feedback(job, "again", seen=1)
        assert failed["failedFeedback"] == "again"
        shown.append(failed["round"])
        assert (await client.get("/jobs")).json() == [failed]
    revised = await feedback(job, "again", seen=1)
    shown.append(revised["round"])
    assert shown == [1, 1, 1, 2]
    assert revised["failedFeedback"] is None
    assert (await client.get("/jobs")).json() == [revised]

    rows = await session.execute(
        select(JobRound.number, JobRound.failed_at.is_(None))
        .where(JobRound.job_id == uuid.UUID(job["id"]))
        .order_by(JobRound.number)
    )
    assert rows.all() == [(1, True), (2, False), (3, False), (4, True)]
    assert (await client.post(f"/jobs/{job['id']}/accept", json={"round": 4})).status_code == 409
    assert (await client.post(f"/jobs/{job['id']}/accept", json={"round": 2})).status_code == 200


async def test_feedback_after_a_failed_revision_counts_the_shown_round(
    client: httpx2.AsyncClient, create: Create, feedback: Create, agents: FakeAgents
):
    job = await create()
    agents.unreachable = 2
    await feedback(job, "lost note")
    stale = await client.post(f"/jobs/{job['id']}/feedback", json={"text": "x", "round": 2})
    assert stale.status_code == 409
    again = await client.post(f"/jobs/{job['id']}/feedback", json={"text": "x", "round": 1})
    assert again.status_code == 200
    assert again.json()["round"] == 2


async def test_a_failed_first_round_has_no_failed_feedback(create: Create):
    job = await create("fail")
    assert (job["status"], job["round"], job["failedFeedback"]) == ("failed", 1, None)


async def test_a_failed_revision_can_be_left_as_it_is_and_accepted(
    client: httpx2.AsyncClient, create: Create, feedback: Create, agents: FakeAgents
):
    job = await create()
    agents.unreachable = 2
    await feedback(job)
    accepted = await client.post(f"/jobs/{job['id']}/accept", json={"round": 1})
    assert accepted.status_code == 200
    assert (accepted.json()["accepted"], accepted.json()["round"]) == (True, 1)


async def test_a_stale_run_leaves_a_newer_round_alone(
    client: httpx2.AsyncClient, dispatcher: StepDispatcher, agents: FakeAgents
):
    """A run that lost its lease fails while the run that took over is already under feedback."""
    job = (await client.post("/jobs", json={"text": "Solve"})).json()
    job_id = uuid.UUID(job["id"])
    agents.unreachable = 2

    async def overtake() -> None:
        unreachable, agents.unreachable = agents.unreachable, 0
        await expire_lease(dispatcher, job_id, 1)  # as if this run had stopped renewing
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


async def test_a_round_this_process_is_running_is_not_started_twice(
    client: httpx2.AsyncClient, dispatcher: StepDispatcher, agents: FakeAgents
):
    """Recovery that retries after an outage can meet a round that a request just started."""
    await client.post("/jobs", json={"text": "Solve"})
    await dispatcher.recover()
    await dispatcher.drain()
    assert len(agents.runs) == 1


@pytest.mark.parametrize("step", ["_begin", "_is_current", "_finish"])
async def test_a_short_database_outage_does_not_strand_the_round(
    create: Create,
    dispatcher: StepDispatcher,
    agents: FakeAgents,
    monkeypatch: pytest.MonkeyPatch,
    step: str,
):
    real = getattr(dispatcher, step)
    outages = iter([OSError("no database")])

    async def flaky(*args: Any) -> Any:
        if outage := next(outages, None):
            raise outage
        return await real(*args)

    monkeypatch.setattr("api.dispatcher.DATABASE_DELAY", 0)
    monkeypatch.setattr(dispatcher, step, flaky)
    assert (await create())["result"] == ANSWER
    assert len(agents.runs) == 1


async def test_a_commit_whose_reply_was_lost_is_not_repeated(
    create: Create,
    session: AsyncSession,
    dispatcher: StepDispatcher,
    monkeypatch: pytest.MonkeyPatch,
):
    real = dispatcher._finish
    calls = 0

    async def lost_reply(*args: Any) -> None:
        nonlocal calls
        calls += 1
        await real(*args)
        if calls == 1:
            raise OSError("connection lost after the commit")

    monkeypatch.setattr("api.dispatcher.DATABASE_DELAY", 0)
    monkeypatch.setattr(dispatcher, "_finish", lost_reply)
    job = await create()

    assert (calls, job["result"]) == (2, ANSWER)
    finals = await session.scalars(
        select(Artifact.id).where(Artifact.job_id == uuid.UUID(job["id"]))
    )
    assert len(list(finals)) == 1


async def test_other_errors_are_not_tried_forever(
    create: Create, dispatcher: StepDispatcher, monkeypatch: pytest.MonkeyPatch
):
    async def broken(*_: Any) -> None:
        raise ValueError("a bug, not an outage")

    monkeypatch.setattr(dispatcher, "_finish", broken)
    assert (await create())["status"] == "running"


async def test_a_second_finish_changes_nothing(
    client: httpx2.AsyncClient, create: Create, dispatcher: StepDispatcher
):
    job = await create()
    await dispatcher._finish(uuid.UUID(job["id"]), 1, uuid.uuid4(), "another answer")
    assert (await client.get(f"/jobs/{job['id']}")).json() == job


async def test_close_stops_running_rounds(
    client: httpx2.AsyncClient, dispatcher: StepDispatcher, agents: FakeAgents
):
    job = (await client.post("/jobs", json={"text": "Solve"})).json()
    dispatcher.forget()
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
    dispatcher.forget()
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
    dispatcher.forget()
    monkeypatch.setattr("api.dispatcher.DATABASE_DELAY", 0)
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
    dispatcher.forget()  # the process died before the round started
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


async def queue(client: httpx2.AsyncClient, dispatcher: StepDispatcher) -> uuid.UUID:
    """A new job whose round 1 nobody has started, as after a crash before the run began."""
    job = (await client.post("/jobs", json={"text": "Solve"})).json()
    dispatcher.forget()
    return uuid.UUID(job["id"])


async def test_a_round_held_by_another_run_is_not_started(
    client: httpx2.AsyncClient,
    dispatcher: StepDispatcher,
    agents: FakeAgents,
    session: AsyncSession,
):
    job_id = await queue(client, dispatcher)
    assert await dispatcher._begin(job_id, 1, uuid.uuid4()) is not None  # another process
    dispatcher.start(job_id, 1)
    await dispatcher.recover()
    await dispatcher.drain()

    assert agents.runs == []
    assert (await client.get(f"/jobs/{job_id}")).json()["status"] == "running"
    assert (await round_row(session, str(job_id))).claims == 1


async def test_a_round_whose_lease_ran_out_is_taken_over_by_the_sweep(
    client: httpx2.AsyncClient,
    dispatcher: StepDispatcher,
    agents: FakeAgents,
    session: AsyncSession,
):
    job_id = await queue(client, dispatcher)
    await dispatcher._begin(job_id, 1, uuid.uuid4())  # a process that then died
    await expire_lease(dispatcher, job_id, 1)
    await dispatcher.recover()
    await dispatcher.drain()

    assert len(agents.runs) == 1
    assert (await client.get(f"/jobs/{job_id}")).json()["result"] == ANSWER
    row = await round_row(session, str(job_id))
    assert (row.claims, row.lease_id, row.lease_until) == (2, None, None)


async def test_a_claim_whose_reply_was_lost_is_the_same_claim(
    client: httpx2.AsyncClient,
    dispatcher: StepDispatcher,
    agents: FakeAgents,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    job_id = await queue(client, dispatcher)
    real = dispatcher._begin
    calls = 0

    async def lost_reply(*args: Any) -> Any:
        nonlocal calls
        calls += 1
        run = await real(*args)
        if calls == 1:
            raise OSError("connection lost after the commit")
        return run

    monkeypatch.setattr("api.dispatcher.DATABASE_DELAY", 0)
    monkeypatch.setattr(dispatcher, "_begin", lost_reply)
    dispatcher.start(job_id, 1)
    await dispatcher.drain()

    assert (calls, len(agents.runs)) == (2, 1)
    assert (await round_row(session, str(job_id))).claims == 1
    assert (await client.get(f"/jobs/{job_id}")).json()["result"] == ANSWER


async def test_a_run_that_lost_its_lease_neither_puts_away_nor_finishes(
    client: httpx2.AsyncClient, dispatcher: StepDispatcher, agents: FakeAgents
):
    job_id = await queue(client, dispatcher)

    async def taken_over() -> None:
        await expire_lease(dispatcher, job_id, 1)
        assert await dispatcher._begin(job_id, 1, uuid.uuid4()) is not None

    agents.during_solve = taken_over
    dispatcher.start(job_id, 1)
    await dispatcher.drain()

    assert (agents.hibernated, agents.released) == ([], [])
    assert (await client.get(f"/jobs/{job_id}")).json()["status"] == "running"


async def test_a_round_claimed_too_often_is_left_alone(
    client: httpx2.AsyncClient,
    dispatcher: StepDispatcher,
    agents: FakeAgents,
    caplog: pytest.LogCaptureFixture,
):
    job_id = await queue(client, dispatcher)
    for _ in range(MAX_CLAIMS):
        assert await dispatcher._begin(job_id, 1, uuid.uuid4()) is not None
        await expire_lease(dispatcher, job_id, 1)

    await dispatcher.recover()
    dispatcher.start(job_id, 1)
    await dispatcher.drain()

    assert agents.runs == []
    assert f"was claimed {MAX_CLAIMS} times" in caplog.text
    assert (await client.get(f"/jobs/{job_id}")).json()["status"] == "running"


async def wait_for_a_renewal(
    open_session: Callable[[], AsyncSession], job_id: uuid.UUID, seconds: float = 5
) -> bool:
    """Poll until the round's expired lease has been renewed; False if not within `seconds`."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while loop.time() < deadline:
        async with open_session() as session:
            row = await round_row(session, str(job_id))
        if row.lease_until is not None and row.lease_until > datetime.now(UTC):
            return True
        await asyncio.sleep(0.05)
    return False


async def test_the_lease_is_renewed_while_the_agent_works(
    client: httpx2.AsyncClient,
    dispatcher: StepDispatcher,
    agents: FakeAgents,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    job_id = await queue(client, dispatcher)
    monkeypatch.setattr("api.dispatcher.RENEW_SECONDS", 0.2)
    renewed: list[bool] = []

    async def expire_and_wait() -> None:
        await expire_lease(dispatcher, job_id, 1)
        renewed.append(await wait_for_a_renewal(dispatcher.open_session, job_id))

    agents.during_solve = expire_and_wait
    dispatcher.start(job_id, 1)
    await dispatcher.drain()
    assert renewed == [True]


async def test_the_lease_is_renewed_while_the_actor_is_put_away(
    client: httpx2.AsyncClient,
    dispatcher: StepDispatcher,
    agents: FakeAgents,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    job_id = await queue(client, dispatcher)
    monkeypatch.setattr("api.dispatcher.RENEW_SECONDS", 0.2)
    renewed: list[bool] = []

    async def slow_hibernate(_: uuid.UUID) -> None:
        await expire_lease(dispatcher, job_id, 1)
        renewed.append(await wait_for_a_renewal(dispatcher.open_session, job_id))

    monkeypatch.setattr(agents, "hibernate", slow_hibernate)
    dispatcher.start(job_id, 1)
    await dispatcher.drain()
    assert renewed == [True]
    row = await round_row(session, str(job_id))
    assert (row.lease_id, row.lease_until) == (None, None)  # finished, and the lease given up


async def test_a_renewal_that_fails_for_any_reason_is_tried_again(
    client: httpx2.AsyncClient,
    dispatcher: StepDispatcher,
    agents: FakeAgents,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    job_id = await queue(client, dispatcher)
    monkeypatch.setattr("api.dispatcher.RENEW_SECONDS", 0.2)
    renewed: list[bool] = []

    async def break_the_first_renewal() -> None:
        await expire_lease(dispatcher, job_id, 1)
        real = dispatcher.open_session
        failed = False

        def flaky() -> AsyncSession:
            nonlocal failed
            if not failed:
                failed = True
                raise ValueError("not a database outage")
            return real()

        monkeypatch.setattr(dispatcher, "open_session", flaky)
        renewed.append(await wait_for_a_renewal(real, job_id))  # the second try works

    agents.during_solve = break_the_first_renewal
    dispatcher.start(job_id, 1)
    await dispatcher.drain()
    assert renewed == [True]


async def test_a_run_that_lost_its_lease_stops_the_agent_and_saves_nothing(
    client: httpx2.AsyncClient,
    dispatcher: StepDispatcher,
    agents: FakeAgents,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    job_id = await queue(client, dispatcher)
    monkeypatch.setattr("api.dispatcher.RENEW_SECONDS", 0.1)

    async def lose_the_lease_and_hang() -> None:
        async with dispatcher.open_session() as other:
            await other.execute(
                update(JobRound).where(JobRound.job_id == job_id).values(lease_id=uuid.uuid4())
            )
            await other.commit()
        await asyncio.Event().wait()

    agents.during_solve = lose_the_lease_and_hang
    dispatcher.start(job_id, 1)
    await asyncio.wait_for(dispatcher.drain(), 5)

    assert "Lost the lease" in caplog.text
    assert (agents.hibernated, agents.released) == ([], [])
    assert (await client.get(f"/jobs/{job_id}")).json()["status"] == "running"


async def test_every_attempt_gets_a_token_of_its_own(
    client: httpx2.AsyncClient,
    dispatcher: StepDispatcher,
    agents: FakeAgents,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("CONTEXT_API_URL", "http://api:8000")
    minted = iter(["first", "second", "third"])
    monkeypatch.setattr("api.dispatcher.mint_token", lambda *args: next(minted))
    agents.unreachable = 1
    await client.post("/jobs", json={"text": "Solve"})
    await dispatcher.drain()

    tokens = [run.context.token for run in agents.runs if run.context]
    assert tokens == ["first", "second"]


async def test_the_token_names_the_lease_that_runs_the_round(
    dispatcher: StepDispatcher, agents: FakeAgents, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("CONTEXT_API_URL", "http://api:8000")
    monkeypatch.setenv("CONTEXT_TOKEN_SECRET", "s" * 32)
    lease = uuid.uuid4()
    run = AgentRun(job_id=uuid.uuid4(), round=2, text="Solve", feedback=None)
    await dispatcher._solve(run, "alice", lease)

    context = agents.runs[0].context
    assert context is not None
    assert verify_token(context.token).lease == lease


async def test_a_release_waits_for_a_context_write_in_progress(engine: AsyncEngine):
    """Real connections: the shared test one cannot show one transaction waiting for another."""
    holder = uuid.uuid4()
    user_id = f"user-{uuid.uuid4()}"

    def new_session() -> AsyncSession:
        return AsyncSession(engine, expire_on_commit=False)

    async with new_session() as setup:
        setup.add(User(id=user_id, is_anonymous=True))
        await setup.flush()
        job = Job(
            user_id=user_id,
            status=JobStatus.PROCESSING,
            subject="math",
            level=Level.COLLEGE,
            language="en",
            input_text="Solve",
        )
        setup.add(job)
        await setup.flush()
        setup.add(JobRound(job_id=job.id, number=1, lease_id=holder, claims=1))
        await setup.commit()
    try:
        real = Dispatcher(FakeAgents(), new_session)
        async with new_session() as write:
            await _lock_current(write, Agent(job=job, round=1, lease=holder))  # the write begins
            giving_back = asyncio.create_task(real._give_back(job.id, 1, holder))
            await asyncio.sleep(0.3)
            assert not giving_back.done()  # waits for the write to commit
            await write.commit()
        await asyncio.wait_for(giving_back, 5)
        async with new_session() as check:
            row = await round_row(check, str(job.id))
        assert (row.lease_id, row.claims) == (None, 0)
    finally:
        async with new_session() as cleanup:
            await cleanup.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
            await cleanup.commit()


async def test_the_sweep_runs_until_close(
    client: httpx2.AsyncClient,
    dispatcher: StepDispatcher,
    agents: FakeAgents,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    job_id = await queue(client, dispatcher)
    await dispatcher._begin(job_id, 1, uuid.uuid4())
    await expire_lease(dispatcher, job_id, 1)
    real = Dispatcher(agents, dispatcher.open_session)

    async def hang() -> None:
        await asyncio.Event().wait()

    agents.during_solve = hang
    monkeypatch.setattr("api.dispatcher.SWEEP_SECONDS", 0.01)
    real.start_sweeping()
    await asyncio.wait_for(agents.solving.wait(), 5)
    await real.close()

    assert not [task for task in real._tasks if not task.done()]
    assert agents.runs[0].job_id == job_id


async def test_close_gives_the_round_back_without_counting_the_claim(
    client: httpx2.AsyncClient,
    dispatcher: StepDispatcher,
    agents: FakeAgents,
    session: AsyncSession,
):
    job_id = await queue(client, dispatcher)
    real = Dispatcher(agents, dispatcher.open_session)

    async def hang() -> None:
        await asyncio.Event().wait()

    agents.during_solve = hang
    real.start(job_id, 1)
    await asyncio.wait_for(agents.solving.wait(), 5)
    held = await round_row(session, str(job_id))
    assert (held.lease_id is not None, held.claims) == (True, 1)

    await real.close()

    row = await round_row(session, str(job_id))
    assert (row.lease_id, row.lease_until, row.claims) == (None, None, 0)


async def test_the_sweep_survives_an_error(
    dispatcher: StepDispatcher, monkeypatch: pytest.MonkeyPatch
):
    calls = 0
    done = asyncio.Event()

    async def flaky() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("a bug")
        done.set()

    monkeypatch.setattr("api.dispatcher.SWEEP_SECONDS", 0)
    monkeypatch.setattr(dispatcher, "recover", flaky)
    sweeping = asyncio.create_task(dispatcher._sweep_forever())
    await asyncio.wait_for(done.wait(), 5)
    sweeping.cancel()
    await asyncio.gather(sweeping, return_exceptions=True)
    assert calls == 2


async def test_the_agent_gets_a_token_for_the_context_api(
    client: httpx2.AsyncClient,
    dispatcher: StepDispatcher,
    agents: FakeAgents,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("CONTEXT_API_URL", "http://api:8000")
    monkeypatch.setenv("CONTEXT_TOKEN_SECRET", "s" * 32)
    job = (await client.post("/jobs", json={"text": "Solve"})).json()
    await dispatcher.drain()

    context = agents.runs[0].context
    assert context is not None
    assert context.url == "http://api:8000"
    claims = verify_token(context.token)
    assert (claims.job_id, claims.user_id, claims.round) == (uuid.UUID(job["id"]), "dev-user", 1)
    assert context.token not in repr(agents.runs[0])


async def test_the_agent_gets_no_context_api_without_a_url(create: Create, agents: FakeAgents):
    await create()
    assert agents.runs[0].context is None


@pytest.mark.parametrize("secret", [None, "", "short"])
async def test_lifespan_refuses_a_context_api_without_a_good_secret(
    agents: FakeAgents, monkeypatch: pytest.MonkeyPatch, secret: str | None
):
    monkeypatch.setenv("CONTEXT_API_URL", "http://api:8000")
    if secret is None:
        monkeypatch.delenv("CONTEXT_TOKEN_SECRET", raising=False)
    else:
        monkeypatch.setenv("CONTEXT_TOKEN_SECRET", secret)
    monkeypatch.setattr("api.main.agents_from_env", lambda: agents)
    with pytest.raises(InvalidToken, match="CONTEXT_TOKEN_SECRET"):
        async with lifespan(app):
            pass
