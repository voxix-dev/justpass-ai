"""In-memory stand-ins for the Context API (and later the model): tests and local demos."""

from pydantic import BaseModel, JsonValue

from agent.tools import (
    GetProfile,
    GetSubjectMemory,
    ReadArtifact,
    ToolError,
    UpdateMemory,
    WriteArtifact,
)


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
