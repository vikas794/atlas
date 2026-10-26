"""Editable settings: DB layer, precedence, the /api/settings routes and live apply."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.config import load_settings
from src.config.settings import read_layers
from src.infrastructure.storage.settings_repo import SqlSettingsRepository
from src.transport.http.fastapi import dependencies as deps


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import settings as settings_module

    monkeypatch.setattr(settings_module, "load_dotenv", lambda *a, **k: None)
    for name in list(__import__("os").environ):
        # Keep ATLAS_DATABASE_PATH: conftest points it at a per-test database, and these
        # tests write settings rows.
        if name.startswith("ATLAS_") and name != "ATLAS_DATABASE_PATH":
            monkeypatch.delenv(name)


@pytest.fixture
def client() -> TestClient:
    from backend.main import app

    return TestClient(app)


def _fields(payload: dict) -> dict:
    return {f["key"]: f for f in payload["fields"]}


def test_a_database_without_the_table_or_file_means_no_saved_settings() -> None:
    assert read_layers().db == {}


def test_saved_settings_sit_between_yaml_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = deps.get_settings_repo()
    repo.replace({"max_concurrent_videos": 5, "backoff_base_seconds": 7.5})

    assert load_settings().max_concurrent_videos == 5
    monkeypatch.setenv("ATLAS_MAX_CONCURRENT_VIDEOS", "3")
    assert load_settings().max_concurrent_videos == 3  # env still wins
    assert load_settings().backoff_base_seconds == 7.5


def test_repository_round_trip_keeps_types_and_replaces_atomically() -> None:
    repo = SqlSettingsRepository(deps.get_storage_settings()["database_path"])
    repo.replace({"max_auto_retries": 2, "log_file_logging": False})
    assert repo.load_all() == {"max_auto_retries": 2, "log_file_logging": False}
    repo.replace({"max_auto_retries": 9})
    assert repo.load_all() == {"max_auto_retries": 9}
    repo.clear()
    assert repo.load_all() == {}


def test_get_lists_fields_with_source_and_live_flag(client: TestClient) -> None:
    body = client.get("/api/settings").json()
    fields = _fields(body)
    assert fields["max_concurrent_videos"]["apply"] == "live"
    assert fields["max_concurrent_videos"]["source"] in {"default", "yaml"}
    assert fields["port"]["apply"] == "restart"
    assert fields["database_path"]["editable"] is False
    assert not any(k.endswith("_api_key_env") for k in fields)
    assert body["restart_required"] is False


def test_put_saves_and_the_value_is_effective_and_marked_db(client: TestClient) -> None:
    response = client.put(
        "/api/settings",
        json={"values": {"max_concurrent_videos": 1, "notes_request_timeout_seconds": 120}},
    )
    assert response.status_code == 200
    fields = _fields(response.json())
    assert fields["max_concurrent_videos"]["value"] == 1
    assert fields["max_concurrent_videos"]["source"] == "db"
    assert fields["notes_request_timeout_seconds"]["value"] == 120.0
    assert load_settings().max_concurrent_videos == 1


def test_a_value_equal_to_the_default_removes_the_saved_row(client: TestClient) -> None:
    default = _fields(client.get("/api/settings").json())["max_auto_retries"]["default"]
    client.put("/api/settings", json={"values": {"max_auto_retries": default + 1}})
    assert "max_auto_retries" in deps.get_settings_repo().load_all()
    client.put("/api/settings", json={"values": {"max_auto_retries": default}})
    assert "max_auto_retries" not in deps.get_settings_repo().load_all()


def test_invalid_values_are_422_per_field_and_nothing_is_written(client: TestClient) -> None:
    response = client.put(
        "/api/settings",
        json={
            "values": {
                "max_concurrent_videos": 0,
                "backoff_base_seconds": 50,
                "backoff_cap_seconds": 10,
                "max_auto_retries": "lots",
            }
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    fields = body["details"]["fields"]
    assert set(fields) >= {"max_concurrent_videos", "backoff_cap_seconds", "max_auto_retries"}
    assert deps.get_settings_repo().load_all() == {}


def test_unknown_and_secret_keys_are_rejected(client: TestClient) -> None:
    response = client.put(
        "/api/settings",
        json={"values": {"youtube_api_key_env": "X", "nope": 1, "database_path": "x.db"}},
    )
    assert response.status_code == 422
    assert set(response.json()["details"]["fields"]) == {
        "youtube_api_key_env",
        "nope",
        "database_path",
    }


def test_an_env_locked_setting_is_flagged_and_cannot_be_saved(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATLAS_MAX_CONCURRENT_VIDEOS", "4")
    field = _fields(client.get("/api/settings").json())["max_concurrent_videos"]
    assert field["locked"] is True and field["source"] == "env" and field["value"] == 4

    response = client.put("/api/settings", json={"values": {"max_concurrent_videos": 1}})
    assert response.status_code == 422
    assert "max_concurrent_videos" in response.json()["details"]["fields"]


def test_a_saved_restart_only_setting_raises_restart_required(client: TestClient) -> None:
    deps.get_boot_settings()  # what the process "started with"
    body = client.put("/api/settings", json={"values": {"port": 9123}}).json()
    assert body["restart_required"] is True


def test_delete_resets_to_defaults(client: TestClient) -> None:
    client.put("/api/settings", json={"values": {"max_concurrent_videos": 7}})
    body = client.delete("/api/settings").json()
    assert _fields(body)["max_concurrent_videos"]["source"] in {"default", "yaml"}
    assert deps.get_settings_repo().load_all() == {}


def test_saving_updates_the_running_worker_and_use_case(
    client: TestClient, idle_worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = idle_worker
    monkeypatch.setattr(deps, "_worker", worker)  # apply_settings updates the live singleton
    response = client.put(
        "/api/settings",
        json={
            "values": {
                "max_concurrent_videos": 1,
                "max_auto_retries": 9,
                "backoff_cap_seconds": 400,
                "worker_shutdown_timeout_seconds": 1.5,
            }
        },
    )
    assert response.status_code == 200, response.text
    assert worker.max_concurrent_videos == 1
    assert worker.shutdown_timeout_seconds == 1.5
    assert worker.process_video.max_auto_retries == 9
    assert worker.process_video.backoff_cap_seconds == 400.0


def test_the_removed_provider_and_model_settings_cannot_be_saved(client: TestClient) -> None:
    """Provider, model and key choice moved to the model registry (/api/llm)."""
    response = client.put(
        "/api/settings",
        json={
            "values": {
                "notes_provider": "freellmapi",
                "gemini_model": "x",
                "nvidia_request_timeout_seconds": 5,
            }
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert set(body["details"]["fields"]) == {
        "notes_provider",
        "gemini_model",
        "nvidia_request_timeout_seconds",
    }
    assert deps.get_settings_repo().load_all() == {}


def test_the_settings_list_has_no_provider_or_model_fields(client: TestClient) -> None:
    keys = {f["key"] for f in client.get("/api/settings").json()["fields"]}

    assert "notes_prompt_name" in keys and "notes_request_timeout_seconds" in keys
    assert not {k for k in keys if k.startswith(("gemini_", "openrouter_", "freellmapi_", "nvidia_"))}
    assert not keys & {"notes_provider", "notes_model", "notes_models"}


def test_the_old_model_list_endpoint_is_gone(client: TestClient) -> None:
    response = client.get("/api/settings/models", params={"provider": "gemini"})

    assert response.status_code == 404


def test_the_notes_prompt_endpoint_returns_the_current_prompt_file(client: TestClient) -> None:
    body = client.get("/api/settings/notes-prompt").json()

    assert "{{Title}}" in body["prompt"] and "{{Transcript}}" in body["prompt"]
    assert body["version"] == "notes_v1.2"
