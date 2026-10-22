"""Local storage always runs. The GCS tests need fake-gcs-server and are skipped unless
STORAGE_EMULATOR_HOST is set, e.g.:

    docker run -d -p 4443:4443 fsouza/fake-gcs-server -scheme http
    STORAGE_EMULATOR_HOST=http://localhost:4443 uv run pytest apps/api/tests/test_storage.py
"""

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from google.auth.credentials import AnonymousCredentials
from google.cloud import storage as gcs

from api import storage
from api.storage import (
    GcsStorage,
    InvalidPhotoError,
    LocalStorage,
    ObjectNotFoundError,
    PhotoTooLargeError,
    Storage,
    check_key,
    new_upload_key,
    photo_content_type,
)

needs_gcs_emulator = pytest.mark.skipif(
    not os.environ.get("STORAGE_EMULATOR_HOST"), reason="STORAGE_EMULATOR_HOST is not set"
)


@pytest.fixture
def gcs_storage() -> Iterator[GcsStorage]:
    client = gcs.Client(project="test", credentials=AnonymousCredentials())
    bucket = client.create_bucket(f"test-{uuid.uuid4().hex}")
    yield GcsStorage(bucket)
    bucket.delete(force=True)


@pytest.fixture(params=["local", pytest.param("gcs", marks=needs_gcs_emulator)])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Storage:
    if request.param == "gcs":
        return request.getfixturevalue("gcs_storage")
    return LocalStorage(tmp_path)


def test_put_get_delete(store: Storage):
    key = new_upload_key("user-1")
    store.put(key, b"photo bytes", "image/jpeg")
    assert store.get(key) == b"photo bytes"
    store.put(key, b"new bytes", "image/jpeg")
    assert store.get(key) == b"new bytes"
    store.delete(key)
    with pytest.raises(ObjectNotFoundError):
        store.get(key)
    store.delete(key)  # deleting twice is fine


@pytest.mark.parametrize(
    "key", ["", "/abs", "users//x", "users/../x", "users/./x", "..", "a\\b", "a b", "ü", "a" * 513]
)
def test_rejects_bad_keys(store: Storage, key: str):
    with pytest.raises(ValueError, match="invalid object key"):
        store.put(key, b"x", "image/png")


def test_upload_keys_are_per_user_and_unique():
    first, second = new_upload_key("user-1"), new_upload_key("user-1")
    assert first.startswith("users/user-1/uploads/")
    assert first != second
    assert check_key(first) == first


@pytest.mark.parametrize("user_id", ["", "victim/uploads/abc", "..", "a b"])
def test_rejects_bad_user_ids(user_id: str):
    with pytest.raises(ValueError, match="invalid user id"):
        new_upload_key(user_id)


@pytest.mark.parametrize(
    ("data", "content_type"),
    [
        (b"\xff\xd8\xff\xe0rest", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\nrest", "image/png"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
        (b"\x00\x00\x00\x18ftypheic\x00\x00", "image/heic"),
        (b"\xff\xd8\xff" + b"\x00" * (storage.MAX_PHOTO_BYTES - 3), "image/jpeg"),
    ],
)
def test_photo_content_type(data: bytes, content_type: str):
    assert photo_content_type(data) == content_type


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"<html><script>alert(1)</script>",
        b"%PDF-1.7",
        b"RIFF\x00\x00\x00\x00WAVEfmt ",  # audio, not WebP
        b"\x00\x00\x00\x18ftypmp42",  # a video, not HEIC
        b"\x00\x00\x00\x18ftypmif1\x00\x00\x00\x00avif",  # AVIF uses the generic brand
    ],
)
def test_rejects_bad_photos(data: bytes):
    with pytest.raises(InvalidPhotoError):
        photo_content_type(data)


def test_rejects_too_large_photos():
    with pytest.raises(PhotoTooLargeError):
        photo_content_type(b"\xff\xd8\xff" + b"\x00" * (storage.MAX_PHOTO_BYTES - 2))


def test_get_storage_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    storage.get_storage.cache_clear()
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    try:
        local = storage.get_storage()
        assert isinstance(local, LocalStorage)
        assert local.root == tmp_path
        for backend in ["s3", None]:
            storage.get_storage.cache_clear()
            if backend:
                monkeypatch.setenv("STORAGE_BACKEND", backend)
            else:
                monkeypatch.delenv("STORAGE_BACKEND")
            with pytest.raises(ValueError, match="STORAGE_BACKEND"):
                storage.get_storage()
    finally:
        storage.get_storage.cache_clear()
