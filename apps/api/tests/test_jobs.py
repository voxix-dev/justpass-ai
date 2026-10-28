import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import get_session
from api.jobs import _STATUS, FAILED_MESSAGE, SAMPLE_ANSWER
from api.main import app
from api.tables import Artifact, Job, JobRound, User
from shared.models import ArtifactKind, JobStatus, Level

pytestmark = pytest.mark.anyio


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[httpx2.AsyncClient]:
    async def request_session() -> AsyncIterator[AsyncSession]:
        """Each request gets its own session, as in production, so a missing commit is lost."""
        async with AsyncSession(
            session.bind, join_transaction_mode="create_savepoint", expire_on_commit=False
        ) as s:
            yield s

    app.dependency_overrides[get_session] = request_session
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def create(client: httpx2.AsyncClient, text: str = "Solve x^2 - 5x + 6 = 0") -> dict:
    response = await client.post("/jobs", json={"text": text})
    assert response.status_code == 201
    return response.json()


async def test_create_solves_the_job(client: httpx2.AsyncClient, session: AsyncSession):
    job = await create(client, f"  {'a' * 90}\nsecond line  ")
    assert job.keys() == {
        "id", "status", "createdAt", "title", "text", "result", "error", "accepted", "round",
        "feedback",
    }  # fmt: skip
    assert job["status"] == "done"
    assert (job["round"], job["feedback"], job["error"], job["accepted"]) == (1, None, None, False)
    assert job["result"] == SAMPLE_ANSWER
    assert job["text"] == f"{'a' * 90}\nsecond line"
    assert job["title"] == "a" * 80
    row = await session.get(Job, uuid.UUID(job["id"]), populate_existing=True)
    assert row is not None
    assert row.status == JobStatus.AWAITING_REVIEW
    assert row.review_deadline is not None


@pytest.mark.parametrize(
    "body", [{"text": ""}, {"text": "   "}, {"text": "a\u0000"}, {"text": "hi", "extra": 1}, {}]
)
async def test_create_rejects_bad_bodies(client: httpx2.AsyncClient, body: dict):
    assert (await client.post("/jobs", json=body)).status_code == 422


@pytest.mark.parametrize("text", ["please fail", "FAIL now", "it will Fail."])
async def test_the_word_fail_fails_the_job(client: httpx2.AsyncClient, text: str):
    job = await create(client, text)
    assert (job["status"], job["error"], job["result"]) == ("failed", FAILED_MESSAGE, None)


async def test_fail_must_be_a_whole_word(client: httpx2.AsyncClient):
    assert (await create(client, "a failure"))["status"] == "done"


async def test_get_job(client: httpx2.AsyncClient):
    job = await create(client)
    response = await client.get(f"/jobs/{job['id']}")
    assert response.status_code == 200
    assert response.json() == job


async def test_get_unknown_job_is_404(client: httpx2.AsyncClient):
    response = await client.get(f"/jobs/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}


async def test_list_shows_only_the_dev_users_jobs_newest_first(
    client: httpx2.AsyncClient, session: AsyncSession
):
    first, second = await create(client, "first"), await create(client, "second")
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
    assert (await client.post(f"/jobs/{other.id}/accept")).status_code == 404


async def test_list_behind_api_prefix(client: httpx2.AsyncClient):
    job = await create(client)
    response = await client.get("/api/jobs")
    assert response.status_code == 200
    assert response.json() == [job]


async def test_accept(client: httpx2.AsyncClient, session: AsyncSession):
    job = await create(client)
    response = await client.post(f"/jobs/{job['id']}/accept")
    assert response.status_code == 200
    assert (response.json()["status"], response.json()["accepted"]) == ("done", True)
    row = await session.get(Job, uuid.UUID(job["id"]), populate_existing=True)
    assert row is not None
    assert row.status == JobStatus.ACCEPTED
    assert row.review_deadline is None
    assert (await client.post(f"/jobs/{job['id']}/accept")).status_code == 409


async def test_accept_failed_job_is_409(client: httpx2.AsyncClient):
    job = await create(client, "fail")
    assert (await client.post(f"/jobs/{job['id']}/accept")).status_code == 409


async def test_feedback_starts_the_next_round(client: httpx2.AsyncClient, session: AsyncSession):
    job = await create(client)
    response = await client.post(f"/jobs/{job['id']}/feedback", json={"text": " more steps "})
    assert response.status_code == 200
    revised = response.json()
    assert (revised["id"], revised["round"], revised["feedback"]) == (job["id"], 2, "more steps")
    assert revised["result"].startswith("_Revised after your feedback: “more steps”_\n\n")
    assert revised["result"].endswith(SAMPLE_ANSWER)
    finals = await session.scalars(
        select(Artifact.round)
        .where(Artifact.job_id == uuid.UUID(job["id"]), Artifact.kind == ArtifactKind.FINAL)
        .order_by(Artifact.round)
    )
    assert list(finals) == [1, 2]


async def test_feedback_on_an_expired_job(client: httpx2.AsyncClient, session: AsyncSession):
    job = await create(client)
    row = await session.get(Job, uuid.UUID(job["id"]), populate_existing=True)
    assert row is not None
    row.move_to(JobStatus.EXPIRED, datetime.now(UTC))
    await session.flush()
    response = await client.post(f"/jobs/{job['id']}/feedback", json={"text": "again"})
    assert (response.status_code, response.json()["round"]) == (200, 2)
    await session.refresh(row)
    assert row.status == JobStatus.AWAITING_REVIEW


async def test_feedback_conflicts_and_validation(client: httpx2.AsyncClient):
    accepted, failed = await create(client), await create(client, "fail")
    await client.post(f"/jobs/{accepted['id']}/accept")
    for job in (accepted, failed):
        response = await client.post(f"/jobs/{job['id']}/feedback", json={"text": "x"})
        assert response.status_code == 409
    response = await client.post(f"/jobs/{accepted['id']}/feedback", json={"text": "  "})
    assert response.status_code == 422
    response = await client.post(f"/jobs/{uuid.uuid4()}/feedback", json={"text": "x"})
    assert response.status_code == 404


async def test_every_status_has_a_web_status():
    assert _STATUS.keys() == set(JobStatus)
