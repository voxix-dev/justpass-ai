from fastapi.testclient import TestClient

from api.main import app


def test_health():
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_behind_api_prefix():
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_prefix_kept_in_redirects():
    response = TestClient(app).get("/api/health/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].endswith("/api/health")


def test_api_prefix_kept_in_docs():
    client = TestClient(app)
    assert "/api/openapi.json" in client.get("/api/docs").text
    assert client.get("/api/openapi.json").status_code == 200
