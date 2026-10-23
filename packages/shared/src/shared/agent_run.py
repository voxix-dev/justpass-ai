from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from shared.models import Role


class JobRequest(BaseModel):
    """What the agent actor gets to run one job."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1, max_length=100)
    playbook: str = Field(min_length=1, max_length=50)
    task: str = Field(min_length=1, max_length=20_000)  # the homework, as text
    input_artifact_ids: list[str] = Field(default_factory=list[str], max_length=20)


class RunRequest(BaseModel):
    """POST /run on the agent actor: one job plus its Context API tokens."""

    model_config = ConfigDict(extra="forbid")

    job: JobRequest
    # One token per role, minted by the API for this job. Never shown to a model.
    context_tokens: dict[Role, str] = Field(default_factory=dict[Role, str])


class JobResult(BaseModel):
    job_id: str
    status: Literal["ok", "failed"]
    final_artifact_id: str | None = None
    summary: str = ""
    error: str | None = None
    turns: int  # model calls across all agents
    cost_usd: float
