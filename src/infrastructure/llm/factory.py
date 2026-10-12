"""Factory that builds the configured notes generator (PRD section 18, 21).

Lives in infrastructure (not in the domain port) because choosing an adapter means
importing infrastructure modules. Each adapter module is imported inside the function
so that importing this module never pulls in ``google.genai`` / ``openai`` and never
requires an API key to be present in the environment.

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
    """Build the notes generator named by ``notes_provider`` (``NOTES_PROVIDER``).

    ``notes_provider`` is ``"gemini"`` (the default) or ``"openrouter"``.

    Raises:
        ValueError: If ``notes_provider`` names something Atlas cannot serve.
    """
    raw_provider = settings_loader("notes_provider", "gemini")
    provider = str(raw_provider or "gemini").strip().lower()

    if provider == "gemini":
        from src.infrastructure.llm.gemini.notes import GeminiNotesGenerator

        return GeminiNotesGenerator(settings_loader, usage_ledger=usage_ledger)
    if provider == "openrouter":
        from src.infrastructure.llm.openai.notes import OpenRouterNotesGenerator

        return OpenRouterNotesGenerator(settings_loader, usage_ledger=usage_ledger)

    raise ValueError(
        f"Unknown notes_provider {provider!r} (from settings 'notes_provider'); "
        "supported providers are: gemini, openrouter."
    )


__all__ = ["build_notes_generator"]
