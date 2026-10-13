"""GET /api/diagnostics: the exact contract the Diagnostics page is built against."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.main as main_module
from backend.main import app
from src.shared.logging_setup import RECENT_ERRORS

CHECK_IDS = [
    "config",
    "database",
    "youtube_key",
    "llm_key",
    "drive_credentials",
    "drive_token",
    "frontend_build",
]
YT = "AIzaSyYOUTUBE1234567890abcdefghijklmnop"
GEMINI = "AIzaSyGEMINI1234567890abcdefghijklmnop"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, api_env):
    """Temp DB/creds/token/dist; no developer .env; keys controlled per test."""
    monkeypatch.setattr("src.config.settings.load_dotenv", lambda *a, **k: False)
    for name in ("YOUTUBE_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "NOTES_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ATLAS_GOOGLE_CREDS_PATH", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("ATLAS_GOOGLE_TOKEN_PATH", str(tmp_path / "token.json"))
    dist = tmp_path / "dist"
    monkeypatch.setattr(main_module, "default_dist_dir", lambda: dist)
    RECENT_ERRORS.clear()
    return tmp_path


def get_report() -> dict:
    with TestClient(app) as client:  # lifespan runs: the database is created and migrated
        resp = client.get("/api/diagnostics")
    assert resp.status_code == 200
    return resp.json()


def by_id(report: dict) -> dict[str, dict]:
    return {check["id"]: check for check in report["checks"]}


def test_contract_shape_and_check_ids(env) -> None:
    report = get_report()

    assert set(report) == {"checks", "recent_errors"}
    assert [c["id"] for c in report["checks"]] == CHECK_IDS
    for check in report["checks"]:
        assert set(check) == {"id", "label", "status", "message"}
        assert check["status"] in {"ok", "warning", "error"}
        assert check["label"] and check["message"]
    for entry in report["recent_errors"]:
        assert set(entry) == {"timestamp", "level", "logger", "message"}


def test_first_run_without_keys_or_drive(env) -> None:
    checks = by_id(get_report())

    assert checks["config"]["status"] == "ok"
    assert checks["database"]["status"] == "ok"
    assert checks["youtube_key"]["status"] == "error"
    assert "YOUTUBE_API_KEY" in checks["youtube_key"]["message"]
    assert checks["llm_key"]["status"] == "error"
    assert "GEMINI_API_KEY" in checks["llm_key"]["message"]
    assert checks["drive_credentials"]["status"] == "warning"
    assert checks["drive_token"]["status"] == "warning"
    assert checks["frontend_build"]["status"] == "warning"


def test_everything_configured_is_ok_and_no_secret_is_returned(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YOUTUBE_API_KEY", YT)
    monkeypatch.setenv("GEMINI_API_KEY", GEMINI)
    creds = {"installed": {"client_id": "id.apps.googleusercontent.com", "client_secret": "GOCSPX-s3cret"}}
    (env / "credentials.json").write_text(json.dumps(creds), encoding="utf-8")
    (env / "dist").mkdir()
    (env / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")

    report = get_report()
    checks = by_id(report)

    for check_id in ("config", "database", "youtube_key", "llm_key", "drive_credentials", "frontend_build"):
        assert checks[check_id]["status"] == "ok", checks[check_id]
    text = json.dumps(report)
    for secret in (YT, GEMINI, "GOCSPX-s3cret"):
        assert secret not in text


def test_the_llm_check_follows_the_configured_provider(env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTES_PROVIDER", "openrouter")
    monkeypatch.setenv("GEMINI_API_KEY", GEMINI)
    check = by_id(get_report())["llm_key"]
    assert check["status"] == "error"
    assert "OPENROUTER_API_KEY" in check["message"]


def test_an_invalid_credentials_file_is_an_error(env: Path) -> None:
    (env / "credentials.json").write_text('{"type": "service_account"}', encoding="utf-8")
    assert by_id(get_report())["drive_credentials"]["status"] == "error"


def test_an_unusable_token_is_a_warning(env: Path) -> None:
    (env / "token.json").write_text("not json", encoding="utf-8")
    assert by_id(get_report())["drive_token"]["status"] == "warning"


def test_an_invalid_config_is_reported_not_raised(env, monkeypatch: pytest.MonkeyPatch) -> None:
    with TestClient(app) as client:
        monkeypatch.setenv("ATLAS_MAX_CONCURRENT_VIDEOS", "0")  # broken after startup
        checks = by_id(client.get("/api/diagnostics").json())
    assert checks["config"]["status"] == "error"
    assert "max_concurrent_videos" in checks["config"]["message"]
    assert checks["database"]["message"].startswith("Not checked")


def test_recent_errors_are_redacted_newest_first(env) -> None:
    logging.getLogger("src.test.diag").warning("first")
    logging.getLogger("src.test.diag").error("Gemini rejected key=%s", GEMINI)

    report = get_report()
    errors = [e for e in report["recent_errors"] if e["logger"] == "src.test.diag"]

    assert [e["level"] for e in errors] == ["ERROR", "WARNING"]
    assert GEMINI not in json.dumps(report)
    datetime.fromisoformat(errors[0]["timestamp"])


def test_health_stays_a_cheap_liveness_probe(env) -> None:
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok", "service": "atlas-api"}


def test_a_revoked_drive_token_is_not_reported_ok(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """A token that only *looks* refreshable on disk is verified, not trusted."""
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials

    from tests.test_drive_auth import write_expired_token

    (env / "credentials.json").write_text('{"installed": {}}', encoding="utf-8")
    write_expired_token(env / "token.json")

    def refresh(self: Credentials, request: object) -> None:
        raise RefreshError("invalid_grant: Bad Request", {"error": "invalid_grant"})

    monkeypatch.setattr(Credentials, "refresh", refresh)

    check = by_id(get_report())["drive_token"]
    assert check["status"] == "warning"
    assert "Re-authorize Drive" in check["message"]
    assert "invalid_grant" not in check["message"]
