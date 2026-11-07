"""Context API bodies: the tools an agent calls to read and write one student's data.

The API serves them at `POST /v1/context/<tool>` and the agent sends them. The Context API is not
in contracts/openapi.json: only agents call it, never the web app. An argument model's docstring
is the tool description a model would see. Every text field refuses NUL, which Postgres cannot
store.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

from shared.models import ArtifactKind, JobStatus, Level


def _no_nul(text: str) -> str:
    if "\x00" in text:
        raise ValueError("The text contains a NUL character.")
    return text


Text = Annotated[str, AfterValidator(_no_nul)]


def _not_final(kind: ArtifactKind) -> ArtifactKind:
    if kind == ArtifactKind.FINAL:
        raise ValueError("The answer is not written through a tool.")
    return kind


WritableKind = Annotated[ArtifactKind, AfterValidator(_not_final)]


class GetTask(BaseModel):
    """Read the task: the question, and every round so far with its feedback and artifacts."""


class GetProfile(BaseModel):
    """Read the student's level, language and subject, and the notes kept on the student."""


class GetSubjectMemory(BaseModel):
    """Read what is known about the student in one subject (default: this job's subject)."""

    subject: Text | None = Field(default=None, min_length=1, max_length=100)


class ListHistory(BaseModel):
    """List the student's other homework, newest first."""

    limit: int = Field(default=10, ge=1, le=50)


class SearchHistory(BaseModel):
    """Full-text search over what is known about the student."""

    query: Text = Field(max_length=200)


class UpdateMemory(BaseModel):
    """Replace the student's profile doc, or one subject doc (Markdown, about 2k tokens at most)."""

    scope: Literal["profile", "subject"]
    subject: Text | None = Field(default=None, min_length=1, max_length=100)  # default: the job's
    content_md: Text = Field(max_length=10_000)


class ReadArtifact(BaseModel):
    """Read the full content of an artifact of this job."""

    artifact_id: uuid.UUID


class WriteArtifact(BaseModel):
    """Save Markdown as a new artifact of the current round. Not for the final answer."""

    kind: WritableKind
    title: Text = Field(max_length=200)
    content_md: Text = Field(max_length=100_000)


class ArtifactRef(BaseModel):
    id: uuid.UUID
    kind: ArtifactKind
    title: str | None


class RoundInfo(BaseModel):
    number: int
    feedback: str | None  # what the student asked to change; None for round 1
    outcome: Literal["answered", "failed", "running"]
    artifacts: list[ArtifactRef]  # the answer (kind final) is one of them


class Task(BaseModel):
    job_id: uuid.UUID
    subject: str
    level: Level
    language: str
    text: str  # the student's question
    round: int  # the round this token is for
    rounds: list[RoundInfo]  # every round, oldest first, with the current one last


class ArtifactContent(BaseModel):
    artifact_id: uuid.UUID
    kind: ArtifactKind
    round: int
    title: str | None
    content_md: str | None  # None for files (in storage), which agents cannot read


class WrittenArtifact(BaseModel):
    artifact_id: uuid.UUID


class Profile(BaseModel):
    # This job's level, language and subject; the student may differ in other jobs.
    level: Level
    language: str
    subject: str
    profile_md: str  # the notes kept on the student; empty until some are written


class Memory(BaseModel):
    subject: str | None  # None for the profile doc
    content_md: str  # empty when nothing is known yet


class HistoryItem(BaseModel):
    job_id: uuid.UUID
    subject: str
    status: JobStatus
    created_at: datetime
    grade: str | None  # what the student says they got
    grade_comment: str | None
