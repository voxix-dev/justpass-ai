import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from api.agent_token import mint_token
from api.db import get_session
from api.main import app
from api.tables import Artifact, Job, JobRound, MemoryDoc, User
from shared.models import ArtifactKind, Level, Role
from shared.roles import PRIVATE_DATA_TOOLS, ROLE_TOOLS

pytestmark = pytest.mark.anyio

CONTEXT_TOOLS = PRIVATE_DATA_TOOLS | {"read_artifact", "write_artifact"}
BODIES: dict[str, dict[str, str]] = {
    "get_profile": {},
    "get_subject_memory": {"subject": "math"},
    "list_history": {},
    "search_history": {"query": "fractions"},
    "update_memory": {"scope": "profile", "content_md": "Likes worked examples."},
    "write_artifact": {"title": "Notes", "content_md": "# Notes"},
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


async def add_job(session: AsyncSession, user_id: str) -> Job:
    if await session.get(User, user_id) is None:
        session.add(User(id=user_id, is_anonymous=True))
        await session.flush()
    job = Job(user_id=user_id, subject="math", level=Level.COLLEGE, language="en", input_text="?")
    session.add(job)
    await session.flush()
    session.add(JobRound(job_id=job.id, number=1))
    await session.flush()
    return job


async def add_artifact(session: AsyncSession, job: Job) -> str:
    artifact = Artifact(
        job_id=job.id, round=1, role=Role.WRITER, kind=ArtifactKind.DRAFT, content="# Draft"
    )
    session.add(artifact)
    await session.flush()
    return str(artifact.id)


def token(job: Job, role: Role, ttl: timedelta = timedelta(minutes=5)) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(job.id, 1, role, ttl)}"}


async def call(
    client: httpx2.AsyncClient, tool: str, headers: dict[str, str], body: object = None
) -> httpx2.Response:
    return await client.post(f"/v1/context/{tool}", json=body or BODIES[tool], headers=headers)


def test_there_is_a_route_for_each_context_tool():
    paths = app.openapi()["paths"]
    assert {p.removeprefix("/v1/context/") for p in paths if p.startswith("/v1/")} == CONTEXT_TOOLS
    assert frozenset().union(*ROLE_TOOLS.values()) >= CONTEXT_TOOLS


@pytest.mark.parametrize("role", list(Role))
async def test_each_role_can_call_only_its_tools(
    client: httpx2.AsyncClient, session: AsyncSession, role: Role
):
    job = await add_job(session, "alice")
    bodies = {**BODIES, "read_artifact": {"artifact_id": await add_artifact(session, job)}}
    for tool in sorted(CONTEXT_TOOLS):
        response = await call(client, tool, token(job, role), bodies[tool])
        expected = 200 if tool in ROLE_TOOLS[role] else 403
        assert response.status_code == expected, (tool, response.text)


async def test_research_role_reaches_no_private_data(
    client: httpx2.AsyncClient, session: AsyncSession
):
    job = await add_job(session, "alice")
    for tool in PRIVATE_DATA_TOOLS:
        assert (await call(client, tool, token(job, Role.RESEARCH))).status_code == 403


async def test_bad_tokens_are_rejected(client: httpx2.AsyncClient, session: AsyncSession):
    job = await add_job(session, "alice")
    for headers in [
        {},
        {"Authorization": "Bearer dev:alice"},
        token(job, Role.WRITER, ttl=timedelta(seconds=-1)),
    ]:
        assert (await call(client, "write_artifact", headers)).status_code == 401, headers
    other_job = await add_job(session, "alice")
    headers = token(other_job, Role.WRITER)
    await session.delete(other_job)
    await session.flush()
    assert (await call(client, "write_artifact", headers)).status_code == 401


