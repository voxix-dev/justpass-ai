"""Context API: the only way an agent reads and writes a student's data. One route per tool.

Every request carries a token (api.context_token) for one round of one job. The job decides whose
data the agent sees, so it never sees another student's, and it reads artifacts of its job only.
The API is for agents: it is left out of the OpenAPI contract, and the web app never calls it.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.context_token import InvalidToken, verify_token
from api.db import get_session
from api.tables import Artifact, Job, JobRound, MemoryDoc, search_memory
from shared.context import (
    ArtifactContent,
    ArtifactRef,
    GetProfile,
    GetSubjectMemory,
    GetTask,
    HistoryItem,
    ListHistory,
    Memory,
    Profile,
    ReadArtifact,
    RoundInfo,
    SearchHistory,
    Task,
    UpdateMemory,
    WriteArtifact,
    WrittenArtifact,
)
from shared.models import ArtifactKind, JobStatus

router = APIRouter(prefix="/v1/context", include_in_schema=False)
Session = Annotated[AsyncSession, Depends(get_session)]
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Agent:
    """Who is calling: the job (and so the student) and the round the token is for."""

    job: Job
    round: int


async def _agent(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Session,
) -> Agent:
    """401 unless the token is valid and its job still exists for its user."""
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Missing, invalid or expired token",
        {"WWW-Authenticate": "Bearer"},
    )
    try:
        claims = verify_token(credentials.credentials if credentials else "")
    except InvalidToken:
        raise unauthorized from None
    job = await session.scalar(
        select(Job).where(Job.id == claims.job_id, Job.user_id == claims.user_id)
    )
    if job is None:
        raise unauthorized
    return Agent(job=job, round=claims.round)


Caller = Annotated[Agent, Depends(_agent)]


async def _lock_current(session: AsyncSession, agent: Agent) -> Job:
    """Lock the job and return it, or 409 unless the token's round is the one it is processing.

    The dispatcher locks the job to finish a round, so a write cannot land after the answer.
    """
    job = await session.scalar(
        select(Job)
        .where(Job.id == agent.job.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    newest = await session.scalar(
        select(func.max(JobRound.number)).where(JobRound.job_id == agent.job.id)
    )
    if job is None or job.status != JobStatus.PROCESSING or newest != agent.round:
        raise HTTPException(status.HTTP_409_CONFLICT, "This round is no longer being worked on")
    return job


async def _memory(session: AsyncSession, user_id: str, subject: str | None) -> str:
    body = await session.scalar(
        select(MemoryDoc.body).where(
            MemoryDoc.user_id == user_id, MemoryDoc.subject.is_not_distinct_from(subject)
        )
    )
    return body or ""


@router.post("/get_task")
async def get_task(args: GetTask, agent: Caller, session: Session) -> Task:
    """The question and every round: feedback, outcome and artifacts (failed rounds too)."""
    job = agent.job
    rounds = await session.scalars(
        select(JobRound).where(JobRound.job_id == job.id).order_by(JobRound.number)
    )
    artifacts = await session.scalars(
        select(Artifact).where(Artifact.job_id == job.id).order_by(Artifact.created_at, Artifact.id)
    )
    written: dict[int, list[Artifact]] = {}
    for artifact in artifacts:
        written.setdefault(artifact.round, []).append(artifact)
    infos: list[RoundInfo] = []
    for round_row in rounds:
        mine = written.get(round_row.number, [])
        if any(a.kind == ArtifactKind.FINAL for a in mine):
            outcome = "answered"
        else:
            outcome = "failed" if round_row.failed_at else "running"
        infos.append(
            RoundInfo(
                number=round_row.number,
                feedback=round_row.feedback,
                outcome=outcome,
                artifacts=[ArtifactRef(id=a.id, kind=a.kind, title=a.title) for a in mine],
            )
        )
    return Task(
        job_id=job.id,
        subject=job.subject,
        level=job.level,
        language=job.language,
        text=job.input_text or "",
        round=agent.round,
        rounds=infos,
    )


@router.post("/read_artifact")
async def read_artifact(args: ReadArtifact, agent: Caller, session: Session) -> ArtifactContent:
    """Any artifact of this job, from any round. Another job's answers 404, like a missing one."""
    artifact = await session.scalar(
        select(Artifact).where(Artifact.id == args.artifact_id, Artifact.job_id == agent.job.id)
    )
    if artifact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")
    return ArtifactContent(
        artifact_id=artifact.id,
        kind=artifact.kind,
        round=artifact.round,
        title=artifact.title,
        content_md=artifact.content,
    )


@router.post("/write_artifact")
async def write_artifact(args: WriteArtifact, agent: Caller, session: Session) -> WrittenArtifact:
    """Save an artifact in the token's round. The dispatcher saves the answer (kind final)."""
    await _lock_current(session, agent)
    artifact = Artifact(
        job_id=agent.job.id,
        round=agent.round,
        kind=args.kind,
        title=args.title,
        content=args.content_md,
    )
    session.add(artifact)
    await session.commit()
    return WrittenArtifact(artifact_id=artifact.id)


@router.post("/get_profile")
async def get_profile(args: GetProfile, agent: Caller, session: Session) -> Profile:
    job = agent.job
    return Profile(
        level=job.level,
        language=job.language,
        subject=job.subject,
        profile_md=await _memory(session, job.user_id, None),
    )


@router.post("/get_subject_memory")
async def get_subject_memory(args: GetSubjectMemory, agent: Caller, session: Session) -> Memory:
    subject = args.subject or agent.job.subject
    return Memory(subject=subject, content_md=await _memory(session, agent.job.user_id, subject))


@router.post("/update_memory")
async def update_memory(args: UpdateMemory, agent: Caller, session: Session) -> Memory:
    """scope=profile ignores `subject`; scope=subject defaults to the job's subject."""
    job = await _lock_current(session, agent)
    subject = (args.subject or job.subject) if args.scope == "subject" else None
    await session.execute(
        insert(MemoryDoc)
        .values(user_id=job.user_id, subject=subject, body=args.content_md)
        .on_conflict_do_update(
            constraint="uq_memory_docs_user_id_subject",
            set_={"body": args.content_md, "updated_at": func.clock_timestamp()},
        )
    )
    await session.commit()
    return Memory(subject=subject, content_md=args.content_md)


@router.post("/list_history")
async def list_history(args: ListHistory, agent: Caller, session: Session) -> list[HistoryItem]:
    """The student's other jobs, newest first."""
    jobs = await session.scalars(
        select(Job)
        .where(Job.user_id == agent.job.user_id, Job.id != agent.job.id)
        .order_by(Job.created_at.desc(), Job.id)
        .limit(args.limit)
    )
    return [
        HistoryItem(
            job_id=job.id,
            subject=job.subject,
            status=job.status,
            created_at=job.created_at,
            grade=job.grade,
            grade_comment=job.grade_comment,
        )
        for job in jobs
    ]


@router.post("/search_history")
async def search_history(args: SearchHistory, agent: Caller, session: Session) -> list[Memory]:
    """Search the student's memory docs."""
    docs = await search_memory(session, agent.job.user_id, args.query)
    return [Memory(subject=doc.subject, content_md=doc.body) for doc in docs]
