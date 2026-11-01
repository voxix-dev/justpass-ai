"""Jobs API: students send homework, follow it, and accept the answer or ask for changes.

For now there is no auth and no agent: every job belongs to one dev user, and fake_solve() writes a
fixed answer in the same request. The response shape is what the web app's screens use.
"""

import re
import unicodedata
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints
from pydantic.alias_generators import to_camel
from sqlalchemy import Select, and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import get_session
from api.tables import Artifact, Job, JobRound, User
from shared.models import ArtifactKind, JobStatus, Level, Role

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
    round: int  # 1 for the first answer, +1 for each feedback
    feedback: str | None  # what the student asked to change in this round


router = APIRouter(prefix="/jobs", tags=["jobs"], responses=UNAVAILABLE)
Session = Annotated[AsyncSession, Depends(get_session)]

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

SAMPLE_ANSWER = r"""## Solution

We need the roots of $x^2 - 5x + 6 = 0$.

**1. Factor.** Look for two numbers that multiply to $6$ and add to $-5$: those are $-2$ and $-3$.

$$
x^2 - 5x + 6 = (x - 2)(x - 3)
$$

**2. Solve.** A product is zero when one factor is zero, so $x = 2$ or $x = 3$.

**3. Check** with the quadratic formula:

$$
x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a} = \frac{5 \pm \sqrt{25 - 24}}{2} = \frac{5 \pm 1}{2}
$$

> **Answer:** $x \in \{2, 3\}$

### Why it works
The sum of the roots is $-\tfrac{b}{a} = 5$ and the product is $\tfrac{c}{a} = 6$
(Vieta's formulas).
"""


def fake_solve(session: AsyncSession, job: Job, round_number: int) -> None:
    """Stand-in for the dispatcher and agents: finish the round at once with a fixed answer.

    The whole word "fail" in the question, ignoring case, fails the job instead.
    """
    now = datetime.now(UTC)
    job.move_to(JobStatus.PROCESSING, now)
    if re.search(r"\bfail\b", job.input_text or "", re.IGNORECASE):
        job.move_to(JobStatus.FAILED, now)
        return
    session.add(
        Artifact(
            job_id=job.id,
            round=round_number,
            role=Role.WRITER,
            kind=ArtifactKind.FINAL,
            content=SAMPLE_ANSWER,
        )
    )
    job.move_to(JobStatus.AWAITING_REVIEW, now)


def _title(text: str) -> str:
    """The first line with something to see, cut to TITLE_LENGTH."""
    line = next((line for line in text.splitlines() if _visible(line)), text).strip()
    return line if len(line) <= TITLE_LENGTH else f"{line[: TITLE_LENGTH - 1].rstrip()}…"


def _jobs() -> Select[Job, int, str | None, str | None]:
    """The dev user's jobs with their newest round and its answer.

    One statement, so a response never mixes two rounds of a job that is being revised.
    """
    newest = (
        select(func.max(JobRound.number))
        .where(JobRound.job_id == Job.id)
        .correlate(Job)
        .scalar_subquery()
    )
    return (
        select(Job, JobRound.number, JobRound.feedback, Artifact.content)
        .join(JobRound, and_(JobRound.job_id == Job.id, JobRound.number == newest))
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


def _out(job: Job, round_number: int, feedback: str | None, answer: str | None) -> JobOut:
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
async def create_job(body: JobCreate, session: Session) -> JobOut:
    """Store a new job with its first round and solve it at once (the dev user is created once)."""
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
    fake_solve(session, job, 1)
    out = await _read(session, job.id)
    await session.commit()
    return out


@router.get("/{job_id}", responses=NOT_FOUND)
async def get_job(job_id: uuid.UUID, session: Session) -> JobOut:
    """One of the dev user's jobs; 404 for a missing job or another user's."""
    return await _read(session, job_id)


async def _check_review(session: AsyncSession, job: Job, seen: int, *allowed: JobStatus) -> int:
    """Return the job's newest round; 409 if its status isn't allowed or the student saw an older
    answer than the newest (say in a second tab), so nobody accepts an answer they never read."""
    if job.status not in allowed:
        raise HTTPException(status.HTTP_409_CONFLICT, f"The job is {job.status}, not in review")
    newest = await _newest_round(session, job.id)
    if seen != newest:
        raise HTTPException(status.HTTP_409_CONFLICT, "There is a newer answer. Read that first.")
    return newest


@router.post("/{job_id}/accept", responses=NOT_FOUND | NOT_IN_REVIEW | BAD_BODY)
async def accept_job(job_id: uuid.UUID, body: Review, session: Session) -> JobOut:
    """Accept the answer under review. 409 unless the job is awaiting review on that round."""
    job = await _own_job(session, job_id)
    await _check_review(session, job, body.round, JobStatus.AWAITING_REVIEW)
    job.move_to(JobStatus.ACCEPTED, datetime.now(UTC))
    out = await _read(session, job.id)
    await session.commit()
    return out


@router.post("/{job_id}/feedback", responses=NOT_FOUND | NOT_IN_REVIEW | BAD_BODY)
async def send_feedback(job_id: uuid.UUID, body: FeedbackCreate, session: Session) -> JobOut:
    """Start the next round of the same job with the student's feedback.

    409 unless the job is awaiting review or expired on that round (accepted and failed are final).
    """
    job = await _own_job(session, job_id)
    newest = await _check_review(
        session, job, body.round, JobStatus.AWAITING_REVIEW, JobStatus.EXPIRED
    )
    session.add(JobRound(job_id=job.id, number=newest + 1, feedback=body.text))
    await session.flush()
    fake_solve(session, job, newest + 1)
    out = await _read(session, job.id)
    await session.commit()
    return out
