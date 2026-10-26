"""First-run import of the old saved settings and env keys into the registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.infrastructure.llm.registry import ModelRegistry, RegistryRepo, seed_registry
from src.infrastructure.storage.db import connect

_KEY_VARS = (
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
    "FREELLMAPI_API_KEY",
    "NVIDIA_API_KEY",
    "OPENAI_API_KEY",
)


@pytest.fixture(autouse=True)
def _no_ambient_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _KEY_VARS:
        monkeypatch.delenv(name, raising=False)
        for suffix in ("_2", "_3"):
            monkeypatch.delenv(name + suffix, raising=False)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "atlas.sqlite3"


@pytest.fixture
def registry(db_path: Path) -> ModelRegistry:
    return ModelRegistry(RegistryRepo(db_path))


def _save(db_path: Path, **settings: Any) -> None:
    """Rows of the old UI-saved settings (``app_settings``), as a previous version wrote them."""
    conn = connect(db_path)
    try:
        conn.executemany(
            "INSERT INTO app_settings (key, value_json, updated_at) VALUES (?, ?, 'x')",
            [(key, json.dumps(value)) for key, value in settings.items()],
        )
    finally:
        conn.close()


def test_a_fresh_install_gets_the_default_gemini_model(registry: ModelRegistry) -> None:
    seed_registry(registry)

    (model,) = registry.repo.list_models()

    assert (model.provider, model.model_id, model.litellm_model) == (
        "gemini",
        "gemini-3.5-flash",
        "gemini/gemini-3.5-flash",
    )
    assert model.enabled and model.chain_enabled and model.priority == 1 and model.source == "seed"
    assert model.input_token_limit == 1_000_000


def test_the_saved_provider_and_model_become_the_first_enabled_model(
    registry: ModelRegistry, db_path: Path
) -> None:
    """The developer's database: freellmapi/auto selected, a Gemini model configured as well."""
    _save(
        db_path,
        notes_provider="freellmapi",
        freellmapi_model="auto",
        gemini_model="gemini-flash-latest",
    )

    seed_registry(registry)

    models = registry.repo.list_models()
    assert [(m.provider, m.model_id, m.priority, m.enabled) for m in models] == [
        ("freellmapi", "auto", 1, True),
        ("gemini", "gemini-flash-latest", 2, False),
    ]
    freellmapi = models[0]
    assert freellmapi.litellm_model == "openai/auto"  # an OpenAI-compatible gateway
    assert freellmapi.api_base == "http://localhost:3001/v1"


def test_a_saved_notes_model_overrides_the_providers_model(
    registry: ModelRegistry, db_path: Path
) -> None:
    _save(db_path, notes_provider="gemini", gemini_model="gemini-x", notes_model="gemini-y")

    seed_registry(registry)

    (model,) = registry.repo.list_models()
    assert model.model_id == "gemini-y"


def test_saved_base_url_token_limit_and_timeout_are_carried_over(
    registry: ModelRegistry, db_path: Path
) -> None:
    _save(
        db_path,
        notes_provider="openrouter",
        openrouter_model="anthropic/claude-sonnet",
        openrouter_base_url="https://proxy.example/v1",
        openrouter_input_token_limit=64_000,
        nvidia_model="deepseek-ai/other",
        nvidia_request_timeout_seconds=900,
    )

    seed_registry(registry)

    by_provider = {m.provider: m for m in registry.repo.list_models()}
    openrouter = by_provider["openrouter"]
    assert (openrouter.api_base, openrouter.input_token_limit) == (
        "https://proxy.example/v1",
        64_000,
    )
    nvidia = by_provider["nvidia"]  # configured, so it is there, but not selected
    assert not nvidia.enabled and nvidia.request_timeout_seconds == 900


def test_nvidia_defaults_to_the_long_timeout(registry: ModelRegistry, db_path: Path) -> None:
    _save(db_path, notes_provider="nvidia")

    seed_registry(registry)

    nvidia = next(m for m in registry.repo.list_models() if m.provider == "nvidia")
    assert nvidia.enabled and nvidia.request_timeout_seconds == 3600
    assert nvidia.api_base == "https://integrate.api.nvidia.com/v1"


def test_an_unknown_saved_provider_falls_back_to_the_default(
    registry: ModelRegistry, db_path: Path
) -> None:
    _save(db_path, notes_provider="llama.cpp")

    seed_registry(registry)

    (model,) = registry.repo.list_models()
    assert model.provider == "gemini"


def test_env_keys_are_imported_with_multi_key_forms_and_deduplicated(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "aaa,bbb")
    monkeypatch.setenv("GEMINI_API_KEY_2", "bbb")
    monkeypatch.setenv("NVIDIA_API_KEY", "nnn")

    seed_registry(registry)

    keys = registry.repo.list_keys()
    assert sorted((k.provider, k.key_value) for k in keys) == [
        ("gemini", "aaa"),
        ("gemini", "bbb"),
        ("nvidia", "nnn"),
    ]
    assert all(k.label.startswith("Imported from ") for k in keys)


def test_a_provider_with_a_key_but_no_saved_model_gets_a_disabled_default(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "nnn")

    seed_registry(registry)

    by_provider = {m.provider: m for m in registry.repo.list_models()}
    assert by_provider["gemini"].enabled
    nvidia = by_provider["nvidia"]
    assert not nvidia.enabled and nvidia.priority == 2 and nvidia.request_timeout_seconds == 3600
    assert "openrouter" not in by_provider  # no key and nothing saved: no model


def test_freellmapi_without_a_saved_model_seeds_no_model_but_imports_its_key(
    registry: ModelRegistry, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FREELLMAPI_API_KEY", "fff")
    _save(db_path, notes_provider="freellmapi")  # no model was ever selected

    seed_registry(registry)

    assert registry.repo.list_models() == []
    assert [k.provider for k in registry.repo.list_keys()] == ["freellmapi"]


def test_seeding_runs_once_and_survives_user_deletions(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "aaa")
    seed_registry(registry)
    for model in registry.repo.list_models():
        registry.repo.delete_model(model.id)
    for key in registry.repo.list_keys():
        registry.repo.delete_key(key.id)

    seed_registry(registry)

    assert registry.repo.list_models() == [] and registry.repo.list_keys() == []


def test_an_interrupted_seed_is_finished_without_duplicates(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "aaa")
    seed_registry(registry)
    registry.repo.set_meta("seeded", "")  # as if the marker was never written

    seed_registry(registry)

    assert len(registry.repo.list_models()) == 1 and len(registry.repo.list_keys()) == 1


def test_saved_settings_that_are_not_valid_json_are_skipped(
    registry: ModelRegistry, db_path: Path
) -> None:
    conn = connect(db_path)
    conn.execute(
        "INSERT INTO app_settings (key, value_json, updated_at) VALUES ('notes_provider', '{', 'x')"
    )
    conn.close()

    seed_registry(registry)

    (model,) = registry.repo.list_models()
    assert model.provider == "gemini"
