"""What the API and the agent send each other over HTTP."""

import uuid

from pydantic import BaseModel, ConfigDict, Field


class AgentRun(BaseModel):
    """POST /run on the agent: solve one round of one job."""

    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID
    round: int = Field(ge=1)
    text: str  # the student's task
    feedback: str | None = None  # what the student asked to change, from round 2 on


class AgentAnswer(BaseModel):
    """The agent's reply to POST /run."""

    # Markdown with math, or None when the agent could not solve the task. No NUL: Postgres text
    # can't hold it.
    answer: str | None = Field(pattern=r"^[^\x00]*$")
