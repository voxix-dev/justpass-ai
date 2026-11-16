import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx2
import pytest
from google.api_core import exceptions as gexc
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api import files
from api.auth.current_user import _current_user  # pyright: ignore[reportPrivateUsage]
from api.auth.firebase import AuthUser
from api.db import get_session
from api.files import MAX_FILE
from api.main import MAX_BODY, app
from api.storage import (
    GcsStorage,
    LocalStorage,
    NotFound,
    StorageUnavailable,
    get_storage,
    storage_from_env,
)
from api.tables import File, Job, User

pytestmark = pytest.mark.anyio

JPEG = b"\xff\xd8\xff\xe0jpeg bytes"
PNG = b"\x89PNG\r\n\x1a\npng bytes"
WEBP = b"RIFF\x04\x00\x00\x00WEBPwebp bytes"
GIF = b"GIF89a gif bytes"
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


def send(data: bytes, mime_type: str | None, name: str | None = None) -> dict:
    return {
        "content": data,
        "headers": {"content-type": mime_type} if mime_type is not None else {},
        "params": {"name": name} if name is not None else {},
    }


@pytest.mark.parametrize(
    ("data", "mime_type"),
    [
        (JPEG, "image/jpeg"),
        (PNG, "image/png"),
        (WEBP, "image/webp"),
        (GIF, "image/gif"),
        (b"%PDF-1.7 pdf", "application/pdf"),
        (
            b"PK\x03\x04docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (b"\x00\x01 whatever", "application/x-something-unknown"),
        (b"<html><script>alert(1)</script>", "text/html"),
        (b"<svg onload=alert(1)/>", "image/svg+xml"),
    ],
)
async def test_upload_stores_any_file_with_its_name(
    client: httpx2.AsyncClient,
    session: AsyncSession,
    storage: LocalStorage,
    data: bytes,
    mime_type: str,
):
    response = await client.post("/files", **send(data, mime_type, "My task.bin"))
    assert response.status_code == 201
    body = response.json()
    assert body == {
        "id": body["id"],
        "name": "My task.bin",
        "mimeType": mime_type,
        "size": len(data),
    }
    row = await session.get_one(File, uuid.UUID(body["id"]))
    assert row.storage_key == f"users/uid-ana/uploads/{body['id']}"
    assert (row.user_id, row.name, row.mime_type, row.size_bytes) == (
        "uid-ana",
        "My task.bin",
        mime_type,
        len(data),
    )
    assert row.extracted_text is None
    assert await storage.get(row.storage_key) == data


async def test_upload_normalizes_the_content_type(client: httpx2.AsyncClient):
    response = await client.post("/files", **send(PNG, "Image/PNG; charset=binary"))
    assert response.status_code == 201
    assert response.json()["mimeType"] == "image/png"


@pytest.mark.parametrize(
    "mime_type",
    [
        None,
        "",
        "garbage",
        "text/",
        "/plain",
        "a/b/c",
        "text plain/x",
        "text/pl ain",
        "x/" + "y" * 255,
    ],
)
async def test_upload_falls_back_to_octet_stream_for_a_bad_content_type(
    client: httpx2.AsyncClient, mime_type: str | None
):
    response = await client.post("/files", **send(b"bytes", mime_type))
    assert response.status_code == 201
    assert response.json()["mimeType"] == "application/octet-stream"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\ana\\notes.docx", "notes.docx"),
        ("a/b\\c/d.txt", "d.txt"),
        ("tab\there\x00nul\nline.pdf", "tabherenulline.pdf"),
        ("  padded name.pdf  ", "padded name.pdf"),
        ("", "file"),
        ("   ", "file"),
        ("dir/", "file"),
        ("\x01\x02", "file"),
        ("zadanie z matematyki ąę.pdf", "zadanie z matematyki ąę.pdf"),
        ("n" * 300, "n" * 255),
        ("n" * 254 + " tail", "n" * 254),
    ],
)
async def test_upload_sanitizes_the_name(
    client: httpx2.AsyncClient, session: AsyncSession, name: str, expected: str
):
    response = await client.post("/files", **send(b"bytes", "application/pdf", name))
    assert response.status_code == 201
    assert response.json()["name"] == expected
    assert await session.scalar(select(File.name)) == expected


