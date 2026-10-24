"""Context API: the only way agents read and write a student's data. One route per tool.

Every request carries an agent token (api.agent_token) naming one job round and one role. The
role may call only its tools in shared.roles; any other tool answers 403. The job decides whose
data the agent sees, so it never sees another student's, and it reads artifacts of its job only.
"""

import uuid
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.agent_token import InvalidToken, verify_token
from api.db import get_session
from api.tables import Artifact, Job, MemoryDoc, search_memory
from shared.context import (
    ArtifactContent,
    GetProfile,
    GetSubjectMemory,
    HistoryItem,
    ListHistory,
    Memory,
    Profile,
    ReadArtifact,
    SearchHistory,
    UpdateMemory,
    WriteArtifact,
    WrittenArtifact,
)
from shared.models import ArtifactKind, Role
from shared.roles import ROLE_TOOLS

router = APIRouter(prefix="/v1/context", tags=["context (agents only)"])
Session = Annotated[AsyncSession, Depends(get_session)]
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Agent:
    job: Job
    round: int
    role: Role


def allowed(tool: str) -> Any:
    """Depends() on the agent calling `tool`: 401 without a valid token, 403 if its role lacks
    the tool. Every route here takes one."""

    async def check(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
        session: Session,
    ) -> Agent:
        try:
            claims = verify_token(credentials.credentials if credentials else "")
        except InvalidToken:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "missing, invalid or expired agent token"
            ) from None
        if tool not in ROLE_TOOLS[claims.role]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"{claims.role} may not call {tool}")
        job = await session.get(Job, claims.job_id)
        if job is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "the token's job no longer exists")
        return Agent(job=job, round=claims.round, role=claims.role)

    return Depends(check)


def _check_text(*texts: str | None) -> None:
    """Postgres cannot store NUL characters, so refuse them rather than fail with a 500."""
    if any(text and "\x00" in text for text in texts):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "text contains a NUL character")


async def _memory(session: AsyncSession, user_id: str, subject: str | None) -> str:
    body = await session.scalar(
        select(MemoryDoc.body).where(
            MemoryDoc.user_id == user_id, MemoryDoc.subject.is_not_distinct_from(subject)
        )
    )
    return body or ""


@router.post("/get_profile")
async def get_profile(
    args: GetProfile, agent: Annotated[Agent, allowed("get_profile")], session: Session
) -> Profile:
    job = agent.job
    return Profile(
        level=job.level,
        language=job.language,
        subject=job.subject,
        profile_md=await _memory(session, job.user_id, None),
    )


@router.post("/get_subject_memory")
async def get_subject_memory(
    args: GetSubjectMemory,
    agent: Annotated[Agent, allowed("get_subject_memory")],
    session: Session,
) -> Memory:
    _check_text(args.subject)
    content = await _memory(session, agent.job.user_id, args.subject)
    return Memory(subject=args.subject, content_md=content)


@router.post("/list_history")
async def list_history(
    args: ListHistory, agent: Annotated[Agent, allowed("list_history")], session: Session
) -> list[HistoryItem]:
    """The student's other jobs, newest first."""
    jobs = await session.scalars(
        select(Job)
        .where(Job.user_id == agent.job.user_id, Job.id != agent.job.id)
        .order_by(Job.created_at.desc())
        .limit(args.limit)
    )
    return [
        HistoryItem(
            job_id=str(job.id),
            subject=job.subject,
            status=job.status,
            created_at=job.created_at,
            grade=job.grade,
            grade_comment=job.grade_comment,
        )
        for job in jobs
    ]


@router.post("/search_history")
async def search_history(
    args: SearchHistory, agent: Annotated[Agent, allowed("search_history")], session: Session
) -> list[Memory]:
    """Searches the student's memory docs, which the curator distills from past jobs."""
    _check_text(args.query)
    docs = await search_memory(session, agent.job.user_id, args.query)
    return [Memory(subject=doc.subject, content_md=doc.body) for doc in docs]


@router.post("/update_memory")
async def update_memory(
    args: UpdateMemory, agent: Annotated[Agent, allowed("update_memory")], session: Session
) -> Memory:
    """scope=profile ignores `subject`; scope=subject needs one."""
    subject = args.subject if args.scope == "subject" else None
    if args.scope == "subject" and not subject:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "scope=subject needs a subject")
    _check_text(subject, args.content_md)
    await session.execute(
        insert(MemoryDoc)
        .values(user_id=agent.job.user_id, subject=subject, body=args.content_md)
        .on_conflict_do_update(
            constraint="uq_memory_docs_user_id_subject",
            set_={"body": args.content_md, "updated_at": func.clock_timestamp()},
        )
    )
    await session.commit()
    return Memory(subject=subject, content_md=args.content_md)


@router.post("/read_artifact")
async def read_artifact(
    args: ReadArtifact, agent: Annotated[Agent, allowed("read_artifact")], session: Session
) -> ArtifactContent:
    """Any artifact of this job, from any round. Others answer 404, the same as missing ones."""
    try:
        artifact_id = uuid.UUID(args.artifact_id)
    except ValueError:
        artifact_id = None
    artifact = await session.scalar(
        select(Artifact).where(Artifact.id == artifact_id, Artifact.job_id == agent.job.id)
    )
    if artifact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "artifact not found")
    return ArtifactContent(
        artifact_id=str(artifact.id),
        title=artifact.title,
        role=artifact.role,
        kind=artifact.kind,
        round=artifact.round,
        content_md=artifact.content,
    )


@router.post("/write_artifact")
async def write_artifact(
    args: WriteArtifact, agent: Annotated[Agent, allowed("write_artifact")], session: Session
) -> WrittenArtifact:
    """Saves the artifact in the token's round. The dispatcher later marks the answer FINAL."""
    _check_text(args.title, args.content_md)
    kind = ArtifactKind.RESEARCH_NOTES if agent.role == Role.RESEARCH else ArtifactKind.DRAFT
    artifact = Artifact(
        job_id=agent.job.id,
        round=agent.round,
        role=agent.role,
        kind=kind,
        title=args.title,
        content=args.content_md,
    )
    session.add(artifact)
    await session.commit()
    return WrittenArtifact(artifact_id=str(artifact.id))
