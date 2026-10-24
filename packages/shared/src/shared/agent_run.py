from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class JobRequest(BaseModel):
    """What the agent actor gets to run one job."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1, max_length=100)
    playbook: str = Field(min_length=1, max_length=50)
    task: str = Field(min_length=1, max_length=20_000)  # the homework, as text
    input_artifact_ids: list[str] = Field(default_factory=list[str], max_length=20)


class JobResult(BaseModel):
    job_id: str
    status: Literal["ok", "failed"]
    final_artifact_id: str | None = None
    summary: str = ""
    error: str | None = None
    turns: int  # model calls across all agents
    cost_usd: float
