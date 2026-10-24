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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.agent_token import InvalidToken, verify_token
from api.db import get_session
from api.tables import Artifact, Job
from shared.context import ArtifactContent, ReadArtifact, WriteArtifact, WrittenArtifact
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
