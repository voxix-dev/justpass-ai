"""The agent's client for the Context API: the only way it reads or writes a student's data."""

from types import TracebackType
from typing import Literal, Self

import httpx2
from pydantic import BaseModel, TypeAdapter, ValidationError

from shared.context import (
    GetProfile,
    GetSubjectMemory,
    GetTask,
    HistoryItem,
    ListHistory,
    Memory,
    Profile,
    SearchHistory,
    Task,
    UpdateMemory,
    WriteArtifact,
    WrittenArtifact,
)
from shared.models import ArtifactKind

TIMEOUT_SECONDS = 30

TASK = TypeAdapter(Task)
PROFILE = TypeAdapter(Profile)
WRITTEN = TypeAdapter(WrittenArtifact)
MEMORY = TypeAdapter(Memory)
HISTORY = TypeAdapter(list[HistoryItem])
MEMORIES = TypeAdapter(list[Memory])


class ContextError(Exception):
    """A Context API call that failed. It fails the attempt, whichever role made the call."""


class ContextStatusError(ContextError, httpx2.HTTPStatusError):
    """The Context API answered with an error status."""


class ContextClient:
    """Calls the tools of one round of one job. A failed call raises, so the attempt fails."""

    def __init__(self, url: str, token: str, transport: httpx2.AsyncBaseTransport | None = None):
        self._http = httpx2.AsyncClient(
            base_url=url.rstrip("/") + "/v1/context/",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=TIMEOUT_SECONDS,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._http.aclose()

    async def _call[T](self, tool: str, arguments: BaseModel, reply: TypeAdapter[T]) -> T:
        try:
            response = await self._http.post(tool, content=arguments.model_dump_json().encode())
        except httpx2.RequestError as error:
            raise ContextError(f"{tool} failed: {type(error).__name__}") from error
        if not response.is_success:  # also a redirect: its body is not tool output
            # Keep the Context API's reason, which raise_for_status() drops.
            where = (
                f" (location: {response.headers['location']})"
                if "location" in response.headers
                else ""
            )
            raise ContextStatusError(
                f"{tool} returned {response.status_code}{where}: {response.text[:500]}",
                request=response.request,
                response=response,
            )
        try:
            return reply.validate_json(response.content)
        except ValidationError as error:  # not the tool output the agent was built against
            raise ContextError(f"{tool} returned an invalid response") from error

    async def get_task(self) -> Task:
        return await self._call("get_task", GetTask(), TASK)

    async def get_profile(self) -> Profile:
        return await self._call("get_profile", GetProfile(), PROFILE)

    async def write_draft(self, title: str, content_md: str) -> WrittenArtifact:
        arguments = WriteArtifact(kind=ArtifactKind.DRAFT, title=title, content_md=content_md)
        return await self._call("write_artifact", arguments, WRITTEN)

    async def get_subject_memory(self, subject: str | None = None) -> Memory:
        return await self._call("get_subject_memory", GetSubjectMemory(subject=subject), MEMORY)

    async def update_memory(
        self,
        scope: Literal["profile", "subject"],
        subject: str | None,
        content_md: str,
        base_version: int,
    ) -> Memory:
        arguments = UpdateMemory(
            scope=scope, subject=subject, content_md=content_md, base_version=base_version
        )
        return await self._call("update_memory", arguments, MEMORY)

    async def list_history(self, limit: int = 10) -> list[HistoryItem]:
        return await self._call("list_history", ListHistory(limit=limit), HISTORY)

    async def search_history(self, query: str) -> list[Memory]:
        return await self._call("search_history", SearchHistory(query=query), MEMORIES)
