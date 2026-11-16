"""Object storage for what students upload.

Keys are relative paths like users/{user_id}/uploads/{id}.

Production uses a Cloud Storage bucket (GCS_BUCKET). For local development and tests a directory
(STORAGE_DIR) does the same job.
"""

import asyncio
import os
import threading
from pathlib import Path
from typing import Any, Protocol

from fastapi import HTTPException, Request, status


class NotFound(KeyError):
    """No object has this key."""


class StorageUnavailable(Exception):
    """The storage failed (it cannot be reached, refused, or errored), not because of the key."""


class Storage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None:
        """Write a new object. Keys are never reused, so an existing one is an error.

        Raise StorageUnavailable if the storage fails.
        """
        ...

    async def get(self, key: str) -> bytes:
        """Read the object. Raise NotFound if there is none, StorageUnavailable if the storage
        fails."""
        ...


class LocalStorage:
    """Objects as files under a directory."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError(f"The key {key!r} is outside the storage directory")
        return path

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as out:
                out.write(data)

        await asyncio.to_thread(write)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError, IsADirectoryError, NotADirectoryError:
            raise NotFound(key) from None


class GcsStorage:
    """Objects in a Cloud Storage bucket, with Application Default Credentials.

    The client library is imported and the client made in a worker thread, never on the event
    loop: both block.
    """

    def __init__(self, bucket: str) -> None:
        self.bucket_name = bucket
        self._bucket: Any = None
        self._lock = threading.Lock()

    def _blob(self, key: str) -> Any:
        if self._bucket is None:
            with self._lock:
                if self._bucket is None:
                    from google.cloud import storage

                    self._bucket = storage.Client().bucket(self.bucket_name)
        return self._bucket.blob(key)

    def _put(self, key: str, data: bytes, content_type: str) -> None:
        try:
            self._blob(key).upload_from_string(data, content_type, if_generation_match=0)
        except _failures() as error:
            raise StorageUnavailable(key) from error

    def _get(self, key: str) -> bytes:
        from google.api_core.exceptions import NotFound as GcsNotFound

        try:
            return self._blob(key).download_as_bytes()
        except GcsNotFound as error:
            # A missing bucket is also a 404, but it is our problem, not a missing file.
            if "bucket does not exist" in str(error).lower():
                raise StorageUnavailable(key) from error
            raise NotFound(key) from None
        except _failures() as error:
            raise StorageUnavailable(key) from error

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(self._put, key, data, content_type)

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._get, key)


def _failures() -> tuple[type[Exception], ...]:
    """What the Google client raises when a call fails: an error response, no credentials, or a
    network problem."""
    from google.api_core.exceptions import GoogleAPICallError
    from google.auth.exceptions import GoogleAuthError
    from requests.exceptions import RequestException

    return GoogleAPICallError, GoogleAuthError, RequestException, OSError


def storage_from_env() -> Storage:
    """A directory if STORAGE_DIR is set, else the bucket in GCS_BUCKET.

    Raise RuntimeError if neither is set.
    """
    if directory := os.environ.get("STORAGE_DIR"):
        return LocalStorage(Path(directory))
    if bucket := os.environ.get("GCS_BUCKET"):
        return GcsStorage(bucket)
    raise RuntimeError("Set STORAGE_DIR or GCS_BUCKET to store uploads")


def get_storage(request: Request) -> Storage:
    """The storage from the environment, made on first use so the API starts without one.

    Tests replace this with their own.
    """
    storage: Storage | None = getattr(request.app.state, "storage", None)
    if storage is None:
        try:
            storage = storage_from_env()
        except RuntimeError:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Files cannot be stored right now. Try again later.",
            ) from None
        request.app.state.storage = storage
    return storage
