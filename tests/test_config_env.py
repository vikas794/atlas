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
from src.infrastructure.llm.factory import build_notes_generator
from src.infrastructure.llm.models import DEFAULT_OPENROUTER_MODEL, resolve_model_specs
from src.infrastructure.llm.notes_base import NotesPromptSource

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ATLAS_* or documented override from the developer's shell or .env leaks in."""
    for name in list(os.environ):
        if name.startswith("ATLAS_") or name in ENV_ALIASES:
            monkeypatch.delenv(name, raising=False)
    # A developer's real repo .env must not influence these tests either.
    monkeypatch.setattr("src.config.settings.load_dotenv", lambda *a, **k: False)


def _primary(monkeypatch_unused: object = None):
    """The first (primary) model spec the factory builds from the current settings."""
    return build_notes_generator(load_settings().to_loader()).specs[0]


def test_notes_provider_env_selects_the_openrouter_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTES_PROVIDER", "openrouter")

    assert _primary().provider == "openrouter"


def test_without_overrides_the_default_provider_is_gemini() -> None:
    assert _primary().provider == "gemini"


def test_documented_llm_variables_reach_the_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTES_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-pro")
    monkeypatch.setenv("GEMINI_INPUT_TOKEN_LIMIT", "12345")
    gemini = _primary()
    assert gemini.model == "gemini-2.5-pro"
    assert gemini.input_token_limit == 12345  # a real int, not a string

    monkeypatch.setenv("NOTES_MODEL", "gemini-custom")  # NOTES_MODEL beats GEMINI_MODEL
    assert _primary().model == "gemini-custom"

    monkeypatch.setenv("NOTES_PROVIDER", "openrouter")
    monkeypatch.delenv("NOTES_MODEL")
    monkeypatch.setenv("OPENROUTER_MODEL", "anthropic/claude-sonnet")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://proxy.example/v1")
    router = _primary()
    assert router.model == "anthropic/claude-sonnet"
    assert router.api_base == "https://proxy.example/v1"


def test_openrouter_defaults_when_nothing_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTES_PROVIDER", "openrouter")

    router = _primary()

    assert router.model == DEFAULT_OPENROUTER_MODEL
    assert router.api_base == OPENROUTER_BASE_URL


def test_notes_models_env_is_a_json_list_and_beats_the_legacy_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOTES_PROVIDER", "openrouter")
    monkeypatch.setenv(
        "NOTES_MODELS",
        '[{"model": "openai/gpt-5-mini", "api_base": "http://localhost:20128/v1"},'
        ' {"model": "gemini/gemini-2.5-flash"}]',
    )

    settings = load_settings()
    first, second = resolve_model_specs(settings.to_loader())

    assert settings.notes_models[0]["model"] == "openai/gpt-5-mini"
    assert (first.provider, first.api_base) == ("openai", "http://localhost:20128/v1")
    assert second.litellm_model == "gemini/gemini-2.5-flash"


@pytest.mark.parametrize("raw", ["not json", '{"model": "gemini/x"}'])
def test_notes_models_env_must_be_a_json_list(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("NOTES_MODELS", raw)

    with pytest.raises(SettingsError, match="NOTES_MODELS"):
        load_settings()


def test_notes_models_entries_are_validated(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "notes_models:",
                "  - model: gemini/gemini-2.5-flash",
                "  - model: gpt-5",
                "  - model: openai/gpt-5-mini",
                "    temperature: 0.2",
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(SettingsError) as excinfo:
        load_settings(config)

    text = "\n".join(excinfo.value.problems)
    assert "notes_models[2].model" in text
    assert "notes_models[3]: unknown key(s) temperature" in text


def test_the_settings_loader_reads_the_environment_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = load_settings().to_loader()
    assert loader("notes_provider") == "gemini"

    monkeypatch.setenv("NOTES_PROVIDER", "openrouter")

    assert loader("notes_provider") == "openrouter"


def test_the_atlas_prefixed_name_wins_over_the_documented_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOTES_PROVIDER", "gemini")
    monkeypatch.setenv("ATLAS_NOTES_PROVIDER", "openrouter")

    assert load_settings().notes_provider == "openrouter"
    assert load_settings().to_loader()("notes_provider") == "openrouter"


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
    assert build_notes_generator(loader).provider == "gemini"


def test_whitespace_only_values_are_also_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_MAX_CONCURRENT_VIDEOS", "   ")
    monkeypatch.setenv("NOTES_PROVIDER", "  ")

    settings = load_settings()

    assert settings.max_concurrent_videos == AtlasSettings().max_concurrent_videos
    assert settings.notes_provider == "gemini"


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
    """The old JSON coercion turned NOTES_MODEL=1.5 into a float."""
    monkeypatch.setenv("NOTES_MODEL", "1.5")
    monkeypatch.setenv("ATLAS_DB_PATH", "123")

    settings = load_settings()

    assert settings.notes_model == "1.5"
    assert settings.database_path == "123"


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
    """The defect: .env.example listed NOTES_PROVIDER etc. but only ATLAS_<FIELD> was honoured."""
    field_names = {f.name for f in fields(AtlasSettings)}
    recognised = (
        {f"ATLAS_{name.upper()}" for name in field_names}
        | set(ENV_ALIASES)
        | {"ATLAS_DB_PATH"}  # legacy alias of ATLAS_DATABASE_PATH
        | {"GEMINI_API_KEY", "OPENROUTER_API_KEY", "YOUTUBE_API_KEY"}  # secrets, read by name
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

    assert NotesPromptSource(load_settings().to_loader()).version() == "notes_v1.1"


def test_the_shipped_prompt_requires_tagged_code_blocks_and_no_title_line() -> None:
    source = NotesPromptSource(load_settings().to_loader())
    template = source.template()

    assert source.path.name == "notes_v1_1.yaml"
    assert "{{Title}}" in template and "{{Transcript}}" in template
    assert "language" in template and "fenced code block" in template.lower()
    assert "title" in template.lower() and "Do not repeat the video title" in template
    for section in ("Notes", "Key Points", "Important Details", "Takeaways"):
        assert section in template
