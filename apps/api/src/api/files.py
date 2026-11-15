"""Files API: a student uploads photos of a task, then creates a job that lists them.

The photo is the request body as it is, not a multipart form. The bytes go to object storage
(api.storage) and the database keeps one row per photo.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy import select

from api.auth.current_user import CurrentUser
from api.jobs import UNAVAILABLE, Problem, Responses, Session, ensure_user
from api.storage import NotFound, Storage, get_storage
from api.tables import File

MAX_PHOTO = 8 * 1024 * 1024

# Declared type, file extension and the bytes a file of that type starts with.
_KINDS = {
    "image/jpeg": ("jpg", b"\xff\xd8\xff"),
    "image/png": ("png", b"\x89PNG\r\n\x1a\n"),
    "image/webp": ("webp", b"RIFF"),
}
_BINARY: dict[str, Any] = {"schema": {"type": "string", "format": "binary"}}
_UPLOAD: dict[str, Any] = {
    "requestBody": {"required": True, "content": dict.fromkeys(_KINDS, _BINARY)}
}
_PHOTO: Responses = {
    200: {"description": "The photo", "content": dict.fromkeys(_KINDS, _BINARY)},
    404: {"model": Problem, "description": "No such photo for this user"},
}

router = APIRouter(prefix="/files", tags=["files"], responses=UNAVAILABLE)
Store = Annotated[Storage, Depends(get_storage)]


class FileOut(BaseModel):
    """An uploaded photo. Give its id to POST /jobs."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: uuid.UUID
    mime_type: str
    size: int  # bytes


def _matches(mime_type: str, data: bytes) -> bool:
    """Whether the bytes start like a file of the declared type."""
    start = _KINDS[mime_type][1]
    if not data.startswith(start):
        return False
    return mime_type != "image/webp" or data[8:12] == b"WEBP"


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=_UPLOAD,
    responses={
        413: {"model": Problem, "description": "The photo is over 8 MiB"},
        415: {"model": Problem, "description": "Not a JPEG, PNG or WebP photo"},
        422: {"model": Problem, "description": "The body is empty"},
    },
)
async def upload_file(
    request: Request, user: CurrentUser, session: Session, storage: Store
) -> FileOut:
    """Store a photo sent as the request body, with its image type as the Content-Type."""
    mime_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if mime_type not in _KINDS:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Use a JPEG, PNG or WebP photo."
        )
    data = await request.body()
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The photo is empty.")
    if len(data) > MAX_PHOTO:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, "The photo is too large. Use one under 8 MB."
        )
    if not _matches(mime_type, data):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "This is not a valid photo. Use a JPEG, PNG or WebP photo.",
        )
    file_id = uuid.uuid4()
    key = f"users/{user.uid}/uploads/{file_id}.{_KINDS[mime_type][0]}"
    # The object goes first: a failed upload leaves no row that points at nothing.
    await storage.put(key, data, mime_type)
    await ensure_user(session, user)
    session.add(
        File(
            id=file_id, user_id=user.uid, storage_key=key, mime_type=mime_type, size_bytes=len(data)
        )
    )
    await session.commit()
    return FileOut(id=file_id, mime_type=mime_type, size=len(data))


@router.get("/{file_id}", response_class=Response, responses=_PHOTO)
async def get_file(
    file_id: uuid.UUID, user: CurrentUser, session: Session, storage: Store
) -> Response:
    """One of the signed-in user's photos; 404 for a missing one or another user's."""
    file = await session.scalar(select(File).where(File.id == file_id, File.user_id == user.uid))
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Photo not found")
    try:
        data = await storage.get(file.storage_key)
    except NotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Photo not found") from None
    return Response(
        data,
        media_type=file.mime_type,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"},
    )
