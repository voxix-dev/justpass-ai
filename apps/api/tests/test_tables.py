import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.tables import (
    REVIEW_WINDOW,
    Artifact,
    InvalidTransition,
    Job,
    JobRound,
    MemoryDoc,
    User,
    search_memory,
)
from shared.models import ArtifactKind, JobStatus, Level, Role

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def new_job(user_id: str = "uid", **fields: object) -> Job:
    defaults: dict[str, object] = {
        "status": JobStatus.RECEIVED,
        "subject": "history",
        "level": Level.HIGH_SCHOOL,
        "language": "en",
        "input_text": "Why did World War I start?",
    }
    return Job(user_id=user_id, **(defaults | fields))


async def add_user(session: AsyncSession) -> User:
    user = User(id=f"uid-{uuid.uuid4().hex}", is_anonymous=True)
    session.add(user)
    await session.flush()
    return user


async def add_job_with_round(session: AsyncSession) -> Job:
    job = new_job((await add_user(session)).id)
    session.add(job)
    await session.flush()
    session.add(JobRound(job_id=job.id, number=1))
    await session.flush()
    return job


def test_move_to_follows_shared_transitions_and_sets_the_review_deadline():
    job = new_job()
    with pytest.raises(InvalidTransition):
        job.move_to(JobStatus.AWAITING_REVIEW, NOW)
    job.move_to(JobStatus.PROCESSING, NOW)
    job.move_to(JobStatus.AWAITING_REVIEW, NOW)
    assert job.review_deadline == NOW + REVIEW_WINDOW
    job.move_to(JobStatus.EXPIRED, NOW)  # late feedback can still restart it
    job.move_to(JobStatus.PROCESSING, NOW)
    job.move_to(JobStatus.AWAITING_REVIEW, NOW + REVIEW_WINDOW)
    assert job.review_deadline == NOW + 2 * REVIEW_WINDOW
    job.move_to(JobStatus.ACCEPTED, NOW)
    assert job.review_deadline is None
    with pytest.raises(InvalidTransition):
        job.move_to(JobStatus.PROCESSING, NOW)


@pytest.mark.anyio
async def test_job_needs_text_or_a_photo(session: AsyncSession):
    user = await add_user(session)
    session.add(new_job(user.id, input_text=None, photo_keys=["users/u/uploads/1.jpg"]))
    await session.flush()
    session.add(new_job(user.id, input_text=None))
    with pytest.raises(IntegrityError, match="ck_jobs_has_input"):
        await session.flush()


@pytest.mark.anyio
async def test_a_job_in_review_always_has_a_deadline(session: AsyncSession):
    session.add(new_job((await add_user(session)).id, status=JobStatus.AWAITING_REVIEW))
    with pytest.raises(IntegrityError, match="ck_jobs_deadline_iff_awaiting_review"):
        await session.flush()


@pytest.mark.anyio
async def test_updated_at_moves_when_a_job_changes(session: AsyncSession):
    job = new_job((await add_user(session)).id)
    session.add(job)
    await session.flush()
    before = job.updated_at
    job.move_to(JobStatus.PROCESSING, NOW)
    await session.flush()
    await session.refresh(job)
    assert job.updated_at > before


@pytest.mark.anyio
async def test_only_later_rounds_have_feedback(session: AsyncSession):
    job = await add_job_with_round(session)
    session.add(JobRound(job_id=job.id, number=2))
    with pytest.raises(IntegrityError, match="ck_job_rounds_feedback_iff_not_first"):
        await session.flush()


@pytest.mark.anyio
async def test_a_round_has_at_most_one_final_artifact(session: AsyncSession):
    job = await add_job_with_round(session)
    final = {"job_id": job.id, "round": 1, "role": Role.WRITER, "kind": ArtifactKind.FINAL}
    session.add(Artifact(**final, content="# Answer"))
    await session.flush()
    session.add(Artifact(**final, storage_key="runs/x/final.md"))
    with pytest.raises(IntegrityError, match="uq_artifacts_one_final_per_round"):
        await session.flush()


@pytest.mark.anyio
async def test_artifact_has_content_or_a_storage_key_not_both(session: AsyncSession):
    job = await add_job_with_round(session)
    notes = {"job_id": job.id, "round": 1, "role": Role.RESEARCH, "kind": ArtifactKind.DRAFT}
    session.add(Artifact(**notes, content="notes", storage_key="runs/x/notes.md"))
    with pytest.raises(IntegrityError, match="ck_artifacts_content_xor_storage_key"):
        await session.flush()


@pytest.mark.anyio
async def test_deleting_a_user_deletes_all_their_data(session: AsyncSession):
    job = await add_job_with_round(session)
    session.add_all(
        [
            Artifact(
                job_id=job.id, round=1, role=Role.WRITER, kind=ArtifactKind.FINAL, content="x"
            ),
            MemoryDoc(user_id=job.user_id, body="profile"),
        ]
    )
    await session.flush()
    await session.delete(await session.get_one(User, job.user_id))
    await session.flush()
    assert await session.scalar(select(func.count()).where(Job.id == job.id)) == 0
    for table in (JobRound, Artifact):
        count = select(func.count()).select_from(table).where(table.job_id == job.id)
        assert await session.scalar(count) == 0
    docs = select(func.count()).select_from(MemoryDoc).where(MemoryDoc.user_id == job.user_id)
    assert await session.scalar(docs) == 0


@pytest.mark.anyio
async def test_one_profile_doc_per_user(session: AsyncSession):
    user = await add_user(session)
    session.add(MemoryDoc(user_id=user.id, body="Writes informally."))
    await session.flush()
    session.add(MemoryDoc(user_id=user.id, body="A second profile."))
    with pytest.raises(IntegrityError, match="uq_memory_docs_user_id_subject"):
        await session.flush()


@pytest.mark.anyio
async def test_search_finds_only_this_users_docs(session: AsyncSession):
    alice, bob = await add_user(session), await add_user(session)
    session.add_all(
        [
            MemoryDoc(user_id=alice.id, subject="physics", body="Loses points for missing units."),
            MemoryDoc(user_id=alice.id, body="Writes informally."),
            MemoryDoc(user_id=bob.id, subject="physics", body="Always forgets units."),
        ]
    )
    await session.flush()
    found = await search_memory(session, alice.id, "units")
    assert [doc.subject for doc in found] == ["physics"]
    assert await search_memory(session, alice.id, "chemistry") == []
