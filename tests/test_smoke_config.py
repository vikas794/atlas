"""Smoke tests for the settings/config layer.

These are the regression tests for the three stacked config bugs:
  1. load_settings() looked for <repo_root>/config.yaml, which does not exist.
  2. src/configs/config.yaml was nested, so from_dict() discarded all of it.
  3. The dotted SettingsLoader silently returned `default` for flat keys.

No backend/transport/infrastructure modules are imported here beyond the SQLite
helper used by conftest, so these stay green while the rest of the repo is
mid-refactor.
"""

from __future__ import annotations

import logging
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.config.settings import (
    DEFAULT_CONFIG_RELPATH,
    AtlasSettings,
    load_settings,
    repo_root,
)

SENTINEL = "<default-was-used>"


@pytest.fixture
def settings(clean_atlas_env: None) -> AtlasSettings:
    """Settings loaded from the real repo config, with ATLAS_* overrides cleared."""
    return load_settings()


# --------------------------------------------------------------------------
# load_settings()
# --------------------------------------------------------------------------


def test_load_settings_returns_atlas_settings(settings: AtlasSettings) -> None:
    assert isinstance(settings, AtlasSettings)


def test_default_config_path_exists(settings: AtlasSettings) -> None:
    """Bug 1: the default lookup pointed at a file that has never existed."""
    config_path = repo_root() / DEFAULT_CONFIG_RELPATH
    assert config_path.is_file(), f"expected config at {config_path}"
    assert DEFAULT_CONFIG_RELPATH == Path("src") / "configs" / "config.yaml"


def test_config_yaml_is_loaded_not_ignored(settings: AtlasSettings) -> None:
    """Bug 2: a nested YAML file matches no field name and is dropped whole."""
    raw = yaml.safe_load(
        (repo_root() / DEFAULT_CONFIG_RELPATH).read_text(encoding="utf-8")
    )
    assert isinstance(raw, dict) and raw, "config.yaml parsed to nothing"

    # Every top-level key must be a real field name.
    valid = {f.name for f in fields(AtlasSettings)}
    unknown = sorted(set(raw) - valid)
    assert not unknown, f"config.yaml has non-field keys: {unknown}"

    # A non-trivial share of the file must survive the filter, and the values
    # the loader reports must be the ones in the file.
    assert len(set(raw) & valid) == len(raw)
    assert settings == AtlasSettings.from_dict(raw)


# --------------------------------------------------------------------------
# SettingsLoader key resolution
# --------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["database_path", "max_concurrent_videos"])
def test_loader_resolves_flat_keys(settings: AtlasSettings, key: str) -> None:
    """Bug 3: every key went through a dot-walk and fell back to `default`."""
    loader = settings.to_loader()
    resolved = loader(key, SENTINEL)
    assert resolved != SENTINEL, f"loader fell back to the default for {key!r}"
    assert resolved == getattr(settings, key)


def test_loader_prefers_env_override(
    settings: AtlasSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATLAS_MAX_CONCURRENT_VIDEOS", "7")
    assert settings.to_loader()("max_concurrent_videos") == 7


def test_loader_returns_default_and_logs_for_unknown_key(
    settings: AtlasSettings, caplog: pytest.LogCaptureFixture
) -> None:
    loader = settings.to_loader()
    with caplog.at_level(logging.DEBUG, logger="src.config.settings"):
        assert loader("no_such_setting", SENTINEL) == SENTINEL
    assert "no_such_setting" in caplog.text


def test_loader_reads_settings_from_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_atlas_env: None
) -> None:
    """A stray CWD must not change where the config and .env are read from."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "database_path: from/yaml.sqlite3\nmax_concurrent_videos: 5\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    loaded: Any = load_settings(config_path)
    assert loaded.database_path == "from/yaml.sqlite3"
    assert loaded.max_concurrent_videos == 5
    assert loaded.to_loader()("max_concurrent_videos") == 5


# --------------------------------------------------------------------------
# Dataclass / loader map hygiene
# --------------------------------------------------------------------------


def test_every_field_is_reachable_through_the_loader(settings: AtlasSettings) -> None:
    loader = settings.to_loader()
    for f in fields(AtlasSettings):
        assert loader(f.name, SENTINEL) != SENTINEL, f"{f.name} not reachable via loader"


def test_removed_legacy_fields_are_gone() -> None:
    removed = (
        "openai_model",
        "openai_timeout",
        "openai_base_url",
        "openai_max_retries",
        "youtube_max_results",
        "youtube_order",
        "youtube_type",
        "search_default_max_results",
        "search_description_max_length",
        "summarizer_prompt_version",
        "summarizer_default_model",
        "summarizer_prompt_name",
        "insights_prompt_version",
        "insights_prompt_name",
        "assignment_prompt_version",
        "assignment_prompt_name",
        "quiz_prompt_name",
        "playlist_quiz_max_videos",
        "playlist_quiz_max_retries",
        "playlist_quiz_delay_between_requests",
        "playlist_quiz_output_folder",
        "transcript_retry_wait_seconds",
        "transcript_retry_jitter_seconds",
        "transcript_min_delay_between_videos",
        "transcript_rate_limit_cooldown_seconds",
        "transcript_delay_between_requests",
        "workers_auto_detect",
        "workers_cpu_ratio",
        "workers_min_workers",
        "workers_max_workers",
        "cache_ttl_days_override",
    )
    present = {f.name for f in fields(AtlasSettings)}
    assert not sorted(set(removed) & present)


def test_new_pipeline_fields_have_expected_defaults() -> None:
    expected = {
        "notes_provider": "gemini",
        "notes_model": "",
        "gemini_model": "gemini-2.5-flash",
        "gemini_input_token_limit": 1000000,
        "notes_prompt_name": "notes_v1_1.yaml",
        "max_concurrent_videos": 2,
        "max_auto_retries": 4,
        "backoff_base_seconds": 5.0,
        "backoff_cap_seconds": 300.0,
    }
    defaults_by_name = {f.name: f.default for f in fields(AtlasSettings)}
    for name, value in expected.items():
        assert defaults_by_name.get(name, "<missing>") == value, name


def test_config_yaml_matches_checked_in_defaults() -> None:
    """The committed YAML and the dataclass defaults must not drift apart."""
    raw = yaml.safe_load((repo_root() / DEFAULT_CONFIG_RELPATH).read_text(encoding="utf-8"))
    dataclass_defaults = {f.name: f.default for f in fields(AtlasSettings)}
    for key, value in raw.items():
        assert dataclass_defaults[key] == value, f"{key} differs between YAML and the dataclass"
