"""Jobs API: students send homework, follow it, and accept the answer or ask for changes.

For now there is no auth: every job belongs to one dev user. The agent works in the background (see
dispatcher.py), so a new job comes back queued and the web app polls for the answer. The response
shape is what the web app's screens use.
"""

import unicodedata
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints
from pydantic.alias_generators import to_camel
from sqlalchemy import ScalarSelect, Select, and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from api.db import get_session
from api.dispatcher import Dispatcher
from api.tables import Artifact, Job, JobRound, User
from shared.models import ArtifactKind, JobStatus, Level

LIST_LIMIT = 100
TITLE_LENGTH = 80


def _visible(text: str) -> bool:
    """Whether the text has a letter, number, punctuation mark or symbol a student can see."""
    return any(unicodedata.category(c)[0] in "LNPS" for c in text)


def _readable(text: str) -> str:
    """Refuse NUL, which Postgres text cannot hold, and text that would show up blank."""
    if "\x00" in text or not _visible(text):
        raise ValueError("Write it out in visible text.")
    return text


TaskText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000),
    AfterValidator(_readable),
]
FeedbackText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000),
    AfterValidator(_readable),
]


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: TaskText


class Review(BaseModel):
    """Accepting an answer. `round` is the one the student saw: a newer answer gets a 409."""

    model_config = ConfigDict(extra="forbid")

    round: int


class FeedbackCreate(Review):
    text: FeedbackText


class Problem(BaseModel):
    """An error response, as FastAPI's HTTPException writes it."""

    detail: str


Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {404: {"model": Problem, "description": "No such job for this user"}}
NOT_IN_REVIEW: Responses = {
    409: {"model": Problem, "description": "The job is not in review, or its answer has changed"}
}
BAD_BODY: Responses = {
    400: {"model": Problem, "description": "The body cannot be parsed"},
    413: {"model": Problem, "description": "The body is too large"},
}
UNAVAILABLE: Responses = {503: {"model": Problem, "description": "The database cannot be reached"}}


class JobOut(BaseModel):
    """A job as the web app shows it. `done` covers awaiting_review, accepted and expired."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: uuid.UUID
    status: Literal["queued", "running", "done", "failed"]
    created_at: datetime
    title: str  # first line of the question
    text: str
    result: str | None  # Markdown with $inline$ and $$display$$ math, set when done
    error: str | None
    accepted: bool
    round: int  # 1 for the first answer, +1 for each revision that worked
    feedback: str | None  # what the student asked to change in this round
    # What the student asked for in a newer round that failed, so the web app can offer it again.
    failed_feedback: str | None


def get_dispatcher(request: Request) -> Dispatcher:
    """The dispatcher main.py made at start-up. Tests replace this with their own."""
    return request.app.state.dispatcher


router = APIRouter(prefix="/jobs", tags=["jobs"], responses=UNAVAILABLE)
Session = Annotated[AsyncSession, Depends(get_session)]
Dispatch = Annotated[Dispatcher, Depends(get_dispatcher)]

# shortcut: no auth yet. Replace with the Firebase user once sign-in lands.
DEV_USER = "dev-user"

_STATUS: dict[JobStatus, Literal["queued", "running", "done", "failed"]] = {
    JobStatus.RECEIVED: "queued",
    JobStatus.PROCESSING: "running",
    JobStatus.AWAITING_REVIEW: "done",
    JobStatus.ACCEPTED: "done",
    JobStatus.EXPIRED: "done",
    JobStatus.FAILED: "failed",
}
FAILED_MESSAGE = "The solver could not read this question."


def _title(text: str) -> str:
    """The first line with something to see, cut to TITLE_LENGTH."""
    line = next((line for line in text.splitlines() if _visible(line)), text).strip()
    return line if len(line) <= TITLE_LENGTH else f"{line[: TITLE_LENGTH - 1].rstrip()}…"


def _shown_round(job_id: Any) -> ScalarSelect[int]:
    """The number of the round the student reads: the newest that did not fail, or 1 if none did.

    A revision that failed keeps its row (with what the agent wrote in it), but the student goes on
    reading the earlier answer.
    """
    return (
        select(func.coalesce(func.max(JobRound.number).filter(JobRound.failed_at.is_(None)), 1))
        .where(JobRound.job_id == job_id)
        .correlate(Job)
        .scalar_subquery()
    )


def _shown_ordinal(job_id: Any) -> ScalarSelect[int]:
    """The round as the student counts it: 1, 2, 3 over the rounds that did not fail.

    A failed revision leaves a gap in `job_rounds.number`, which stays internal. The student sees no
    gap, and the retry of a failed revision is the same "round 2" again.
    """
    return (
        select(func.greatest(func.count().filter(JobRound.failed_at.is_(None)), 1))
        .where(JobRound.job_id == job_id)
        .correlate(Job)
        .scalar_subquery()
    )


def _jobs() -> Select[Job, int, str | None, str | None, str | None]:
    """The dev user's jobs with the round the student counts, its answer and the feedback of a newer
    round that failed.

    One statement, so a response never mixes two rounds of a job that is being revised.
    """
    shown = _shown_round(Job.id)
    newest = (
        select(func.max(JobRound.number))
        .where(JobRound.job_id == Job.id)
        .correlate(Job)
        .scalar_subquery()
    )
    failed = aliased(JobRound)
    return (
        select(Job, _shown_ordinal(Job.id), JobRound.feedback, Artifact.content, failed.feedback)
        .join(JobRound, and_(JobRound.job_id == Job.id, JobRound.number == shown))
        .outerjoin(
            failed,
            and_(
                failed.job_id == Job.id,
                failed.number == newest,
                failed.number > shown,
                failed.failed_at.is_not(None),
            ),
        )
        .outerjoin(
            Artifact,
            and_(
                Artifact.job_id == Job.id,
                Artifact.round == JobRound.number,
                Artifact.kind == ArtifactKind.FINAL,
            ),
        )
        .where(Job.user_id == DEV_USER)
    )


def _out(
    job: Job,
    round_number: int,
    feedback: str | None,
    answer: str | None,
    failed_feedback: str | None,
) -> JobOut:
    """A row of _jobs() as the web app shows it."""
    text = job.input_text or ""
    shown = _STATUS[job.status]
    return JobOut(
        id=job.id,
        status=shown,
        created_at=job.created_at,
        title=_title(text),
        text=text,
        result=answer if shown == "done" else None,
        error=FAILED_MESSAGE if shown == "failed" else None,
        accepted=job.status == JobStatus.ACCEPTED,
        round=round_number,
        feedback=feedback,
        failed_feedback=failed_feedback,
    )


async def _read(session: AsyncSession, job_id: uuid.UUID) -> JobOut:
    """One job as the web app shows it; 404 for a missing job or another user's."""
    row = (await session.execute(_jobs().where(Job.id == job_id))).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return _out(*row)


async def _newest_round(session: AsyncSession, job_id: uuid.UUID) -> int:
    """The number of the job's newest round. Every job has at least one."""
    number = await session.scalar(
        select(func.max(JobRound.number)).where(JobRound.job_id == job_id)
    )
    assert number is not None
    return number


async def _own_job(session: AsyncSession, job_id: uuid.UUID) -> Job:
    """Load and lock one of the dev user's jobs, so two requests cannot change it at once."""
    job = await session.scalar(
        select(Job)
        .where(Job.id == job_id, Job.user_id == DEV_USER)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job


@router.get("")
async def list_jobs(session: Session) -> list[JobOut]:
    """The dev user's newest jobs first, at most LIST_LIMIT."""
    rows = await session.execute(_jobs().order_by(Job.created_at.desc(), Job.id).limit(LIST_LIMIT))
    return [_out(*row) for row in rows]


@router.post("", status_code=status.HTTP_201_CREATED, responses=BAD_BODY)
async def create_job(body: JobCreate, session: Session, dispatcher: Dispatch) -> JobOut:
    """Store a new job with its first round and queue it (the dev user is created once)."""
    await session.execute(
        insert(User).values(id=DEV_USER, is_anonymous=False).on_conflict_do_nothing()
    )
    # shortcut: the web app does not ask for these yet.
    job = Job(
        user_id=DEV_USER,
        subject="general",
        level=Level.HIGH_SCHOOL,
        language="en",
        input_text=body.text,
    )
    session.add(job)
    await session.flush()
    session.add(JobRound(job_id=job.id, number=1))
    await session.flush()
    out = await _read(session, job.id)
    await session.commit()
    dispatcher.start(job.id, 1)
    return out


@router.get("/{job_id}", responses=NOT_FOUND)
async def get_job(job_id: uuid.UUID, session: Session) -> JobOut:
    """One of the dev user's jobs; 404 for a missing job or another user's."""
    return await _read(session, job_id)


async def _check_review(session: AsyncSession, job: Job, seen: int, *allowed: JobStatus) -> None:
    """409 if the job's status isn't allowed or the student saw an older answer than the shown
    one (say in a second tab), so nobody accepts an answer they never read.

    `seen` is the round as the Jobs API shows it, not the row's number.
    """
    if job.status not in allowed:
        raise HTTPException(status.HTTP_409_CONFLICT, f"The job is {job.status}, not in review")
    if seen != await session.scalar(select(_shown_ordinal(job.id))):
        raise HTTPException(status.HTTP_409_CONFLICT, "There is a newer answer. Read that first.")


@router.post("/{job_id}/accept", responses=NOT_FOUND | NOT_IN_REVIEW | BAD_BODY)
async def accept_job(
    job_id: uuid.UUID, body: Review, session: Session, dispatcher: Dispatch
) -> JobOut:
    """Accept the answer and schedule agent release after committing.

    Raise 404 for a missing job and 409 unless it is awaiting review on the submitted round.
    """
    job = await _own_job(session, job_id)
    await _check_review(session, job, body.round, JobStatus.AWAITING_REVIEW)
    job.move_to(JobStatus.ACCEPTED, datetime.now(UTC))
    out = await _read(session, job.id)
    await session.commit()
    dispatcher.release(job.id)
    return out


@router.post("/{job_id}/feedback", responses=NOT_FOUND | NOT_IN_REVIEW | BAD_BODY)
async def send_feedback(
    job_id: uuid.UUID, body: FeedbackCreate, session: Session, dispatcher: Dispatch
) -> JobOut:
    """Queue the next round with the student's feedback and return the running job.

    Raise 404 for a missing job and 409 unless it is awaiting review or expired on the submitted
    round (accepted and failed are final).
    """
    job = await _own_job(session, job_id)
    await _check_review(session, job, body.round, JobStatus.AWAITING_REVIEW, JobStatus.EXPIRED)
    number = (
        await _newest_round(session, job.id) + 1
    )  # internal: never reuses a failed round's number
    session.add(JobRound(job_id=job.id, number=number, feedback=body.text))
    job.move_to(JobStatus.PROCESSING, datetime.now(UTC))
    await session.flush()
    out = await _read(session, job.id)
    await session.commit()
    dispatcher.start(job.id, number)
    return out
