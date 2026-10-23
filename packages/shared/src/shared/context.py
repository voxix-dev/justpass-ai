"""Context API bodies: the tools agents call to read and write one student's data.

Each argument model's docstring is the tool description the model sees. Keep the argument models
the same as apps/agent/src/agent/tools.py until the agent imports them from here. The api serves
them at `POST /v1/context/<tool>`; the agent sends them with HttpContextClient.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from shared.models import ArtifactKind, JobStatus, Level, Role


class GetProfile(BaseModel):
    """Read the student's profile: level, language, writing style."""


class GetSubjectMemory(BaseModel):
    """Read what is known about the student in one subject."""

    subject: str = Field(max_length=100)


class ListHistory(BaseModel):
    """List the student's recent homework, newest first."""

    limit: int = Field(default=10, ge=1, le=50)


class SearchHistory(BaseModel):
    """Full-text search over the student's past homework and memory."""

    query: str = Field(max_length=200)


class UpdateMemory(BaseModel):
    """Replace the student's profile doc or one subject doc (Markdown, about 2k tokens max)."""

    scope: Literal["profile", "subject"]
    subject: str | None = Field(default=None, max_length=100)
    content_md: str = Field(max_length=10_000)


class ReadArtifact(BaseModel):
    """Read an artifact's full content by id."""

    artifact_id: str = Field(max_length=100)


class WriteArtifact(BaseModel):
    """Save Markdown as a new artifact. Returns {artifact_id}."""

    title: str = Field(max_length=200)
    content_md: str = Field(max_length=200_000)


class Profile(BaseModel):
    # This job's level, language and subject; the student may differ in other jobs.
    level: Level
    language: str
    subject: str
    profile_md: str  # the curator's notes on the student; empty until it writes some


class Memory(BaseModel):
    subject: str | None  # None for the profile doc
    content_md: str  # empty when nothing is known yet


class HistoryItem(BaseModel):
    job_id: str
    subject: str
    status: JobStatus
    created_at: datetime
    grade: str | None  # what the student says they got
    grade_comment: str | None


class ArtifactContent(BaseModel):
    artifact_id: str
    title: str | None
    role: Role
    kind: ArtifactKind
    round: int
    content_md: str | None  # None for files (in storage), which agents cannot read


class WrittenArtifact(BaseModel):
    artifact_id: str
