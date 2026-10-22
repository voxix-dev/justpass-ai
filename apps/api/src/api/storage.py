"""Where user uploads (homework photos) and large artifacts live.

Objects are addressed by opaque keys like `users/<user id>/uploads/<random>`, so deleting a
user's files means deleting one prefix. The content type is recorded in Postgres (`files`
table), not here.

No signed URLs: files reach the browser through an API route that checks the user and calls
`get()`. That keeps the ownership check in one place, needs no extra IAM permission to sign
URLs, and works the same on local disk. Photos are a few MB, so proxying them is cheap.
That route should send the Content-Type stored in Postgres plus `X-Content-Type-Options: nosniff`,
and the upload route should read at most MAX_PHOTO_BYTES + 1 bytes. These calls block, so call
them from plain `def` routes.

Environment:
- STORAGE_BACKEND: `local` or `gcs`. Required, so production can't fall back to local disk.
- STORAGE_DIR: folder for `local`. Required with it.
- GCS_BUCKET: bucket for `gcs`. Credentials come from Application Default Credentials.
  STORAGE_EMULATOR_HOST (e.g. `http://localhost:4443`) points the client at fake-gcs-server.
"""

import os
import re
import uuid
from contextlib import suppress
from functools import cache
from pathlib import Path
from typing import Protocol

from google.api_core.exceptions import NotFound
from google.cloud import storage as gcs

MAX_PHOTO_BYTES = 10 * 1024 * 1024
KEY_SEGMENT_RE = re.compile(r"[A-Za-z0-9_.-]+")
# HEIC brands only: the generic HEIF ones (mif1, msf1) are also used by AVIF.
HEIC_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx"}


class ObjectNotFoundError(Exception):
    pass


class InvalidPhotoError(ValueError):
    """Not a JPEG/PNG/WebP/HEIC image, or empty. Answer 400."""


class PhotoTooLargeError(InvalidPhotoError):
    """Answer 413."""


class Storage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...

    def get(self, key: str) -> bytes:
        """Raises ObjectNotFoundError."""
        ...

    def delete(self, key: str) -> None:
        """Does nothing if the object doesn't exist."""
        ...


def check_key(key: str) -> str:
    """Keys are `/`-separated segments of letters, digits, `_`, `-` and `.`; no `.` or `..`."""
    segments = key.split("/")
    if len(key) > 512 or not all(
        KEY_SEGMENT_RE.fullmatch(s) and s not in {".", ".."} for s in segments
    ):
        raise ValueError(f"invalid object key: {key!r}")
    return key


def new_upload_key(user_id: str) -> str:
    if not KEY_SEGMENT_RE.fullmatch(user_id) or user_id in {".", ".."}:
        raise ValueError(f"invalid user id: {user_id!r}")
    return check_key(f"users/{user_id}/uploads/{uuid.uuid4().hex}")


def photo_content_type(data: bytes) -> str:
    """Return the photo's real content type from its first bytes. Never trust the client's."""
    if not data:
        raise InvalidPhotoError("the photo is empty")
    if len(data) > MAX_PHOTO_BYTES:
        raise PhotoTooLargeError(f"the photo is larger than {MAX_PHOTO_BYTES // 2**20} MB")
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in HEIC_BRANDS:
        return "image/heic"
    raise InvalidPhotoError("only JPEG, PNG, WebP and HEIC photos are accepted")


class LocalStorage:
    """Files on disk, for development and tests."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / check_key(key)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            raise ObjectNotFoundError(key) from None

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class GcsStorage:
    """Google Cloud Storage, for production."""

    def __init__(self, bucket: gcs.Bucket) -> None:
        self.bucket = bucket

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.bucket.blob(check_key(key)).upload_from_string(data, content_type=content_type)

    def get(self, key: str) -> bytes:
        try:
            return self.bucket.blob(check_key(key)).download_as_bytes()
        except NotFound:
            raise ObjectNotFoundError(key) from None

    def delete(self, key: str) -> None:
        with suppress(NotFound):
            self.bucket.blob(check_key(key)).delete()


@cache
def get_storage() -> Storage:
    backend = os.environ.get("STORAGE_BACKEND")
    if backend == "gcs":
        return GcsStorage(gcs.Client().bucket(os.environ["GCS_BUCKET"]))
    if backend == "local":
        return LocalStorage(Path(os.environ["STORAGE_DIR"]))
    raise ValueError(f"unknown STORAGE_BACKEND: {backend!r}")
