import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import get_session
from api.dispatch import get_dispatch
from api.main import app
from api.tables import Artifact, Job
from shared.models import ArtifactKind, JobStatus, Role

pytestmark = pytest.mark.anyio

NEW_JOB = {"subject": "history", "level": "high_school", "language": "en", "text": "Why WW1?"}
ALICE = {"Authorization": "Bearer dev:alice"}
BOB = {"Authorization": "Bearer dev:bob"}


@pytest.fixture
def dispatched() -> list[tuple[uuid.UUID, int]]:
    return []


@pytest.fixture
async def client(
    session: AsyncSession, dispatched: list[tuple[uuid.UUID, int]], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx2.AsyncClient]:
    monkeypatch.setenv("DEV_AUTH", "1")

    async def record(job_id: uuid.UUID, round_number: int) -> None:
        dispatched.append((job_id, round_number))

    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_dispatch] = lambda: record
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def create_job(client: httpx2.AsyncClient, headers: dict[str, str] = ALICE) -> uuid.UUID:
    response = await client.post("/jobs", json=NEW_JOB, headers=headers)
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


async def put_in_review(session: AsyncSession, job_id: uuid.UUID, at: datetime) -> None:
    """What the dispatcher and the agents will do: run round 1 and save its final answer."""
    job = await session.get_one(Job, job_id)
    job.move_to(JobStatus.PROCESSING, at)
    job.move_to(JobStatus.AWAITING_REVIEW, at)
    session.add(
        Artifact(job_id=job_id, round=1, role=Role.WRITER, kind=ArtifactKind.FINAL, content="# A")
    )
    await session.flush()


async def test_create_and_read_a_job(client: httpx2.AsyncClient, dispatched: list[object]):
    job_id = await create_job(client)
    assert dispatched == [(job_id, 1)]
    job = (await client.get(f"/jobs/{job_id}", headers=ALICE)).json()
    assert job["status"] == "received"
    assert (job["round"], job["result"], job["input_text"]) == (1, None, "Why WW1?")
    listed = (await client.get("/jobs", headers=ALICE)).json()
    assert [j["id"] for j in listed] == [str(job_id)]


async def test_users_never_see_each_others_jobs(client: httpx2.AsyncClient, session: AsyncSession):
    job_id = await create_job(client, ALICE)
    await put_in_review(session, job_id, datetime.now(UTC))
    assert (await client.get(f"/jobs/{job_id}", headers=BOB)).status_code == 404
    assert (await client.get("/jobs", headers=BOB)).json() == []


async def test_a_job_in_review_shows_its_answer_and_deadline(
    client: httpx2.AsyncClient, session: AsyncSession
):
    job_id = await create_job(client)
    await put_in_review(session, job_id, datetime.now(UTC))
    job = (await client.get(f"/jobs/{job_id}", headers=ALICE)).json()
    assert (job["status"], job["result"]) == ("awaiting_review", {"round": 1, "markdown": "# A"})
    assert job["review_deadline"]


@pytest.mark.parametrize(
    ("dev_auth", "token"),
    [("1", None), ("1", "alice"), ("1", "dev:"), ("1", "dev:a/b"), ("", "dev:a")],
)
async def test_requests_without_a_valid_token_are_rejected(
    client: httpx2.AsyncClient, monkeypatch: pytest.MonkeyPatch, dev_auth: str, token: str | None
):
    monkeypatch.setenv("DEV_AUTH", dev_auth)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = await client.get("/jobs", headers=headers)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_polling_returns_jobs_updated_since_with_some_overlap(client: httpx2.AsyncClient):
    job_id = await create_job(client)
    updated_at = datetime.fromisoformat(
        (await client.get(f"/jobs/{job_id}", headers=ALICE)).json()["updated_at"]
    )

    async def poll(since: str) -> list[object]:
        response = await client.get("/jobs", params={"updated_since": since}, headers=ALICE)
        return response.json()

    assert len(await poll((updated_at + timedelta(seconds=3)).isoformat())) == 1
    assert await poll((updated_at + timedelta(seconds=10)).isoformat()) == []
    assert len(await poll("0001-01-01T00:00:00Z")) == 1  # overlap would underflow: no lower bound
    assert "detail" in await poll("2026-10-23T12:00:00")  # no time zone: rejected


@pytest.mark.parametrize(
    "body",
    [
        NEW_JOB | {"text": "   "},
        NEW_JOB | {"level": "kindergarten"},
        NEW_JOB | {"language": "English"},
        NEW_JOB | {"photo_keys": []},
    ],
)
async def test_invalid_jobs_are_rejected(client: httpx2.AsyncClient, body: dict[str, object]):
    assert (await client.post("/jobs", json=body, headers=ALICE)).status_code == 422
