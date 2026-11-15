import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.current_user import _current_user  # pyright: ignore[reportPrivateUsage]
from api.auth.firebase import AuthUser
from api.db import get_session
from api.files import MAX_PHOTO
from api.main import MAX_BODY, app
from api.storage import GcsStorage, LocalStorage, NotFound, get_storage, storage_from_env
from api.tables import File, User

pytestmark = pytest.mark.anyio

JPEG = b"\xff\xd8\xff\xe0jpeg bytes"
PNG = b"\x89PNG\r\n\x1a\npng bytes"
WEBP = b"RIFF\x04\x00\x00\x00WEBPwebp bytes"
ANA = AuthUser(
    uid="uid-ana",
    is_anonymous=False,
    email="ana@example.com",
    email_verified=True,
    provider="google.com",
)
BOB = AuthUser(
    uid="uid-bob",
    is_anonymous=True,
    email=None,
    email_verified=False,
    provider="anonymous",
)


@pytest.fixture
async def client(session: AsyncSession, storage: LocalStorage) -> AsyncIterator[httpx2.AsyncClient]:
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[_current_user] = lambda: ANA
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def photo(data: bytes, mime_type: str) -> dict:
    return {"content": data, "headers": {"content-type": mime_type}}


@pytest.mark.parametrize(
    ("data", "mime_type", "extension"),
    [(JPEG, "image/jpeg", "jpg"), (PNG, "image/png", "png"), (WEBP, "image/webp", "webp")],
)
async def test_upload_stores_the_photo(
    client: httpx2.AsyncClient,
    session: AsyncSession,
    storage: LocalStorage,
    data: bytes,
    mime_type: str,
    extension: str,
):
    response = await client.post("/files", **photo(data, mime_type))
    assert response.status_code == 201
    body = response.json()
    assert body == {"id": body["id"], "mimeType": mime_type, "size": len(data)}
    row = await session.get_one(File, uuid.UUID(body["id"]))
    assert row.storage_key == f"users/uid-ana/uploads/{body['id']}.{extension}"
    assert (row.user_id, row.mime_type, row.size_bytes) == ("uid-ana", mime_type, len(data))
    assert row.extracted_text is None
    assert await storage.get(row.storage_key) == data


async def test_upload_accepts_a_content_type_with_parameters(client: httpx2.AsyncClient):
    response = await client.post("/files", **photo(PNG, "Image/PNG; charset=binary"))
    assert response.status_code == 201


@pytest.mark.parametrize(
    ("data", "mime_type"),
    [
        (PNG, "image/jpeg"),
        (JPEG, "image/png"),
        (b"RIFF\x04\x00\x00\x00WAVEdata", "image/webp"),
        (b"just text", "image/jpeg"),
        (JPEG, "image/gif"),
        (b"GIF89a", "image/gif"),
        (JPEG, "application/octet-stream"),
        (JPEG, "text/plain"),
        (JPEG, ""),
    ],
)
async def test_upload_refuses_a_wrong_or_unsupported_type(
    client: httpx2.AsyncClient, session: AsyncSession, data: bytes, mime_type: str
):
    headers = {"content-type": mime_type} if mime_type else {}
    response = await client.post("/files", content=data, headers=headers)
    assert response.status_code == 415
    assert isinstance(response.json()["detail"], str)
    assert await session.scalar(select(File.id)) is None


async def test_upload_refuses_an_empty_body(client: httpx2.AsyncClient):
    response = await client.post("/files", **photo(b"", "image/jpeg"))
    assert response.status_code == 422
    assert response.json() == {"detail": "The photo is empty."}


@pytest.mark.parametrize("path", ["/files", "/api/files"])
async def test_upload_over_the_limit_is_413(client: httpx2.AsyncClient, path: str):
    ok = await client.post(path, **photo(JPEG + bytes(MAX_PHOTO - len(JPEG)), "image/jpeg"))
    assert ok.status_code == 201
    big = await client.post(path, **photo(JPEG + bytes(MAX_PHOTO), "image/jpeg"))
    assert big.status_code == 413
    assert big.json() == {"detail": "The photo is too large. Use one under 8 MB."}


@pytest.mark.parametrize("path", ["/jobs", "/api/jobs"])
async def test_the_upload_limit_does_not_raise_the_limit_of_other_routes(
    client: httpx2.AsyncClient, path: str
):
    response = await client.post(path, content=bytes(MAX_BODY + 1))
    assert response.status_code == 413
    assert response.json() == {"detail": "The request is too large"}


async def test_get_returns_the_bytes_with_safe_headers(client: httpx2.AsyncClient):
    file_id = (await client.post("/files", **photo(PNG, "image/png"))).json()["id"]
    response = await client.get(f"/files/{file_id}")
    assert response.status_code == 200
    assert response.content == PNG
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, max-age=3600"