async def test_upload_without_a_name_is_called_file(client: httpx2.AsyncClient):
    response = await client.post("/files", **send(b"bytes", "application/pdf"))
    assert response.json()["name"] == "file"


@pytest.mark.parametrize(
    ("data", "mime_type"),
    [
        (PNG, "image/jpeg"),
        (JPEG, "image/png"),
        (b"RIFF\x04\x00\x00\x00WAVEdata", "image/webp"),
        (b"just text", "image/jpeg"),
        (JPEG, "image/gif"),
        (b"GIF90a", "image/gif"),
        (PNG, "Image/JPEG; q=1"),
    ],
)
async def test_upload_refuses_an_image_whose_bytes_are_not_that_image(
    client: httpx2.AsyncClient, session: AsyncSession, data: bytes, mime_type: str
):
    response = await client.post("/files", **send(data, mime_type))
    assert response.status_code == 415
    assert isinstance(response.json()["detail"], str)
    assert await session.scalar(select(File.id)) is None


async def test_upload_refuses_an_empty_body(client: httpx2.AsyncClient):
    response = await client.post("/files", **send(b"", "application/pdf"))
    assert response.status_code == 422
    assert response.json() == {"detail": "The file is empty."}


@pytest.mark.parametrize("path", ["/files", "/api/files"])
async def test_upload_over_the_limit_is_413(client: httpx2.AsyncClient, path: str):
    assert MAX_FILE == 20 * 1024 * 1024
    ok = await client.post(
        path, content=bytes(MAX_FILE), headers={"content-type": "application/pdf"}
    )
    assert ok.status_code == 201
    big = await client.post(
        path, content=bytes(MAX_FILE + 1), headers={"content-type": "application/pdf"}
    )
    assert big.status_code == 413
    assert big.json() == {"detail": "The file is too large. Use one under 20 MB."}


@pytest.mark.parametrize("path", ["/jobs", "/api/jobs"])
async def test_the_upload_limit_does_not_raise_the_limit_of_other_routes(
    client: httpx2.AsyncClient, path: str
):
    response = await client.post(path, content=bytes(MAX_BODY + 1))
    assert response.status_code == 413
    assert response.json() == {"detail": "The request is too large"}


async def test_get_serves_an_image_inline_with_safe_headers(client: httpx2.AsyncClient):
    file_id = (await client.post("/files", **send(PNG, "image/png", "scan.png"))).json()["id"]
    response = await client.get(f"/files/{file_id}")
    assert response.status_code == 200
    assert response.content == PNG
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-disposition"] == "inline"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert response.headers["content-security-policy"] == "sandbox"


async def test_get_serves_a_pdf_as_a_download_under_its_name(client: httpx2.AsyncClient):
    data = b"%PDF-1.7 body"
    file_id = (
        await client.post("/files", **send(data, "application/pdf", "Zadania ąę.pdf"))
    ).json()["id"]
    response = await client.get(f"/files/{file_id}")
    assert response.status_code == 200
    assert response.content == data
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        "attachment; filename=\"Zadania __.pdf\"; filename*=UTF-8''Zadania%20%C4%85%C4%99.pdf"
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert response.headers["content-security-policy"] == "sandbox"


