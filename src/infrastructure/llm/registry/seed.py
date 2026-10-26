"""First-run import of the pre-registry configuration into the model registry.

Before the registry, the notes model was chosen with the UI-saved settings ``notes_provider`` and
``<provider>_model`` (rows in ``app_settings``) and the keys came from ``*_API_KEY`` variables.
This runs once, on the first start after migration 0007: it turns that choice into the first
(enabled) model of the chain and imports the keys. Afterwards the registry is the only source;
the old settings are ignored.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from typing import Any

from src.infrastructure.llm.models import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_NVIDIA_MODEL,
    DEFAULT_OPENROUTER_MODEL,
)
from src.infrastructure.llm.registry.providers import PROVIDERS, litellm_model_for
from src.infrastructure.llm.registry.service import ModelRegistry
from src.shared.api_keys import read_api_keys

logger = logging.getLogger(__name__)

_SEEDED = "seeded"
_lock = threading.Lock()

#: Provider the old ``notes_provider`` setting defaulted to.
_DEFAULT_PROVIDER = "gemini"
#: A provider's default model, when the old settings named none.
_DEFAULT_MODELS = {
    "gemini": DEFAULT_GEMINI_MODEL,
    "openrouter": DEFAULT_OPENROUTER_MODEL,
    "nvidia": DEFAULT_NVIDIA_MODEL,
}


def _text(saved: dict[str, Any], key: str) -> str:
    return str(saved.get(key) or "").strip()


def _number(saved: dict[str, Any], key: str, default: float) -> float:
    value = saved.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return default
    return float(value)


def _model_fields(provider: str, saved: dict[str, Any], model_id: str) -> dict[str, Any]:
    """Registry columns for ``model_id`` on ``provider``, with the old per-provider overrides."""
    info = PROVIDERS[provider]
    return {
        "provider": provider,
        "model_id": model_id,
        "display_name": model_id,
        "litellm_model": litellm_model_for(provider, model_id),
        "api_base": _text(saved, f"{provider}_base_url") or info.default_api_base,
        "input_token_limit": int(
            _number(saved, f"{provider}_input_token_limit", info.default_input_token_limit)
        ),
        "request_timeout_seconds": _number(
            saved, f"{provider}_request_timeout_seconds", info.default_timeout_seconds or 0.0
        )
        or None,
        "source": "seed",
    }


def _configured_model(provider: str, saved: dict[str, Any], *, primary: bool) -> str:
    """The model the old settings named for ``provider`` (empty when there is none)."""
    explicit = _text(saved, "notes_model") if primary else ""
    return explicit or _text(saved, f"{provider}_model") or _DEFAULT_MODELS.get(provider, "")


def seed_registry(registry: ModelRegistry) -> None:
    """Fill an empty registry from the old saved settings and ``*_API_KEY`` variables, once.

    Safe to call on every start: the ``seeded`` marker makes later calls no-ops, and rows are
    deduplicated, so a run interrupted halfway simply finishes on the next start. Saved settings
    for options that no longer exist are read here and nowhere else.
    """
    repo = registry.repo
    with _lock:
        if repo.get_meta(_SEEDED):
            return
        saved = repo.saved_settings()
        _seed_models(registry, saved)
        _seed_keys(registry)
        repo.set_meta(_SEEDED, "1")
    logger.info("Seeded the model registry from the existing settings")


def _add_model(registry: ModelRegistry, **fields: Any) -> None:
    try:
        registry.repo.add_model(**fields)
    except sqlite3.IntegrityError:
        pass  # already there (a previous, interrupted seed)


def _seed_models(registry: ModelRegistry, saved: dict[str, Any]) -> None:
    primary = _text(saved, "notes_provider").lower() or _DEFAULT_PROVIDER
    if primary not in PROVIDERS:
        primary = _DEFAULT_PROVIDER

    # The model the user had selected goes first and stays enabled.
    model_id = _configured_model(primary, saved, primary=True)
    if model_id:
        _add_model(registry, enabled=True, **_model_fields(primary, saved, model_id))

    # Every other provider the user has a key for (or had configured) is added disabled, so it
    # is one click away on the Models page.
    for provider in PROVIDERS:
        if provider == primary:
            continue
        model_id = _configured_model(provider, saved, primary=False)
        if not model_id:
            continue
        has_key = bool(read_api_keys(PROVIDERS[provider].env_key))
        if not has_key and not _text(saved, f"{provider}_model"):
            continue
        _add_model(registry, enabled=False, **_model_fields(provider, saved, model_id))


def _seed_keys(registry: ModelRegistry) -> None:
    repo = registry.repo
    known = {k.key_value for k in repo.list_keys()}
    for provider, info in PROVIDERS.items():
        for index, value in enumerate(read_api_keys(info.env_key), start=1):
            if value in known:
                continue
            known.add(value)
            repo.add_key(
                provider=provider, label=f"Imported from {info.env_key} #{index}", key_value=value
            )


__all__ = ["seed_registry"]
