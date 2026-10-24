"""The run_code protocol: the agent's sandbox client, the API's relay and the sandbox actor all
send and receive these bodies."""

from pydantic import BaseModel, ConfigDict, Field


class CodeRun(BaseModel):
    """POST /run on the sandbox actor: one Python script."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=20_000)


class CodeRunResult(BaseModel):
    exit_code: int  # 1: uncaught exception (also MemoryError); negative: killed by that signal
    stdout: str
    stderr: str
    timed_out: bool
    output_truncated: bool  # stdout or stderr hit the size cap; later writes failed
