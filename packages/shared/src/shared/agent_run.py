"""What the API and the agent send each other over HTTP."""

import uuid

from pydantic import BaseModel, ConfigDict, Field


class ContextGrant(BaseModel):
    """How the agent reaches the Context API: its URL, and a token for this round of this job."""

    model_config = ConfigDict(extra="forbid")

    url: str
    token: str = Field(repr=False)  # a secret: keep it out of logs and out of prompts


class AgentRun(BaseModel):
    """POST /run on the agent: solve one round of one job.

    The API and the agent forbid unknown fields, so they deploy together: a new field needs both.
    """

    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID
    round: int = Field(ge=1)
    text: str  # the student's task
    feedback: str | None = None  # what the student asked to change, from round 2 on
    # None when there is no Context API to reach: the agent works from text and feedback alone.
    context: ContextGrant | None = None


class AgentAnswer(BaseModel):
    """The agent's reply to POST /run."""

    # Markdown with math, or None when the agent could not solve the task. No NUL: Postgres text
    # can't hold it.
    answer: str | None = Field(pattern=r"^[^\x00]*$")
