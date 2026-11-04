import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.context_token import mint_token
from api.db import get_session
from api.main import app
from api.tables import Artifact, Job, JobRound, MemoryDoc, User
from shared.models import ArtifactKind, JobStatus, Level

pytestmark = pytest.mark.anyio

BODIES: dict[str, dict[str, Any]] = {
    "get_task": {},
    "get_profile": {},
    "get_subject_memory": {},
    "list_history": {},
    "search_history": {"query": "fractions"},
    "update_memory": {"scope": "profile", "content_md": "Likes worked examples."},
    "read_artifact": {"artifact_id": str(uuid.uuid4())},
    "write_artifact": {"kind": "draft", "title": "Notes", "content_md": "# Notes"},
}


@pytest.fixture(autouse=True)
def secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTEXT_TOKEN_SECRET", "s" * 32)


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[httpx2.AsyncClient]:
    app.dependency_overrides[get_session] = lambda: session
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def add_job(
    session: AsyncSession, user_id: str, status: JobStatus = JobStatus.PROCESSING, **fields: Any
) -> Job:
    if await session.get(User, user_id) is None:
        session.add(User(id=user_id, is_anonymous=True))
        await session.flush()
    values: dict[str, Any] = {
        "subject": "math",
        "level": Level.COLLEGE,
        "language": "en",
        "input_text": "Solve x",
    } | fields
    job = Job(user_id=user_id, status=JobStatus.RECEIVED, **values)
    session.add(job)
    await session.flush()
    session.add(JobRound(job_id=job.id, number=1))
    await session.flush()
    if status != JobStatus.RECEIVED:
        job.move_to(JobStatus.PROCESSING, job.created_at)
        if status != JobStatus.PROCESSING:
            job.move_to(status, job.created_at)
        await session.flush()
    return job


async def add_artifact(
    session: AsyncSession, job: Job, kind: ArtifactKind = ArtifactKind.DRAFT, round: int = 1
) -> Artifact:
    artifact = Artifact(job_id=job.id, round=round, kind=kind, title="Draft", content="# Draft")
    session.add(artifact)
    await session.flush()
    return artifact


def auth(job: Job, round: int = 1, **kwargs: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(job.id, job.user_id, round, **kwargs)}"}


async def call(
    client: httpx2.AsyncClient, tool: str, headers: dict[str, str], **body: Any
) -> httpx2.Response:
    return await client.post(f"/v1/context/{tool}", json=body or BODIES[tool], headers=headers)


async def test_the_context_api_is_not_in_the_contract(client: httpx2.AsyncClient):
    schema = (await client.get("/openapi.json")).json()
    assert not [path for path in schema["paths"] if "context" in path]


@pytest.mark.parametrize("tool", BODIES)
async def test_every_tool_needs_a_valid_token(
    client: httpx2.AsyncClient, session: AsyncSession, tool: str
):
    job = await add_job(session, "alice")
    for headers in (
        {},
        {"Authorization": "Bearer nonsense"},
        {"Authorization": "Basic abc"},
        auth(job, ttl=timedelta(seconds=-1)),
    ):
        response = await call(client, tool, headers)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("tool", BODIES)
async def test_a_token_for_a_deleted_job_or_another_user_is_refused(
    client: httpx2.AsyncClient, session: AsyncSession, tool: str
):
    job = await add_job(session, "alice")
    await add_job(session, "bob")
    wrong_user = {"Authorization": f"Bearer {mint_token(job.id, 'bob', 1)}"}
    assert (await call(client, tool, wrong_user)).status_code == 401
    deleted = auth(job)
    await session.delete(job)
    await session.flush()
    assert (await call(client, tool, deleted)).status_code == 401


async def test_get_task(client: httpx2.AsyncClient, session: AsyncSession):
    job = await add_job(session, "alice", input_text="Solve x^2 = 4")
    first = await add_artifact(session, job, ArtifactKind.FINAL)
    session.add(JobRound(job_id=job.id, number=2, feedback="more steps"))
    await session.flush()
    stray = await add_artifact(session, job, ArtifactKind.DRAFT, round=2)
    failed = await session.get_one(JobRound, (job.id, 2))
    failed.failed_at = job.created_at
    session.add(JobRound(job_id=job.id, number=4, feedback="try again"))
    await session.flush()

    response = await call(client, "get_task", auth(job, 4))
    assert response.status_code == 200
    assert response.json() == {
        "job_id": str(job.id),
        "subject": "math",
        "level": "college",
        "language": "en",
        "text": "Solve x^2 = 4",
        "round": 4,
        "rounds": [
            {
                "number": 1,
                "feedback": None,
                "outcome": "answered",
                "artifacts": [{"id": str(first.id), "kind": "final", "title": "Draft"}],
            },
            {
                "number": 2,
                "feedback": "more steps",
                "outcome": "failed",
                "artifacts": [{"id": str(stray.id), "kind": "draft", "title": "Draft"}],
            },
            {"number": 4, "feedback": "try again", "outcome": "running", "artifacts": []},
        ],
    }


