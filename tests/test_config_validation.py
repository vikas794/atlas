"""Startup validation: a bad configuration fails fast with a message a human can act on."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.main import app, install_cors
from src.config.settings import ENV_ALIASES, SettingsError, load_settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("ATLAS_") or name in ENV_ALIASES:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("src.config.settings.load_dotenv", lambda *a, **k: False)


def write_yaml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def problems_for(path: Path) -> str:
    with pytest.raises(SettingsError) as excinfo:
        load_settings(path)
    message = str(excinfo.value)
    assert message.startswith("Atlas cannot start: invalid configuration")
    assert "Traceback" not in message and "pydantic" not in message.lower()
    return message


def test_the_shipped_config_is_valid() -> None:
    load_settings()


def test_unknown_keys_are_reported_instead_of_silently_dropped(tmp_path: Path) -> None:
    message = problems_for(write_yaml(tmp_path, "max_concurent_videos: 3\n"))
    assert "unknown setting 'max_concurent_videos'" in message


def test_wrong_types_are_reported(tmp_path: Path) -> None:
    message = problems_for(write_yaml(tmp_path, "max_concurrent_videos: two\nlog_file_logging: 1\n"))
    assert "max_concurrent_videos: expected a whole number (got 'two')" in message
    assert "log_file_logging: expected true or false" in message


def test_invalid_yaml_is_reported(tmp_path: Path) -> None:
    assert "not valid YAML" in problems_for(write_yaml(tmp_path, "a: [unclosed\n"))


def test_a_nested_layout_is_reported(tmp_path: Path) -> None:
    assert "unknown setting 'api'" in problems_for(write_yaml(tmp_path, "api:\n  model: x\n"))


@pytest.mark.parametrize(
    ("env", "value", "expected"),
    [
        ("ATLAS_MAX_CONCURRENT_VIDEOS", "0", "max_concurrent_videos: must be at least 1"),
        ("ATLAS_MAX_AUTO_RETRIES", "-1", "max_auto_retries: must be 0 or more"),
        ("ATLAS_BACKOFF_CAP_SECONDS", "1", "backoff_cap_seconds: must be at least backoff_base"),
        ("ATLAS_LOG_LEVEL", "LOUD", "log_level: must be one of"),
        ("ATLAS_CORS_ORIGINS", "*", "wildcards are not allowed"),
        ("ATLAS_WORKER_POLL_INTERVAL_SECONDS", "0", "worker_poll_interval_seconds: must be greater"),
        ("NOTES_PROMPT_NAME", "missing.yaml", "prompt file 'missing.yaml' not found"),
        ("ATLAS_PORT", "70000", "port: must be between 1 and 65535"),
    ],
)
def test_out_of_range_values_are_reported(
    monkeypatch: pytest.MonkeyPatch, env: str, value: str, expected: str
) -> None:
    monkeypatch.setenv(env, value)
    with pytest.raises(SettingsError) as excinfo:
        load_settings()
    assert expected in str(excinfo.value)


def test_all_problems_are_reported_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_MAX_CONCURRENT_VIDEOS", "0")
    monkeypatch.setenv("ATLAS_PORT", "0")
    with pytest.raises(SettingsError) as excinfo:
        load_settings()
    assert len(excinfo.value.problems) == 2


def test_missing_api_keys_do_not_stop_startup(monkeypatch: pytest.MonkeyPatch, api_env) -> None:
    for key in ("GEMINI_API_KEY", "YOUTUBE_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    load_settings()
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200


def test_env_example_holds_only_secrets_and_no_values() -> None:
    from dotenv import dotenv_values

    from src.config.settings import repo_root

    template = dotenv_values(repo_root() / ".env.example")
    assert set(template) == {"YOUTUBE_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "FREELLMAPI_API_KEY", "NVIDIA_API_KEY"}
    assert all(not value for value in template.values())


def test_secrets_are_not_settings_fields() -> None:
    settings = load_settings()
    text = repr(settings)
    for key in ("GEMINI_API_KEY", "YOUTUBE_API_KEY", "OPENROUTER_API_KEY"):
        value = os.environ.get(key)
        if value:
            assert value not in text


# --- CORS ----------------------------------------------------------------------------------


def test_the_app_sends_no_cors_headers_by_default() -> None:
    client = TestClient(app)
    resp = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in resp.headers
    preflight = client.options(
        "/api/playlists",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in preflight.headers


def test_configured_origins_are_the_only_ones_allowed() -> None:
    probe = FastAPI()

    @probe.get("/ping")
    def ping() -> dict:
        return {}

    install_cors(probe, ["http://localhost:5174"])
    client = TestClient(probe)
    ok = client.get("/ping", headers={"Origin": "http://localhost:5174"})
    assert ok.headers["access-control-allow-origin"] == "http://localhost:5174"
    assert "access-control-allow-credentials" not in ok.headers
    other = client.get("/ping", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers
