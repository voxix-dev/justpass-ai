"""In-memory stand-ins for the model and the Context API: tests and local demos, no network."""

import json
from itertools import count

from pydantic import BaseModel, JsonValue

from agent.llm import ChatRequest, Completion, FunctionCall, Message, ToolCall, Usage
from agent.tools import (
    GetProfile,
    GetSubjectMemory,
    ReadArtifact,
    ToolError,
    UpdateMemory,
    WriteArtifact,
)
from shared.models import Role

_ids = count(1)


def say(text: str) -> Message:
    """A model reply without tool calls."""
    return Message(role="assistant", content=text)


def call(name: str, args: dict[str, JsonValue] | str) -> Message:
    """A model reply that calls one tool. A str is sent as raw (maybe malformed) arguments."""
    raw = args if isinstance(args, str) else json.dumps(args)
    tool_call = ToolCall(id=f"call_{next(_ids)}", function=FunctionCall(name=name, arguments=raw))
    return Message(role="assistant", tool_calls=[tool_call])


class ScriptedModel:
    """Replays canned replies per role. The role is read from the system prompt, which starts
    with "You are the <role> agent"."""

    def __init__(self, replies: dict[Role, list[Message]], cost_per_call: float = 0.01) -> None:
        self.replies = {role: list(messages) for role, messages in replies.items()}
        self.cost_per_call = cost_per_call
        self.requests: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> Completion:
        self.requests.append(request)
        system = request.messages[0].content or ""
        role = next(r for r in Role if system.startswith(f"You are the {r} agent"))
        usage = Usage(prompt_tokens=100, completion_tokens=20, cost=self.cost_per_call)
        return Completion(message=self.replies[role].pop(0), usage=usage)


class FakeContextClient:
    """The Context API in memory."""

    def __init__(self, profile: str = "High school, writes in English.") -> None:
        self.profile = profile
        self.memory: dict[str, str] = {}
        self.artifacts: dict[str, str] = {}
        self.calls: list[str] = []

    def add_artifact(self, content: str) -> str:
        artifact_id = f"art-{len(self.artifacts) + 1}"
        self.artifacts[artifact_id] = content
        return artifact_id

    async def call(self, tool: str, args: BaseModel) -> JsonValue:
        self.calls.append(tool)
        match args:
            case WriteArtifact(content_md=content):
                return {"artifact_id": self.add_artifact(content)}
            case ReadArtifact(artifact_id=artifact_id):
                if artifact_id not in self.artifacts:
                    raise ToolError(f"no artifact {artifact_id}")
                return self.artifacts[artifact_id]
            case GetProfile():
                return self.profile
            case GetSubjectMemory(subject=subject):
                return self.memory.get(subject, "")
            case UpdateMemory(scope="profile", content_md=content):
                self.profile = content
                return "ok"
            case UpdateMemory(subject=subject, content_md=content):
                self.memory[subject or ""] = content
                return "ok"
            case _:  # list_history, search_history
                return []
