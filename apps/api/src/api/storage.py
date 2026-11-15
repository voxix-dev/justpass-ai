"""Object storage for what students upload.

Keys are relative paths like users/{user_id}/uploads/{id}.jpg.

Production uses a Cloud Storage bucket (GCS_BUCKET). For local development and tests a directory
(STORAGE_DIR) does the same job.
"""

import asyncio
import os
from pathlib import Path
from typing import Any, Protocol

from fastapi import HTTPException, Request, status


class NotFound(KeyError):
    """No object has this key."""


class Storage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None:
        """Write the object, replacing one with the same key."""
        ...

    async def get(self, key: str) -> bytes:
        """Read the object. Raise NotFound if there is none."""
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
            path.write_bytes(data)

        await asyncio.to_thread(write)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError, IsADirectoryError, NotADirectoryError:
            raise NotFound(key) from None


class GcsStorage:
    """Objects in a Cloud Storage bucket, with Application Default Credentials."""

    def __init__(self, bucket: str) -> None:
        self.bucket_name = bucket
        self._bucket: Any = None

    def _blob(self, key: str) -> Any:
        if self._bucket is None:
            from google.cloud import storage

            self._bucket = storage.Client().bucket(self.bucket_name)
        return self._bucket.blob(key)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(self._blob(key).upload_from_string, data, content_type)

    async def get(self, key: str) -> bytes:
        from google.api_core.exceptions import NotFound as GcsNotFound

        try:
            return await asyncio.to_thread(self._blob(key).download_as_bytes)
        except GcsNotFound:
            raise NotFound(key) from None


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
                "Photos cannot be stored right now. Try again later.",
            ) from None
        request.app.state.storage = storage
    return storage
