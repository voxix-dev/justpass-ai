"""Context API bodies: the tools agents call to read and write one student's data.

Each argument model's docstring is the tool description the model sees. Keep the argument models
the same as apps/agent/src/agent/tools.py until the agent imports them from here. The api serves
them at `POST /v1/context/<tool>`; the agent sends them with HttpContextClient.
"""

from pydantic import BaseModel, Field

from shared.models import ArtifactKind, Role


class ReadArtifact(BaseModel):
    """Read an artifact's full content by id."""

    artifact_id: str = Field(max_length=100)


class WriteArtifact(BaseModel):
    """Save Markdown as a new artifact. Returns {artifact_id}."""

    title: str = Field(max_length=200)
    content_md: str = Field(max_length=200_000)


class ArtifactContent(BaseModel):
    artifact_id: str
    title: str | None
    role: Role
    kind: ArtifactKind
    round: int
    content_md: str | None  # None for files (in storage), which agents cannot read


class WrittenArtifact(BaseModel):
    artifact_id: str
