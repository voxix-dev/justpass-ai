"""What the API and the agent send each other over HTTP."""

import uuid

from pydantic import BaseModel, ConfigDict, Field, SecretStr, SerializationInfo, field_serializer

# The serialization context that makes a model show its secrets: only the wire format sets it.
WIRE = {"reveal_secrets": True}


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
    # The dispatcher's lease on this round, the one its Context token is for. The agent runs a round
    # once per lease: a retry under the same lease joins the run, a new lease (the round was
    # claimed again by another dispatcher) starts its own. None when no lease is in play.
    lease_id: uuid.UUID | None = None
    # None when there is no Context API to reach: the agent works from text and feedback alone.
    context: ContextGrant | None = None
    # The key for the model provider, or None when the round has no model: the agent then answers
    # with a fixed sample. A secret: dumps, reprs and logs show it as asterisks, and only
    # to_wire_json() writes it out.
    openrouter_key: SecretStr | None = Field(default=None, repr=False)

    @field_serializer("openrouter_key", when_used="json")
    def _hide_key(self, key: SecretStr | None, info: SerializationInfo) -> str | None:
        if key is None:
            return None
        return key.get_secret_value() if info.context == WIRE else str(key)

    def to_wire_json(self) -> str:
        """The JSON to POST to the agent, the one place the key is written out."""
        return self.model_dump_json(context=WIRE)


class AgentAnswer(BaseModel):
    """The agent's reply to POST /run."""

    # Markdown with math, or None when the agent could not solve the task. No NUL: Postgres text
    # can't hold it.
    answer: str | None = Field(pattern=r"^[^\x00]*$")
