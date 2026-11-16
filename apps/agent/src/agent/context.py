"""The agent's client for the Context API: the only way it reads or writes a student's data."""

from types import TracebackType
from typing import Literal, Self
from uuid import UUID

import httpx2
from pydantic import BaseModel, TypeAdapter, ValidationError

from shared.context import (
    GetFile,
    GetProfile,
    GetSubjectMemory,
    GetTask,
    HistoryItem,
    ListHistory,
    Memory,
    Profile,
    SavedFileText,
    SaveFileText,
    SearchHistory,
    Task,
    UpdateMemory,
    WriteArtifact,
    WrittenArtifact,
)
from shared.models import ArtifactKind

TIMEOUT_SECONDS = 30
MAX_FILE_BYTES = 20 * 1024 * 1024  # the most the API stores for one file

TASK = TypeAdapter(Task)
PROFILE = TypeAdapter(Profile)
WRITTEN = TypeAdapter(WrittenArtifact)
MEMORY = TypeAdapter(Memory)
HISTORY = TypeAdapter(list[HistoryItem])
MEMORIES = TypeAdapter(list[Memory])
SAVED_TEXT = TypeAdapter(SavedFileText)


class ContextError(Exception):
    """A Context API call that failed. It fails the attempt, whichever role made the call."""


class ContextStatusError(ContextError, httpx2.HTTPStatusError):
    """The Context API answered with an error status."""


def _check(tool: str, response: httpx2.Response) -> None:
    """Raise for a response that is not a success; its body must already be read."""
    if not response.is_success:  # also a redirect: its body is not tool output
        # Keep the Context API's reason, which raise_for_status() drops.
        where = (
            f" (location: {response.headers['location']})" if "location" in response.headers else ""
        )
        raise ContextStatusError(
            f"{tool} returned {response.status_code}{where}: {response.text[:500]}",
            request=response.request,
            response=response,
        )


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
        _check(tool, response)
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

    async def get_file(self, file_id: UUID) -> tuple[bytes, str]:
        """The bytes of a file of this job and the content type they were stored with. The body is
        read as it arrives and refused past 20 MiB, so a wrong answer cannot fill the memory."""
        content = GetFile(file_id=file_id).model_dump_json().encode()
        try:
            async with self._http.stream("POST", "get_file", content=content) as response:
                if not response.is_success:
                    await response.aread()
                    _check("get_file", response)
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_FILE_BYTES:
                        raise ContextError("get_file returned a body over 20 MiB")
                content_type = response.headers.get("content-type", "application/octet-stream")
        except httpx2.RequestError as error:
            raise ContextError(f"get_file failed: {type(error).__name__}") from error
        return bytes(body), content_type

    async def save_file_text(self, file_id: UUID, text: str) -> SavedFileText:
        arguments = SaveFileText(file_id=file_id, text=text)
        return await self._call("save_file_text", arguments, SAVED_TEXT)