async def test_an_agent_never_sees_another_students_data(
    client: httpx2.AsyncClient, session: AsyncSession
):
    alice, bob = await add_job(session, "alice"), await add_job(session, "bob")
    session.add_all(
        [
            MemoryDoc(user_id="bob", subject=None, body="Bob writes tersely."),
            MemoryDoc(user_id="bob", subject="math", body="Bob struggles with fractions."),
        ]
    )
    await session.flush()
    orchestrator = token(alice, Role.ORCHESTRATOR)
    assert (await call(client, "get_profile", orchestrator)).json()["profile_md"] == ""
    assert (await call(client, "get_subject_memory", orchestrator)).json()["content_md"] == ""
    assert (await call(client, "search_history", orchestrator)).json() == []
    assert (await call(client, "list_history", orchestrator)).json() == []
    assert (await call(client, "update_memory", token(alice, Role.CURATOR))).status_code == 200
    bobs_profile = await call(client, "get_profile", token(bob, Role.ORCHESTRATOR))
    assert bobs_profile.json()["profile_md"] == "Bob writes tersely."


async def test_search_history_finds_the_right_doc(
    client: httpx2.AsyncClient, session: AsyncSession
):
    job = await add_job(session, "alice")
    session.add_all(
        [
            MemoryDoc(user_id="alice", subject="math", body="Mixes up fractions and decimals."),
            MemoryDoc(user_id="alice", subject="history", body="Wrote on the French Revolution."),
        ]
    )
    await session.flush()
    found = await call(
        client, "search_history", token(job, Role.ORCHESTRATOR), {"query": "revolution"}
    )
    assert found.json() == [{"subject": "history", "content_md": "Wrote on the French Revolution."}]


async def test_memory_is_replaced_and_read_back(client: httpx2.AsyncClient, session: AsyncSession):
    job = await add_job(session, "alice")
    curator = token(job, Role.CURATOR)
    for content in ["Slow at algebra.", "Good at algebra now."]:
        body = {"scope": "subject", "subject": "math", "content_md": content}
        assert (await call(client, "update_memory", curator, body)).status_code == 200
    memory = (await call(client, "get_subject_memory", curator)).json()
    assert memory == {"subject": "math", "content_md": "Good at algebra now."}
    no_subject = {"scope": "subject", "content_md": "x"}
    assert (await call(client, "update_memory", curator, no_subject)).status_code == 422
    profile = {"scope": "profile", "subject": "math", "content_md": "Prefers short answers."}
    assert (await call(client, "update_memory", curator, profile)).json()["subject"] is None
    assert (await call(client, "get_subject_memory", curator)).json() == memory
    profile_md = (await call(client, "get_profile", curator)).json()["profile_md"]
    assert profile_md == "Prefers short answers."


async def test_an_agent_reads_artifacts_of_its_own_job_only(
    client: httpx2.AsyncClient, session: AsyncSession
):
    alice, bob = await add_job(session, "alice"), await add_job(session, "bob")
    body = {"artifact_id": await add_artifact(session, bob)}
    assert (await call(client, "read_artifact", token(alice, Role.WRITER), body)).status_code == 404
    assert (await call(client, "read_artifact", token(bob, Role.WRITER), body)).status_code == 200


async def test_artifacts_round_trip_within_the_job(
    client: httpx2.AsyncClient, session: AsyncSession
):
    job = await add_job(session, "alice")
    written = await call(client, "write_artifact", token(job, Role.RESEARCH))
    artifact_id = written.json()["artifact_id"]
    read = await call(
        client, "read_artifact", token(job, Role.REVIEW), {"artifact_id": artifact_id}
    )
    assert read.json() == {
        "artifact_id": artifact_id,
        "title": "Notes",
        "role": "research",
        "kind": "research_notes",
        "round": 1,
        "content_md": "# Notes",
    }
    missing = await call(client, "read_artifact", token(job, Role.REVIEW), {"artifact_id": "nope"})
    assert missing.status_code == 404
    draft = await call(client, "write_artifact", token(job, Role.WRITER))
    assert (await session.get_one(Artifact, uuid.UUID(draft.json()["artifact_id"]))).kind == "draft"
    nul = {"title": "Notes", "content_md": "scraped\x00text"}
    assert (await call(client, "write_artifact", token(job, Role.RESEARCH), nul)).status_code == 422


async def test_list_history_shows_the_students_other_jobs(
    client: httpx2.AsyncClient, session: AsyncSession
):
    older, current = await add_job(session, "alice"), await add_job(session, "alice")
    older.grade = "B+"
    await session.flush()
    history = (await call(client, "list_history", token(current, Role.ORCHESTRATOR))).json()
    assert [(h["job_id"], h["grade"]) for h in history] == [(str(older.id), "B+")]