async def test_read_artifact_from_any_round_of_the_job(
    client: httpx2.AsyncClient, session: AsyncSession
):
    job = await add_job(session, "alice")
    artifact = await add_artifact(session, job, ArtifactKind.FINAL)
    response = await call(client, "read_artifact", auth(job), artifact_id=str(artifact.id))
    assert response.json() == {
        "artifact_id": str(artifact.id),
        "kind": "final",
        "round": 1,
        "title": "Draft",
        "content_md": "# Draft",
    }


async def test_read_artifact_of_another_job_is_404(
    client: httpx2.AsyncClient, session: AsyncSession
):
    mine, theirs = await add_job(session, "alice"), await add_job(session, "bob")
    other = await add_artifact(session, theirs)
    same_user = await add_job(session, "alice")
    sibling = await add_artifact(session, same_user)
    for artifact in (other, sibling):
        response = await call(client, "read_artifact", auth(mine), artifact_id=str(artifact.id))
        assert response.status_code == 404
    assert (await call(client, "read_artifact", auth(mine))).status_code == 404
    bad = await call(client, "read_artifact", auth(mine), artifact_id="not-a-uuid")
    assert bad.status_code == 422


async def test_write_artifact_saves_in_the_tokens_round(
    client: httpx2.AsyncClient, session: AsyncSession
):
    job = await add_job(session, "alice")
    response = await call(client, "write_artifact", auth(job))
    assert response.status_code == 200
    saved = await session.get_one(Artifact, uuid.UUID(response.json()["artifact_id"]))
    assert (saved.job_id, saved.round, saved.kind) == (job.id, 1, ArtifactKind.DRAFT)
    assert (saved.title, saved.content) == ("Notes", "# Notes")


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "final", "title": "x", "content_md": "x"},
        {"kind": "draft", "title": "x" * 201, "content_md": "x"},
        {"kind": "draft", "title": "x", "content_md": "x" * 100_001},
        {"kind": "draft", "title": "x\u0000", "content_md": "x"},
        {"kind": "draft", "title": "x", "content_md": "x\u0000"},
        {"kind": "poem", "title": "x", "content_md": "x"},
    ],
)
async def test_write_artifact_refuses_bad_bodies(
    client: httpx2.AsyncClient, session: AsyncSession, body: dict[str, str]
):
    job = await add_job(session, "alice")
    assert (await call(client, "write_artifact", auth(job), **body)).status_code == 422


@pytest.mark.parametrize("tool", ["write_artifact", "update_memory"])
@pytest.mark.parametrize(
    "status", [JobStatus.RECEIVED, JobStatus.AWAITING_REVIEW, JobStatus.FAILED]
)
async def test_writes_are_refused_unless_the_job_is_processing(
    client: httpx2.AsyncClient, session: AsyncSession, tool: str, status: JobStatus
):
    job = await add_job(session, "alice", status)
    assert (await call(client, tool, auth(job))).status_code == 409


@pytest.mark.parametrize("tool", ["write_artifact", "update_memory"])
async def test_writes_are_refused_for_a_round_that_is_not_the_newest(
    client: httpx2.AsyncClient, session: AsyncSession, tool: str
):
    job = await add_job(session, "alice")
    session.add(JobRound(job_id=job.id, number=2, feedback="again"))
    await session.flush()
    assert (await call(client, tool, auth(job, 1))).status_code == 409
    assert (await call(client, tool, auth(job, 2))).status_code == 200
    assert (await call(client, tool, auth(job, 3))).status_code == 409


async def test_get_profile(client: httpx2.AsyncClient, session: AsyncSession):
    job = await add_job(session, "alice", language="pl")
    session.add(MemoryDoc(user_id="alice", body="Writes informally."))
    session.add(MemoryDoc(user_id="alice", subject="math", body="Weak at fractions."))
    await session.flush()
    response = await call(client, "get_profile", auth(job))
    assert response.json() == {
        "level": "college",
        "language": "pl",
        "subject": "math",
        "profile_md": "Writes informally.",
    }


