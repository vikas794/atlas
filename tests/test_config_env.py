"""Environment configuration (M5): the documented names in .env.example must really work."""

from __future__ import annotations

import logging
import os
from dataclasses import fields
from pathlib import Path

import pytest
from dotenv import dotenv_values

from src.config.settings import (
    ENV_ALIASES,
    AtlasSettings,
    SettingsError,
    load_settings,
    repo_root,
)
from src.infrastructure.llm.notes_base import NotesPromptSource


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No ATLAS_* or documented override from the developer's shell or .env leaks in."""
    for name in list(os.environ):
        if name.startswith("ATLAS_") or name in ENV_ALIASES:
            monkeypatch.delenv(name, raising=False)
    # A developer's real repo .env must not influence these tests either.
    monkeypatch.setattr("src.config.settings.load_dotenv", lambda *a, **k: False)
    # Nor the saved settings of the developer's real database.
    monkeypatch.setenv("ATLAS_DATABASE_PATH", str(tmp_path / "no-such.sqlite3"))


def test_the_prompt_name_has_a_documented_short_env_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTES_PROMPT_NAME", "notes_v1_3.yaml")

    assert load_settings().notes_prompt_name == "notes_v1_3.yaml"
    assert load_settings().to_loader()("notes_prompt_name") == "notes_v1_3.yaml"


def test_llm_provider_and_model_settings_no_longer_exist() -> None:
    names = {f.name for f in fields(AtlasSettings)}

    for gone in (
        "notes_provider", "notes_model", "notes_models", "gemini_model", "gemini_api_key_env",
        "openrouter_model", "openrouter_base_url", "freellmapi_model", "freellmapi_base_url",
        "freellmapi_models_url", "nvidia_model", "nvidia_request_timeout_seconds",
        "nvidia_api_key_env", "openrouter_api_key_env", "freellmapi_api_key_env",
    ):
        assert gone not in names, gone
    assert "youtube_api_key_env" in names and "notes_request_timeout_seconds" in names


def test_old_provider_variables_in_the_environment_are_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = load_settings()
    for name in ("NOTES_PROVIDER", "NOTES_MODEL", "NOTES_MODELS", "GEMINI_MODEL", "NVIDIA_MODEL"):
        monkeypatch.setenv(name, "something")
        monkeypatch.setenv(f"ATLAS_{name}", "something")

    assert load_settings() == baseline