async def test_get_of_a_missing_or_another_users_photo_is_404(
    client: httpx2.AsyncClient, session: AsyncSession, storage: LocalStorage
):
    session.add(User(id=BOB.uid, is_anonymous=True))
    await session.flush()
    key = f"users/{BOB.uid}/uploads/1.jpg"
    await storage.put(key, JPEG, "image/jpeg")
    theirs = File(user_id=BOB.uid, storage_key=key, mime_type="image/jpeg", size_bytes=len(JPEG))
    session.add(theirs)
    await session.flush()
    for file_id in (theirs.id, uuid.uuid4()):
        response = await client.get(f"/files/{file_id}")
        assert response.status_code == 404
        assert isinstance(response.json()["detail"], str)


async def test_a_photo_is_only_for_the_user_who_uploaded_it(client: httpx2.AsyncClient):
    file_id = (await client.post("/files", **photo(JPEG, "image/jpeg"))).json()["id"]
    app.dependency_overrides[_current_user] = lambda: BOB
    assert (await client.get(f"/files/{file_id}")).status_code == 404
    response = await client.post("/jobs", json={"photos": [file_id]})
    assert response.status_code == 422
    app.dependency_overrides[_current_user] = lambda: ANA
    assert (await client.get(f"/files/{file_id}")).status_code == 200


async def test_the_upload_makes_the_user_row(client: httpx2.AsyncClient, session: AsyncSession):
    app.dependency_overrides[_current_user] = lambda: BOB
    assert (await client.post("/files", **photo(JPEG, "image/jpeg"))).status_code == 201
    assert await session.scalar(select(User.is_anonymous).where(User.id == BOB.uid)) is True


async def test_files_need_a_signed_in_user(client: httpx2.AsyncClient):
    del app.dependency_overrides[_current_user]
    assert (await client.post("/files", **photo(JPEG, "image/jpeg"))).status_code == 401
    assert (await client.get(f"/files/{uuid.uuid4()}")).status_code == 401


async def test_get_of_a_photo_missing_from_storage_is_404(
    client: httpx2.AsyncClient, session: AsyncSession
):
    file_id = (await client.post("/files", **photo(JPEG, "image/jpeg"))).json()["id"]
    file = await session.get_one(File, uuid.UUID(file_id))
    file.storage_key = "users/uid-ana/uploads/gone.jpg"
    await session.flush()
    assert (await client.get(f"/files/{file_id}")).status_code == 404


async def test_upload_without_storage_is_503(
    client: httpx2.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    del app.dependency_overrides[get_storage]
    monkeypatch.delenv("STORAGE_DIR", raising=False)
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.setattr(app.state, "storage", None, raising=False)
    response = await client.post("/files", **photo(JPEG, "image/jpeg"))
    assert response.status_code == 503
    assert isinstance(response.json()["detail"], str)


async def test_the_contract_describes_the_upload_and_the_download(client: httpx2.AsyncClient):
    paths = (await client.get("/openapi.json")).json()["paths"]
    types = {"image/jpeg", "image/png", "image/webp"}
    upload = paths["/files"]["post"]
    assert set(upload["requestBody"]["content"]) == types
    assert upload["requestBody"]["content"]["image/png"]["schema"] == {
        "type": "string",
        "format": "binary",
    }
    assert set(paths["/files/{file_id}"]["get"]["responses"]["200"]["content"]) == types


async def test_local_storage_round_trips_and_misses(tmp_path: Path):
    store = LocalStorage(tmp_path / "root")
    await store.put("a/b/c.bin", b"data", "application/octet-stream")
    assert await store.get("a/b/c.bin") == b"data"
    with pytest.raises(NotFound):
        await store.get("a/missing.bin")
    with pytest.raises(NotFound):
        await store.get("a")


@pytest.mark.parametrize("key", ["../escape", "a/../../escape", "/etc/passwd", "", "."])
async def test_local_storage_refuses_keys_outside_its_root(tmp_path: Path, key: str):
    store = LocalStorage(tmp_path / "root")
    with pytest.raises(ValueError, match="outside"):
        await store.put(key, b"x", "text/plain")
    with pytest.raises(ValueError, match="outside"):
        await store.get(key)
    assert not (tmp_path / "escape").exists()


def test_storage_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("STORAGE_DIR", raising=False)
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    with pytest.raises(RuntimeError, match="STORAGE_DIR or GCS_BUCKET"):
        storage_from_env()
    monkeypatch.setenv("GCS_BUCKET", "bucket")
    gcs = storage_from_env()
    assert isinstance(gcs, GcsStorage) and gcs.bucket_name == "bucket"
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    local = storage_from_env()
    assert isinstance(local, LocalStorage) and local.root == tmp_path.resolve()
    monkeypatch.setenv("STORAGE_DIR", "")
    assert isinstance(storage_from_env(), GcsStorage)
