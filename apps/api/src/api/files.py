"""Files API: a student uploads the files of a task (a photo, a PDF, a document), then creates a
job that lists them.

The file is the request body as it is, not a multipart form. The bytes go to object storage
(api.storage) and the database keeps one row per file.
"""

import re
import unicodedata
import uuid
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy import any_, func, select

from api.auth.current_user import CurrentUser
from api.jobs import JOBS_AUTH, Problem, Responses, Session, ensure_user
from api.storage import NotFound, Storage, StorageUnavailable, get_storage
from api.tables import File, Job

MAX_FILE = 20 * 1024 * 1024
MAX_NAME = 255
DEFAULT_NAME = "file"
DEFAULT_TYPE = "application/octet-stream"
MAX_TYPE = 255  # the width of files.mime_type
MAX_UNATTACHED = 20  # files a user may hold that no task of theirs uses
CANNOT_STORE = "Files cannot be stored right now. Try again later."
CANNOT_LOAD = "This file cannot be loaded right now. Try again later."

# The only types served inline, so only these are checked: the bytes must start the way the type
# says, or an upload could pass as an image. Everything else is served as a download.
_IMAGES = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),
    "image/gif": (b"GIF87a", b"GIF89a"),
}
_TYPE = re.compile(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+")
_DOWN = "The database, the storage or Google's token signing keys cannot be reached"
_BINARY: dict[str, Any] = {"schema": {"type": "string", "format": "binary"}}
_UPLOAD: dict[str, Any] = {"requestBody": {"required": True, "content": {"*/*": _BINARY}}}
_FILE: Responses = {
    200: {"description": "The file", "content": {"*/*": _BINARY}},
    404: {"model": Problem, "description": "No such file for this user"},
    503: {
        "model": Problem,
        "description": _DOWN,
    },
}

router = APIRouter(prefix="/files", tags=["files"], responses=JOBS_AUTH)
Store = Annotated[Storage, Depends(get_storage)]


class FileOut(BaseModel):
    """An uploaded file. Give its id to POST /jobs."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: uuid.UUID
    name: str
    mime_type: str
    size: int  # bytes


def _mime_type(content_type: str) -> str:
    """The Content-Type without parameters, in lower case; octet-stream if it is not a type."""
    mime_type = content_type.split(";")[0].strip().lower()
    if len(mime_type) > MAX_TYPE or not _TYPE.fullmatch(mime_type):
        return DEFAULT_TYPE
    return mime_type


def _name(name: str) -> str:
    """The file name alone: no directories, no control characters, at most MAX_NAME characters."""
    name = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    name = "".join(c for c in name if unicodedata.category(c) != "Cc")
    return name.strip()[:MAX_NAME].strip() or DEFAULT_NAME


def _matches(mime_type: str, data: bytes) -> bool:
    """Whether the bytes start like an image of the declared type."""
    if not data.startswith(_IMAGES[mime_type]):
        return False
    return mime_type != "image/webp" or data[8:12] == b"WEBP"


def _disposition(mime_type: str, name: str) -> str:
    """Show an image in the page; save anything else under its name (RFC 6266)."""
    if mime_type in _IMAGES:
        return "inline"
    fallback = "".join(c if c.isascii() and c not in '"\\%' else "_" for c in name)
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=_UPLOAD,
    responses={
        413: {"model": Problem, "description": "The file is over 20 MiB"},
        415: {"model": Problem, "description": "The bytes are not an image of the declared type"},
        422: {"model": Problem, "description": "The body is empty"},
        429: {"model": Problem, "description": "The user has too many files no task uses"},
        503: {
            "model": Problem,
            "description": _DOWN,
        },
    },
)
async def upload_file(
    request: Request,
    user: CurrentUser,
    session: Session,
    storage: Store,
    name: Annotated[str, Query(description="The file's name, as the student has it")] = (
        DEFAULT_NAME
    ),
) -> FileOut:
    """Store a file sent as the request body, with its type as the Content-Type.

    A missing or invalid Content-Type counts as application/octet-stream.
    """
    mime_type = _mime_type(request.headers.get("content-type", ""))
    data = await request.body()
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The file is empty.")
    if len(data) > MAX_FILE:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, "The file is too large. Use one under 20 MB."
        )
    if mime_type in _IMAGES and not _matches(mime_type, data):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"This is not a valid {mime_type} image. Check the file and try again.",
        )
    waiting = await session.scalar(
        select(func.count())
        .select_from(File)
        .where(
            File.user_id == user.uid,
            ~select(Job.id)
            .where(Job.user_id == user.uid, File.storage_key == any_(Job.file_keys))
            .exists(),
        )
    )
    # End the read's transaction: the upload must not hold a pooled connection.
    await session.commit()
    if (waiting or 0) >= MAX_UNATTACHED:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "You have too many files waiting. Send a task or remove some first.",
        )
    file_id = uuid.uuid4()
    key = f"users/{user.uid}/uploads/{file_id}"
    # The object goes first: a failed upload leaves no row that points at nothing.
    try:
        await storage.put(key, data, mime_type)
    except StorageUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, CANNOT_STORE) from None
    await ensure_user(session, user)
    name = _name(name)
    session.add(
        File(
            id=file_id,
            user_id=user.uid,
            storage_key=key,
            name=name,
            mime_type=mime_type,
            size_bytes=len(data),
        )
    )
    await session.commit()
    return FileOut(id=file_id, name=name, mime_type=mime_type, size=len(data))


@router.get("/{file_id}", response_class=Response, responses=_FILE)
async def get_file(
    file_id: uuid.UUID, user: CurrentUser, session: Session, storage: Store
) -> Response:
    """One of the signed-in user's files; 404 for a missing one or another user's.

    Images are shown in the page and everything else is a download, with headers that stop an
    uploaded page or SVG from running on the app's origin.
    """
    file = await session.scalar(select(File).where(File.id == file_id, File.user_id == user.uid))
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    key, mime_type, name = file.storage_key, file.mime_type, file.name
    # End the read's transaction: the download must not hold a pooled connection.
    await session.commit()
    try:
        data = await storage.get(key)
    except NotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found") from None
    except StorageUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, CANNOT_LOAD) from None
    return Response(
        data,
        media_type=mime_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=3600",
            "Content-Security-Policy": "sandbox",
            "Content-Disposition": _disposition(mime_type, name),
        },
    )
