from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    AWAITING_REVIEW = "awaiting_review"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    FAILED = "failed"


# Feedback moves a job from review (or expired, via a fresh actor) back to processing.
TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.RECEIVED: frozenset({JobStatus.PROCESSING}),
    JobStatus.PROCESSING: frozenset({JobStatus.AWAITING_REVIEW, JobStatus.FAILED}),
    JobStatus.AWAITING_REVIEW: frozenset(
        {JobStatus.ACCEPTED, JobStatus.PROCESSING, JobStatus.EXPIRED}
    ),
    JobStatus.EXPIRED: frozenset({JobStatus.PROCESSING}),
    JobStatus.ACCEPTED: frozenset(),
    JobStatus.FAILED: frozenset(),
}


def can_transition(current: JobStatus, new: JobStatus) -> bool:
    return new in TRANSITIONS[current]


class Role(StrEnum):
    ORCHESTRATOR = "orchestrator"
    RESEARCH = "research"
    WRITER = "writer"
    SOLVER = "solver"
    REVIEW = "review"
    CURATOR = "curator"


class Tier(StrEnum):
    """Agents ask for a tier; config maps tiers to concrete models."""

    CHEAP = "cheap"
    MID = "mid"
    STRONG = "strong"


class AgentResult(BaseModel):
    """What a worker returns to the orchestrator: a reference, never the full content."""

    artifact_id: str
    summary: str = Field(max_length=600)
    status: Literal["ok", "error"]
    confidence: float = Field(ge=0, le=1)


class ResearchBrief(BaseModel):
    """The only input the research agent gets. No free-text field for personal details."""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(max_length=200)
    questions: list[Annotated[str, Field(max_length=200)]] = Field(max_length=10)
    level: Literal["high_school", "college"]
    language: str = Field(min_length=2, max_length=8)
    length_words: int = Field(gt=0, le=5000)