@pytest.mark.parametrize(
    "mime_type", ["text/html", "image/svg+xml", "application/xhtml+xml", "application/octet-stream"]
)
async def test_get_never_serves_other_types_inline(client: httpx2.AsyncClient, mime_type: str):
    file_id = (await client.post("/files", **send(b"<svg/>", mime_type, 'a"b%.html'))).json()["id"]
    response = await client.get(f"/files/{file_id}")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith(
        "attachment; filename=\"a_b_.html\"; filename*=UTF-8''a%22b%25.html"
    )
    assert response.headers["content-security-policy"] == "sandbox"
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_get_of_a_missing_or_another_users_file_is_404(
    client: httpx2.AsyncClient, session: AsyncSession, storage: LocalStorage
):
    session.add(User(id=BOB.uid, is_anonymous=True))
    await session.flush()
    key = f"users/{BOB.uid}/uploads/1"
    await storage.put(key, JPEG, "image/jpeg")
    theirs = File(
        user_id=BOB.uid, storage_key=key, name="x.jpg", mime_type="image/jpeg", size_bytes=len(JPEG)
    )
    session.add(theirs)
    await session.flush()
    for file_id in (theirs.id, uuid.uuid4()):
        response = await client.get(f"/files/{file_id}")
        assert response.status_code == 404
        assert isinstance(response.json()["detail"], str)


async def test_a_file_is_only_for_the_user_who_uploaded_it(client: httpx2.AsyncClient):
    file_id = (await client.post("/files", **send(JPEG, "image/jpeg"))).json()["id"]
    app.dependency_overrides[_current_user] = lambda: BOB
    assert (await client.get(f"/files/{file_id}")).status_code == 404
    app.dependency_overrides[_current_user] = lambda: ANA
    assert (await client.get(f"/files/{file_id}")).status_code == 200


async def test_the_upload_makes_the_user_row(client: httpx2.AsyncClient, session: AsyncSession):
    app.dependency_overrides[_current_user] = lambda: BOB
    assert (await client.post("/files", **send(JPEG, "image/jpeg"))).status_code == 201
    assert await session.scalar(select(User.is_anonymous).where(User.id == BOB.uid)) is True


async def test_files_need_a_signed_in_user(client: httpx2.AsyncClient):
    del app.dependency_overrides[_current_user]
    assert (await client.post("/files", **send(JPEG, "image/jpeg"))).status_code == 401
    assert (await client.get(f"/files/{uuid.uuid4()}")).status_code == 401


async def test_get_of_a_file_missing_from_storage_is_404(
    client: httpx2.AsyncClient, session: AsyncSession
):
    file_id = (await client.post("/files", **send(JPEG, "image/jpeg"))).json()["id"]
    file = await session.get_one(File, uuid.UUID(file_id))
    file.storage_key = "users/uid-ana/uploads/gone"
    await session.flush()
    assert (await client.get(f"/files/{file_id}")).status_code == 404


async def test_upload_without_storage_is_503(
    client: httpx2.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    del app.dependency_overrides[get_storage]
    monkeypatch.delenv("STORAGE_DIR", raising=False)
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.setattr(app.state, "storage", None, raising=False)
    response = await client.post("/files", **send(JPEG, "image/jpeg"))
    assert response.status_code == 503
    assert isinstance(response.json()["detail"], str)


async def test_the_contract_describes_the_upload_and_the_download(client: httpx2.AsyncClient):
    paths = (await client.get("/openapi.json")).json()["paths"]
    upload = paths["/files"]["post"]
    assert upload["requestBody"]["content"] == {
        "*/*": {"schema": {"type": "string", "format": "binary"}}
    }
    assert [(p["name"], p["in"], p["required"]) for p in upload["parameters"]] == [
        ("name", "query", False)
    ]
    assert set(paths["/files/{file_id}"]["get"]["responses"]["200"]["content"]) == {"*/*"}
    schemas = (await client.get("/openapi.json")).json()["components"]["schemas"]
    assert set(schemas["FileOut"]["properties"]) == {"id", "name", "mimeType", "size"}


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


class BrokenStorage:
    async def put(self, key: str, data: bytes, content_type: str) -> None:
        raise StorageUnavailable(key)

    async def get(self, key: str) -> bytes:
        raise StorageUnavailable(key)


async def test_a_failing_storage_is_503(client: httpx2.AsyncClient, session: AsyncSession):
    file_id = (await client.post("/files", **send(JPEG, "image/jpeg"))).json()["id"]
    app.dependency_overrides[get_storage] = lambda: BrokenStorage()
    response = await client.post("/files", **send(JPEG, "image/jpeg"))
    assert response.status_code == 503
    assert response.json()["detail"] == "Files cannot be stored right now. Try again later."
    assert await session.scalar(select(func.count()).select_from(File)) == 1
    response = await client.get(f"/files/{file_id}")
    assert response.status_code == 503
    assert response.json()["detail"] == "This file cannot be loaded right now. Try again later."


async def test_upload_is_capped_by_files_no_task_uses(
    client: httpx2.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(files, "MAX_UNATTACHED", 2)
    ids = [(await client.post("/files", **send(JPEG, "image/jpeg"))).json()["id"] for _ in range(2)]
    over = await client.post("/files", **send(JPEG, "image/jpeg"))
    assert over.status_code == 429
    assert (
        over.json()["detail"]
        == "You have too many files waiting. Send a task or remove some first."
    )
    # Another user's files do not count, and a file a task uses stops counting.
    app.dependency_overrides[_current_user] = lambda: BOB
    assert (await client.post("/files", **send(JPEG, "image/jpeg"))).status_code == 201
    app.dependency_overrides[_current_user] = lambda: ANA
    key = (await session.get_one(File, uuid.UUID(ids[0]))).storage_key
    session.add(
        Job(
            user_id=ANA.uid,
            subject="history",
            level="high_school",
            language="en",
            input_text=None,
            file_keys=[key],
        )
    )
    await session.flush()
    assert (await client.post("/files", **send(JPEG, "image/jpeg"))).status_code == 201
    assert (await client.post("/files", **send(JPEG, "image/jpeg"))).status_code == 429


class FakeBlob:
    def __init__(self, bucket: FakeBucket, key: str) -> None:
        self.bucket, self.key = bucket, key

    def upload_from_string(self, data: bytes, content_type: str, **options: object) -> None:
        self.bucket.uploads.append((self.key, data, content_type, options))
        if self.bucket.error:
            raise self.bucket.error

    def download_as_bytes(self) -> bytes:
        if self.bucket.error:
            raise self.bucket.error
        return b"data"


class FakeBucket:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.uploads: list[tuple[str, bytes, str, dict[str, object]]] = []

    def blob(self, key: str) -> FakeBlob:
        return FakeBlob(self, key)


def gcs(bucket: FakeBucket) -> GcsStorage:
    store = GcsStorage("bucket")
    store._bucket = bucket  # pyright: ignore[reportPrivateUsage]
    return store


async def test_gcs_never_overwrites():
    bucket = FakeBucket()
    await gcs(bucket).put("k", b"x", "text/plain")
    assert bucket.uploads == [("k", b"x", "text/plain", {"if_generation_match": 0})]
    assert await gcs(bucket).get("k") == b"data"


async def test_gcs_maps_a_missing_object_to_not_found():
    store = gcs(FakeBucket(gexc.NotFound("No such object: bucket/k")))
    with pytest.raises(NotFound):
        await store.get("k")


@pytest.mark.parametrize(
    "error",
    [
        gexc.NotFound("The specified bucket does not exist."),
        gexc.Forbidden("no access"),
        gexc.ServiceUnavailable("down"),
        gexc.PreconditionFailed("exists"),
        ConnectionError("reset"),
    ],
)
async def test_gcs_maps_other_failures_to_unavailable(error: Exception):
    store = gcs(FakeBucket(error))
    with pytest.raises(StorageUnavailable):
        await store.get("k")
    with pytest.raises(StorageUnavailable):
        await store.put("k", b"x", "text/plain")


async def test_local_storage_does_not_overwrite(storage: LocalStorage):
    await storage.put("a/b", b"1", "text/plain")
    with pytest.raises(FileExistsError):
        await storage.put("a/b", b"2", "text/plain")
    assert await storage.get("a/b") == b"1"
