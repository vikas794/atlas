"""POST /api/drive/credentials: size limit, JSON validation, fixed destination, atomic write."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from src.transport.http.fastapi import dependencies as deps
from src.transport.http.fastapi.routers.drive_auth import MAX_CREDENTIALS_BYTES

VALID = {
    "installed": {
        "client_id": "123.apps.googleusercontent.com",
        "client_secret": "GOCSPX-test",
        "redirect_uris": ["http://localhost"],
    }
}


@pytest.fixture
def creds_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, api_env) -> Path:
    target = tmp_path / "secrets" / "credentials.json"
    monkeypatch.setenv("ATLAS_GOOGLE_CREDS_PATH", str(target))
    monkeypatch.setenv("ATLAS_GOOGLE_TOKEN_PATH", str(tmp_path / "secrets" / "token.json"))
    deps.reset_dependency_caches()
    return target


def upload(name: str, content: bytes):
    client = TestClient(app, raise_server_exceptions=False)
    return client.post("/api/drive/credentials", files={"file": (name, content, "application/json")})


def test_a_valid_client_file_is_stored_at_the_configured_path(creds_path: Path) -> None:
    resp = upload("client_secret_123.json", json.dumps(VALID).encode())
    assert resp.status_code == 200, resp.text
    assert json.loads(creds_path.read_text(encoding="utf-8")) == VALID
    assert not list(creds_path.parent.glob("*.tmp-*")), "temp file left behind"


def test_the_upload_filename_cannot_choose_the_destination(creds_path: Path) -> None:
    resp = upload("../../evil.json", json.dumps(VALID).encode())
    assert resp.status_code == 200
    assert creds_path.exists()
    assert not (creds_path.parent.parent.parent / "evil.json").exists()


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("creds.txt", json.dumps(VALID).encode()),
        ("creds.json", b"{not json"),
        ("creds.json", b"\xff\xfe\x00garbage"),
        ("creds.json", b"[1, 2, 3]"),
        ("creds.json", json.dumps({"type": "service_account"}).encode()),
        ("creds.json", json.dumps({"installed": {"client_id": "x"}}).encode()),
    ],
)
def test_invalid_uploads_are_rejected_and_keep_the_old_file(
    creds_path: Path, name: str, content: bytes
) -> None:
    creds_path.parent.mkdir(parents=True)
    creds_path.write_text(json.dumps(VALID), encoding="utf-8")

    resp = upload(name, content)

    assert resp.status_code == 400
    assert resp.json()["code"] == "DRIVE_INVALID_CREDENTIALS_FILE"
    assert json.loads(creds_path.read_text(encoding="utf-8")) == VALID


def test_oversized_uploads_are_rejected(creds_path: Path) -> None:
    resp = upload("creds.json", b" " * (MAX_CREDENTIALS_BYTES + 1))
    assert resp.status_code == 413
    assert resp.json()["code"] == "DRIVE_CREDENTIALS_TOO_LARGE"
    assert not creds_path.exists()


def test_drive_status_never_returns_secret_values(creds_path: Path) -> None:
    upload("creds.json", json.dumps(VALID).encode())
    body = TestClient(app).get("/api/drive/status").text
    assert "GOCSPX" not in body and "client_secret" not in body
