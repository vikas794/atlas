"""Factory that builds the configured notes generator (PRD section 18, 21).

Lives in infrastructure (not in the domain port) because choosing an adapter means
importing infrastructure modules. The adapter module is imported inside the function
so that importing this module never pulls in ``litellm`` and never requires an API key
to be present in the environment.

Retry policy: the returned adapter does **not** retry. ``ProcessVideoUseCase`` owns
retries and backoff (persisted, so they survive a restart and are capped by
``max_auto_retries``); a second retry layer here would multiply LLM calls.
"""

from __future__ import annotations

from src.domain.interfaces.notes_generator import NotesGeneratorPort
from src.domain.interfaces.usage_ledger import UsageLedgerPort
from src.infrastructure.llm.base import SettingsLoader


def build_notes_generator(
    settings_loader: SettingsLoader,
    usage_ledger: UsageLedgerPort | None = None,
) -> NotesGeneratorPort:
    """Build the notes generator for the models listed in ``notes_models``.

    With ``notes_models`` empty the single model named by the legacy ``notes_provider``
    settings is used. Every provider goes through the same LiteLLM adapter.

    Raises:
        ValueError: If a model entry (or ``notes_provider``) names something Atlas cannot serve.
    """
    from src.infrastructure.llm.litellm_notes import LiteLLMNotesGenerator

    return LiteLLMNotesGenerator(settings_loader, usage_ledger=usage_ledger)


__all__ = ["build_notes_generator"]
