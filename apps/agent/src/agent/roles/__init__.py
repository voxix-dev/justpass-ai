"""The roles of the agent: each one is a prompt, an agent and what it needs to run."""

from typing import Literal

from pydantic_ai import Agent

from agent.roles import math, writer
from agent.roles.general import Deps
from agent.roles.general import agent as general_agent

WorkerRole = Literal["general", "math", "writer"]

WORKERS: dict[WorkerRole, Agent[Deps, str]] = {
    "general": general_agent,
    "math": math.agent,
    "writer": writer.agent,
}
REQUEST_LIMITS: dict[WorkerRole, int] = {"general": 25, "math": 15, "writer": 3}