async def test_get_subject_memory(client: httpx2.AsyncClient, session: AsyncSession):
    job = await add_job(session, "alice")
    await add_job(session, "bob")
    session.add(MemoryDoc(user_id="alice", subject="math", body="Weak at fractions."))
    session.add(MemoryDoc(user_id="bob", subject="math", body="Not alice."))
    await session.flush()
    mine = await call(client, "get_subject_memory", auth(job))
    assert mine.json() == {"subject": "math", "content_md": "Weak at fractions."}
    empty = await call(client, "get_subject_memory", auth(job), subject="history")
    assert empty.json() == {"subject": "history", "content_md": ""}


async def test_update_memory_upserts_the_profile_and_subject_docs(
    client: httpx2.AsyncClient, session: AsyncSession
):
    job = await add_job(session, "alice")
    for text in ("first", "second"):
        response = await call(client, "update_memory", auth(job), scope="profile", content_md=text)
        assert response.json() == {"subject": None, "content_md": text}
    await call(client, "update_memory", auth(job), scope="subject", content_md="math notes")
    await call(
        client, "update_memory", auth(job), scope="subject", subject="art", content_md="art notes"
    )
    await call(client, "update_memory", auth(job), scope="subject", content_md="math notes 2")
    docs = await session.execute(
        select(MemoryDoc.subject, MemoryDoc.body).where(MemoryDoc.user_id == "alice")
    )
    assert sorted(docs.all(), key=lambda row: row[0] or "") == [
        (None, "second"),
        ("art", "art notes"),
        ("math", "math notes 2"),
    ]


@pytest.mark.parametrize(
    "body",
    [
        {"scope": "profile", "content_md": "x" * 10_001},
        {"scope": "profile", "content_md": "x\u0000"},
        {"scope": "subject", "subject": "x\u0000", "content_md": "x"},
        {"scope": "subject", "subject": "", "content_md": "x"},
        {"scope": "team", "content_md": "x"},
    ],
)
async def test_update_memory_refuses_bad_bodies(
    client: httpx2.AsyncClient, session: AsyncSession, body: dict[str, str]
):
    job = await add_job(session, "alice")
    assert (await call(client, "update_memory", auth(job), **body)).status_code == 422


async def test_list_history_shows_the_students_other_jobs_newest_first(
    client: httpx2.AsyncClient, session: AsyncSession
):
    current = await add_job(session, "alice")
    older = await add_job(session, "alice", subject="history", grade="B", grade_comment="ok")
    older.created_at -= timedelta(days=1)
    await add_job(session, "bob")
    await session.flush()

    listed = (await call(client, "list_history", auth(current))).json()
    assert [item["job_id"] for item in listed] == [str(older.id)]
    assert listed[0]["grade"] == "B"
    assert (await call(client, "list_history", auth(current), limit=0)).status_code == 422
    assert (await call(client, "list_history", auth(current), limit=51)).status_code == 422


async def test_search_history_stays_within_one_student(
    client: httpx2.AsyncClient, session: AsyncSession
):
    job = await add_job(session, "alice")
    await add_job(session, "bob")
    session.add(MemoryDoc(user_id="alice", subject="math", body="Struggles with fractions."))
    session.add(MemoryDoc(user_id="bob", subject="math", body="Fractions are easy for bob."))
    await session.flush()
    found = (await call(client, "search_history", auth(job))).json()
    assert found == [{"subject": "math", "content_md": "Struggles with fractions."}]
    assert (await call(client, "search_history", auth(job), query="x" * 201)).status_code == 422
    assert (await call(client, "search_history", auth(job), query="a\u0000")).status_code == 422


async def test_an_agent_sees_nothing_of_another_student(
    client: httpx2.AsyncClient, session: AsyncSession
):
    alice, bob = (
        await add_job(session, "alice", input_text="alice's"),
        await add_job(session, "bob"),
    )
    await add_artifact(session, bob)
    session.add(MemoryDoc(user_id="bob", body="bob's profile"))
    await session.flush()
    task = (await call(client, "get_task", auth(alice))).json()
    assert task["text"] == "alice's"
    assert task["rounds"][0]["artifacts"] == []
    assert (await call(client, "get_profile", auth(alice))).json()["profile_md"] == ""
    assert (await call(client, "list_history", auth(alice))).json() == []