def test_a_config_yaml_that_still_sets_a_removed_provider_setting_is_reported(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("notes_provider: gemini\ngemini_model: x\n", encoding="utf-8")

    with pytest.raises(SettingsError) as excinfo:
        load_settings(config)

    text = "\n".join(excinfo.value.problems)
    assert "unknown setting 'notes_provider'" in text and "unknown setting 'gemini_model'" in text


def test_saved_values_of_removed_settings_do_not_stop_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.infrastructure.storage.db import connect, initialize

    db = tmp_path / "atlas.sqlite3"
    initialize(db)
    conn = connect(db)
    conn.execute(
        "INSERT INTO app_settings (key, value_json, updated_at) VALUES "
        "('notes_provider', '\"freellmapi\"', 'x'), ('max_concurrent_videos', '1', 'x')"
    )
    conn.close()
    monkeypatch.setenv("ATLAS_DATABASE_PATH", str(db))

    settings = load_settings()

    assert settings.max_concurrent_videos == 1  # the still-valid row applies
    assert not hasattr(settings, "notes_provider")


def test_the_settings_loader_reads_the_environment_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = load_settings().to_loader()
    assert loader("notes_prompt_name") == "notes_v1_3.yaml"

    monkeypatch.setenv("NOTES_PROMPT_NAME", "other.yaml")

    assert loader("notes_prompt_name") == "other.yaml"


def test_the_atlas_prefixed_name_wins_over_the_documented_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOTES_PROMPT_NAME", "alias.yaml")
    monkeypatch.setenv("ATLAS_NOTES_PROMPT_NAME", "prefixed.yaml")

    assert AtlasSettings().to_loader()("notes_prompt_name") == "prefixed.yaml"


def test_empty_values_count_as_unset_so_copying_env_example_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = load_settings()
    template = dotenv_values(repo_root() / ".env.example")
    assert template, ".env.example should define keys"
    for key, value in template.items():
        monkeypatch.setenv(key, value or "")  # `KEY=` in a .env file yields ''

    loaded = load_settings()

    assert loaded == baseline
    loader = loaded.to_loader()
    for f in fields(AtlasSettings):
        assert loader(f.name) == getattr(baseline, f.name), f.name


def test_whitespace_only_values_are_also_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_MAX_CONCURRENT_VIDEOS", "   ")
    monkeypatch.setenv("NOTES_PROMPT_NAME", "  ")

    settings = load_settings()

    assert settings.max_concurrent_videos == AtlasSettings().max_concurrent_videos
    assert settings.notes_prompt_name == AtlasSettings().notes_prompt_name


def test_numeric_values_are_coerced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_MAX_CONCURRENT_VIDEOS", "4")
    monkeypatch.setenv("ATLAS_BACKOFF_BASE_SECONDS", "0.5")

    settings = load_settings()

    assert settings.max_concurrent_videos == 4 and isinstance(settings.max_concurrent_videos, int)
    assert settings.backoff_base_seconds == 0.5


def test_a_malformed_number_fails_startup_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It used to be ignored with a warning, silently running with a different value."""
    monkeypatch.setenv("ATLAS_MAX_AUTO_RETRIES", "lots")

    with pytest.raises(SettingsError) as excinfo:
        load_settings()

    assert "ATLAS_MAX_AUTO_RETRIES='lots'" in str(excinfo.value)


def test_the_runtime_loader_still_only_warns_on_a_malformed_number(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    loader = load_settings().to_loader()
    monkeypatch.setenv("ATLAS_MAX_AUTO_RETRIES", "lots")

    with caplog.at_level(logging.WARNING):
        assert loader("max_auto_retries") == AtlasSettings().max_auto_retries

    assert "ATLAS_MAX_AUTO_RETRIES" in caplog.text


def test_string_values_that_look_like_numbers_stay_strings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The old JSON coercion turned NOTES_PROMPT_NAME=1.5 into a float."""
    monkeypatch.setenv("NOTES_PROMPT_NAME", "1.5")
    monkeypatch.delenv("ATLAS_DATABASE_PATH", raising=False)
    monkeypatch.setenv("ATLAS_DB_PATH", "123")

    settings = AtlasSettings().to_loader()

    assert settings("notes_prompt_name") == "1.5"
    assert settings("database_path") == "123"


def test_boolean_values_are_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_LOG_FILE_LOGGING", "true")
    assert load_settings().log_file_logging is True
    monkeypatch.setenv("ATLAS_LOG_FILE_LOGGING", "0")
    assert load_settings().log_file_logging is False


def test_legacy_db_path_name_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATLAS_DATABASE_PATH", raising=False)
    monkeypatch.setenv("ATLAS_DB_PATH", "custom/atlas.sqlite3")

    assert load_settings().database_path == "custom/atlas.sqlite3"


def test_every_key_in_env_example_is_actually_read() -> None:
    """The defect: .env.example listed names that only ATLAS_<FIELD> honoured; each must be read."""
    field_names = {f.name for f in fields(AtlasSettings)}
    recognised = (
        {f"ATLAS_{name.upper()}" for name in field_names}
        | set(ENV_ALIASES)
        | {"ATLAS_DB_PATH"}  # legacy alias of ATLAS_DATABASE_PATH
        | {"GEMINI_API_KEY", "OPENROUTER_API_KEY", "FREELLMAPI_API_KEY", "NVIDIA_API_KEY", "OPENAI_API_KEY", "YOUTUBE_API_KEY"}  # secrets, read by name
    )

    unknown = [key for key in dotenv_values(repo_root() / ".env.example") if key not in recognised]

    assert unknown == []


def test_env_example_says_the_youtube_key_is_required_not_optional() -> None:
    lines = (repo_root() / ".env.example").read_text(encoding="utf-8").splitlines()
    index = lines.index("YOUTUBE_API_KEY=")
    comment = " ".join(lines[max(0, index - 3):index]).lower()

    assert "required" in comment
    assert "optional" not in comment


def test_dead_settings_are_gone() -> None:
    names = {f.name for f in fields(AtlasSettings)}

    for dead in (
        "cache_ttl_days",
        "artifact_root",
        "cleanup_retention_days",
        "transcript_failure_cooldown_seconds",
        "worker_queue_size",
        "notes_prompt_version",
    ):
        assert dead not in names, dead


# ------------------------------------------------------------------ prompt version


def test_the_recorded_prompt_version_comes_from_the_prompt_file_itself(
    tmp_path: Path,
) -> None:
    (tmp_path / "custom.yaml").write_text(
        "version: custom_v7\nprompt: 'Title {{Title}} Transcript {{Transcript}}'\n", encoding="utf-8"
    )
    loader = AtlasSettings(prompts_base_dir=str(tmp_path), notes_prompt_name="custom.yaml").to_loader()

    assert NotesPromptSource(loader).version() == "custom_v7"


def test_a_prompt_file_without_a_version_falls_back_to_a_content_hash(tmp_path: Path) -> None:
    (tmp_path / "p.yaml").write_text("prompt: '{{Title}} {{Transcript}}'\n", encoding="utf-8")
    loader = AtlasSettings(prompts_base_dir=str(tmp_path), notes_prompt_name="p.yaml").to_loader()

    version = NotesPromptSource(loader).version()

    assert len(version) == 8 and version.isalnum()


def test_there_is_no_separate_prompt_version_setting_to_drift_from_the_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATLAS_NOTES_PROMPT_VERSION", "something_else")

    assert NotesPromptSource(load_settings().to_loader()).version() == "notes_v1.3"


def test_the_shipped_prompt_requires_tagged_code_blocks_and_no_title_line() -> None:
    source = NotesPromptSource(load_settings().to_loader())
    template = source.template()

    assert source.path.name == "notes_v1_3.yaml"
    assert "{{Title}}" in template and "{{Transcript}}" in template
    assert "language" in template and "fenced code block" in template.lower()
    assert "title" in template.lower() and "Do not repeat the video title" in template
    for section in ("Summary", "Questions and Answers", "Notes", "History"):
        assert section in template
