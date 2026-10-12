"""Smoke-check the PRD error envelope and the surviving route surface."""

from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_health() -> None:
    assert client.get("/api/health").json() == {"status": "ok", "service": "atlas-api"}


def test_framework_404_uses_envelope() -> None:
    resp = client.get("/api/definitely-not-a-route")
    assert resp.status_code == 404
    body = resp.json()
    assert set(body) == {"code", "message", "details"}
    assert body["code"] == "HTTP_404"


def test_legacy_routes_are_gone() -> None:
    for path in ("/api/runs", "/api/runs/latest", "/api/pipeline/search", "/api/quiz/drive-status"):
        assert client.get(path).status_code == 404, path


def test_drive_status_envelope() -> None:
    resp = client.get("/api/drive/status")
    assert resp.status_code == 200
    assert set(resp.json()) == {"configured", "message", "state"}


def test_bad_upload_returns_envelope() -> None:
    resp = client.post("/api/drive/credentials", files={"file": ("x.txt", b"nope", "text/plain")})
    assert resp.status_code == 400
    body = resp.json()
    assert set(body) == {"code", "message", "details"}


def test_usage_endpoint_alive() -> None:
    assert client.get("/api/usage").status_code == 200


def test_dev_shutdown_endpoint_is_gone() -> None:
    """It hard-exited the process (os._exit) and skipped the worker's graceful stop."""
    resp = client.post("/api/dev/shutdown", headers={"X-Atlas-Dev-Token": "anything"})
    assert resp.status_code == 404
    assert resp.json()["code"] == "HTTP_404"
