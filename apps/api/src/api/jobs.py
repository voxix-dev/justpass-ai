"""Jobs API: students send homework, follow it, and accept the answer or ask for changes.

For now there is no auth and no agent: every job belongs to one dev user, and fake_solve() writes a
fixed answer in the same request. The response shape is what the web app's screens use.
"""

import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, StringConstraints
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import get_session
from api.tables import Artifact, Job, JobRound, User
from shared.models import ArtifactKind, JobStatus, Level, Role

LIST_LIMIT = 100
TITLE_LENGTH = 80


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]


class FeedbackCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000)]


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


router = APIRouter(prefix="/jobs", tags=["jobs"])
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


def fake_solve(session: AsyncSession, job: Job, round_number: int, feedback: str | None) -> None:
    """Stand-in for the dispatcher and agents: finish the round at once with a fixed answer.

    The whole word "fail" in the question, ignoring case, fails the job instead.
    """
    now = datetime.now(UTC)
    job.move_to(JobStatus.PROCESSING, now)
    if re.search(r"\bfail\b", job.input_text or "", re.IGNORECASE):
        job.move_to(JobStatus.FAILED, now)
        return
    answer = (
        f"_Revised after your feedback: “{feedback}”_\n\n{SAMPLE_ANSWER}"
        if feedback
        else SAMPLE_ANSWER
    )
    session.add(
        Artifact(
            job_id=job.id,
            round=round_number,
            role=Role.WRITER,
            kind=ArtifactKind.FINAL,
            content=answer,
        )
    )
    job.move_to(JobStatus.AWAITING_REVIEW, now)


async def _out(session: AsyncSession, jobs: list[Job]) -> list[JobOut]:
    """Add each job's newest round and newest final answer, in two queries for all jobs."""
    ids = [j.id for j in jobs]
    rounds = {
        r.job_id: r
        for r in await session.scalars(
            select(JobRound).where(JobRound.job_id.in_(ids)).order_by(JobRound.number)
        )
    }
    finals = {
        a.job_id: a.content
        for a in await session.scalars(
            select(Artifact)
            .where(Artifact.job_id.in_(ids), Artifact.kind == ArtifactKind.FINAL)
            .order_by(Artifact.round)
        )
    }
    out: list[JobOut] = []
    for job in jobs:
        text = job.input_text or ""
        shown = _STATUS[job.status]
        out.append(
            JobOut(
                id=job.id,
                status=shown,
                created_at=job.created_at,
                title=text.split("\n")[0][:TITLE_LENGTH],
                text=text,
                result=finals.get(job.id) if shown == "done" else None,
                error=FAILED_MESSAGE if shown == "failed" else None,
                accepted=job.status == JobStatus.ACCEPTED,
                round=rounds[job.id].number,
                feedback=rounds[job.id].feedback,
            )
        )
    return out


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
    jobs = await session.scalars(
        select(Job)
        .where(Job.user_id == DEV_USER)
        .order_by(Job.created_at.desc(), Job.id)
        .limit(LIST_LIMIT)
    )
    return await _out(session, list(jobs))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_job(body: JobCreate, session: Session) -> JobOut:
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
    fake_solve(session, job, 1, None)
    await session.commit()
    await session.refresh(job)  # created_at comes from the database
    return (await _out(session, [job]))[0]


@router.get("/{job_id}")
async def get_job(job_id: uuid.UUID, session: Session) -> JobOut:
    job = await session.scalar(select(Job).where(Job.id == job_id, Job.user_id == DEV_USER))
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return (await _out(session, [job]))[0]


@router.post("/{job_id}/accept")
async def accept_job(job_id: uuid.UUID, session: Session) -> JobOut:
    """Accept the answer under review. 409 unless the job is awaiting review."""
    job = await _own_job(session, job_id)
    if job.status != JobStatus.AWAITING_REVIEW:
        raise HTTPException(status.HTTP_409_CONFLICT, f"The job is {job.status}, not in review")
    job.move_to(JobStatus.ACCEPTED, datetime.now(UTC))
    await session.commit()
    return (await _out(session, [job]))[0]


@router.post("/{job_id}/feedback")
async def send_feedback(job_id: uuid.UUID, body: FeedbackCreate, session: Session) -> JobOut:
    """Start the next round of the same job with the student's feedback.

    409 unless the job is awaiting review or expired (accepted and failed are final).
    """
    job = await _own_job(session, job_id)
    if job.status not in (JobStatus.AWAITING_REVIEW, JobStatus.EXPIRED):
        raise HTTPException(status.HTTP_409_CONFLICT, f"The job is {job.status}, not in review")
    last = await session.scalar(
        select(JobRound.number)
        .where(JobRound.job_id == job.id)
        .order_by(JobRound.number.desc())
        .limit(1)
    )
    number = (last or 0) + 1
    session.add(JobRound(job_id=job.id, number=number, feedback=body.text))
    await session.flush()
    fake_solve(session, job, number, body.text)
    await session.commit()
    return (await _out(session, [job]))[0]
