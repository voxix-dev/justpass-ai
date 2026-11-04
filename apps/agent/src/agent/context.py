"""The agent's client for the Context API: the only way it reads or writes a student's data."""

from types import TracebackType
from typing import Self

import httpx2

from shared.context import (
    GetProfile,
    GetTask,
    Profile,
    Task,
    WriteArtifact,
    WrittenArtifact,
)
from shared.models import ArtifactKind

TIMEOUT_SECONDS = 30


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

    async def _call(self, tool: str, arguments: GetTask | GetProfile | WriteArtifact) -> bytes:
        response = await self._http.post(tool, content=arguments.model_dump_json().encode())
        if response.is_error:  # keep the Context API's reason, which raise_for_status() drops
            raise httpx2.HTTPStatusError(
                f"{tool} returned {response.status_code}: {response.text[:500]}",
                request=response.request,
                response=response,
            )
        return response.content

    async def get_task(self) -> Task:
        return Task.model_validate_json(await self._call("get_task", GetTask()))

    async def get_profile(self) -> Profile:
        return Profile.model_validate_json(await self._call("get_profile", GetProfile()))

    async def write_draft(self, title: str, content_md: str) -> WrittenArtifact:
        arguments = WriteArtifact(kind=ArtifactKind.DRAFT, title=title, content_md=content_md)
        return WrittenArtifact.model_validate_json(await self._call("write_artifact", arguments))
