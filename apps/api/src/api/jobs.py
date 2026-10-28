"""Jobs API: students create homework jobs, follow them, and accept the result or ask for changes.

A user only ever sees their own jobs: another user's job answers 404, the same as a missing one.
"""

import uuid
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import AwareDatetime, BaseModel, ConfigDict, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.current_user import CurrentUser, current_user
from api.db import get_session
from api.dispatch import Dispatch, get_dispatch
from api.tables import Artifact, Job, JobRound, User
from shared.models import ArtifactKind, JobStatus, Level

# Clients poll "updated since the newest updated_at I have"; rows can commit a moment after their
# updated_at, so the answer also repeats this much time before it.
POLL_OVERLAP = timedelta(seconds=5)
LIST_LIMIT = 100


def _text(max_length: int) -> StringConstraints:
    """Return constraints for stripped text of 1 through max_length characters."""
    return StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length)


class JobFields(BaseModel):
    """What every job has, however the homework was sent."""

    subject: Annotated[str, _text(100)]
    level: Level
    language: Annotated[str, StringConstraints(pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$")]  # en, pt-BR


class JobCreate(JobFields):
    """A job sent as text. Photo jobs get their own upload route, which also calls add_job()."""

    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, _text(20_000)]


class JobSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: JobStatus
    subject: str
    level: Level
    language: str
    # Accept or send feedback before this. Set only while the job is awaiting_review.
    review_deadline: datetime | None
    created_at: datetime
    updated_at: datetime


class JobResult(BaseModel):
    round: int
    markdown: str  # math as KaTeX


class JobDetail(JobSummary):
    input_text: str | None
    photo_count: int
    round: int  # 1 for the first answer, +1 for each feedback
    # The newest answer. While a feedback round runs, this is still the previous round's answer.
    result: JobResult | None


router = APIRouter(prefix="/jobs", tags=["jobs"])
Session = Annotated[AsyncSession, Depends(get_session)]
Me = Annotated[CurrentUser, Depends(current_user)]
Dispatcher = Annotated[Dispatch, Depends(get_dispatch)]


async def add_job(
    session: AsyncSession,
    user: CurrentUser,
    fields: JobFields,
    *,
    input_text: str | None,
    photo_keys: list[str],
) -> Job:
    """Add and flush a job and its first round, returning the job without committing.

    Create the user row or update its is_anonymous flag. input_text may be None for photo jobs;
    photo_keys contains Cloud Storage object keys. If input_text is None and photo_keys is
    empty, the database constraint raises IntegrityError. Session and database errors propagate;
    the caller manages committing or rolling back.
    """
    await session.execute(
        insert(User)
        .values(id=user.uid, is_anonymous=user.is_anonymous)
        .on_conflict_do_update(index_elements=[User.id], set_={"is_anonymous": user.is_anonymous})
    )
    job = Job(
        user_id=user.uid,
        subject=fields.subject,
        level=fields.level,
        language=fields.language,
        input_text=input_text,
        photo_keys=photo_keys,
    )
    session.add(job)
    await session.flush()
    session.add(JobRound(job_id=job.id, number=1))
    await session.flush()
    return job


async def _own_job(session: AsyncSession, user: CurrentUser, job_id: uuid.UUID) -> Job:
    """Return the user's job, raising HTTPException (404) if missing or owned by someone else.

    Session and database errors propagate.
    """
    job = await session.scalar(select(Job).where(Job.id == job_id, Job.user_id == user.uid))
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return job


async def _current_round(session: AsyncSession, job_id: uuid.UUID) -> int:
    """Return the highest round number, or None if the job has no rounds.

    Session and database errors propagate.
    """
    query = select(func.max(JobRound.number)).where(JobRound.job_id == job_id)
    return (await session.execute(query)).scalar_one()


async def _detail(session: AsyncSession, job: Job) -> JobDetail:
    """Return job details with the latest round number and newest final artifact's inline answer.

    The answer may be from an earlier round. result is None if no final artifact exists or the
    newest one has no inline content. Session, database, and Pydantic validation errors propagate.
    """
    final = await session.scalar(
        select(Artifact)
        .where(Artifact.job_id == job.id, Artifact.kind == ArtifactKind.FINAL)
        .order_by(Artifact.round.desc())
        .limit(1)
    )
    # The final answer is Markdown stored in the row, never a file in storage.
    result = None
    if final is not None and final.content is not None:
        result = JobResult(round=final.round, markdown=final.content)
    return JobDetail(
        **JobSummary.model_validate(job).model_dump(),
        input_text=job.input_text,
        photo_count=len(job.photo_keys),
        round=await _current_round(session, job.id),
        result=result,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_job(
    body: JobCreate, user: Me, session: Session, dispatch: Dispatcher
) -> JobDetail:
    """Commit a text job and its first round, dispatch it, and return its details.

    Session, database, dispatch, and Pydantic validation errors propagate. Failures after the
    commit leave the job and its first round saved.
    """
    job = await add_job(session, user, body, input_text=body.text, photo_keys=[])
    await session.commit()
    await dispatch(job.id, 1)
    return await _detail(session, job)


@router.get("")
async def list_jobs(
    user: Me, session: Session, updated_since: AwareDatetime | None = None
) -> list[JobSummary]:
    """My jobs, most recently changed first: the newest 100, or all changed since `updated_since`.

    To poll for changes, pass the newest `updated_at` you have seen as `updated_since`. The answer
    includes jobs updated strictly after five seconds before that timestamp, with no count limit,
    so replace jobs you already have by id. If subtracting the overlap underflows the date range,
    return all of the user's jobs. HTTP requests require a timezone-aware timestamp.

    Session, database, and Pydantic validation errors propagate.
    """
    query = select(Job).where(Job.user_id == user.uid).order_by(Job.updated_at.desc())
    if updated_since is None:
        query = query.limit(LIST_LIMIT)
    else:
        # An updated_since at the start of the date range can't go back further: no lower bound.
        with suppress(OverflowError):
            query = query.where(Job.updated_at > updated_since - POLL_OVERLAP)
    return [JobSummary.model_validate(job) for job in await session.scalars(query)]


@router.get("/{job_id}")
async def get_job(job_id: uuid.UUID, user: Me, session: Session) -> JobDetail:
    """Return the user's job details, including the latest available final answer.

    Raise HTTPException (404) if the job is missing or belongs to someone else. Session, database,
    and Pydantic validation errors propagate.
    """
    return await _detail(session, await _own_job(session, user, job_id))
