"""Factory that builds the notes generator (PRD section 18, 21).

Lives in infrastructure (not in the domain port) because choosing an adapter means
importing infrastructure modules. The adapter module is imported inside the function
so that importing this module never pulls in ``litellm`` and never requires an API key
to be present in the environment.

Retry policy: the returned adapter does **not** retry. ``ProcessVideoUseCase`` owns
retries and backoff (persisted, so they survive a restart and are capped by
``max_auto_retries``); a second retry layer here would multiply LLM calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.domain.interfaces.notes_generator import NotesGeneratorPort
from src.domain.interfaces.usage_ledger import UsageLedgerPort
from src.infrastructure.llm.base import SettingsLoader

if TYPE_CHECKING:
    from src.infrastructure.llm.registry.service import ModelRegistry


def build_notes_generator(
    settings_loader: SettingsLoader,
    *,
    registry: ModelRegistry,
    usage_ledger: UsageLedgerPort | None = None,
) -> NotesGeneratorPort:
    """Build the notes generator on the model registry.

    The models, their fallback order and the API keys all come from the registry (database), so
    this never fails for configuration reasons: an empty chain or missing keys surface when a
    note is generated. Every provider goes through the same LiteLLM adapter.
    """
    from src.infrastructure.llm.litellm_notes import LiteLLMNotesGenerator

    return LiteLLMNotesGenerator(settings_loader, usage_ledger=usage_ledger, registry=registry)


__all__ = ["build_notes_generator"]
