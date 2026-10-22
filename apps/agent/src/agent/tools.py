"""Tool arguments, tool specs for the model, and the backends that run tools.

Each tool's arguments are a Pydantic model; its docstring is the description the model sees.
Which role may call which tool is decided only in roles.py.
"""

from typing import Literal, Protocol

import httpx2
from pydantic import BaseModel, Field, JsonValue


class RunAgent(BaseModel):
    """Delegate one step to a worker agent. Returns {artifact_id, summary, status, confidence}."""

    role: Literal["research", "writer", "solver", "review", "curator"]
    tier: Literal["cheap", "mid", "strong"]
    task: str = Field(max_length=4000)
    input_artifact_ids: list[str] = Field(default_factory=list[str], max_length=20)


class GetProfile(BaseModel):
    """Read the student's profile: level, language, writing style."""


class GetSubjectMemory(BaseModel):
    """Read what is known about the student in one subject."""

    subject: str = Field(max_length=100)


class ListHistory(BaseModel):
    """List the student's recent homework, newest first."""

    limit: int = Field(default=10, ge=1, le=50)


class SearchHistory(BaseModel):
    """Full-text search over the student's past homework and memory."""

    query: str = Field(max_length=200)


class UpdateMemory(BaseModel):
    """Replace the student's profile doc or one subject doc (Markdown, about 2k tokens max)."""

    scope: Literal["profile", "subject"]
    subject: str | None = Field(default=None, max_length=100)
    content_md: str = Field(max_length=10_000)


class ReadArtifact(BaseModel):
    """Read an artifact's full content by id."""

    artifact_id: str = Field(max_length=100)


class WriteArtifact(BaseModel):
    """Save Markdown as a new artifact. Returns {artifact_id}."""

    title: str = Field(max_length=200)
    content_md: str = Field(max_length=200_000)


class WebSearch(BaseModel):
    """Search the web."""

    query: str = Field(max_length=300)


class WebFetch(BaseModel):
    """Fetch a web page as text."""

    url: str = Field(max_length=2000)


class RunCode(BaseModel):
    """Run Python code (no network) and return its output."""

    code: str = Field(max_length=20_000)


TOOL_ARGS: dict[str, type[BaseModel]] = {
    "run_agent": RunAgent,
    "get_profile": GetProfile,
    "get_subject_memory": GetSubjectMemory,
    "list_history": ListHistory,
    "search_history": SearchHistory,
    "update_memory": UpdateMemory,
    "read_artifact": ReadArtifact,
    "write_artifact": WriteArtifact,
    "web_search": WebSearch,
    "web_fetch": WebFetch,
    "run_code": RunCode,
}
CONTEXT_TOOLS = frozenset(TOOL_ARGS) - {"run_agent", "web_search", "web_fetch", "run_code"}


def tool_spec(name: str) -> dict[str, JsonValue]:
    args = TOOL_ARGS[name]
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": args.__doc__ or "",
            "parameters": args.model_json_schema(),
        },
    }


class WrittenArtifact(BaseModel):
    artifact_id: str


class ToolError(Exception):
    """A tool failed in a way the model should hear about and may work around."""


class ToolBackend(Protocol):
    async def call(self, tool: str, args: BaseModel) -> JsonValue: ...


class HttpContextClient:
    """Context API over HTTP for one role. The token is scoped to one job and that role and
    never reaches a prompt; the server checks it against roles.py again."""

    def __init__(self, base_url: str, token: str, http: httpx2.AsyncClient | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._http = http or httpx2.AsyncClient(timeout=30)

    async def call(self, tool: str, args: BaseModel) -> JsonValue:
        url = f"{self._base_url}/v1/context/{tool}"
        try:
            resp = await self._http.post(
                url, json=args.model_dump(mode="json"), headers=self._headers
            )
        except httpx2.TransportError as e:
            raise ToolError(f"context API unreachable: {type(e).__name__}") from e
        if resp.is_error:
            raise ToolError(f"context API returned HTTP {resp.status_code} for {tool}")
        try:
            result: JsonValue = resp.json()
        except ValueError as e:
            raise ToolError(f"context API returned invalid JSON for {tool}") from e
        return result


class UnavailableTool:
    """Stand-in for tools not built yet (web search/fetch, code sandbox)."""

    async def call(self, tool: str, args: BaseModel) -> JsonValue:
        raise ToolError(f"{tool} is not available yet")
