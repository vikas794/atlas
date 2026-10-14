"""OpenRouter notes generator (OpenAI-compatible chat completions).

OpenRouter fronts many models behind the OpenAI chat-completions shape, so the
adapter is the same code shape as a plain OpenAI call with a different
``base_url``. ``openai`` is imported lazily so that
``import src.infrastructure.llm`` never requires the package or a key.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.domain.interfaces.notes_generator import GeneratedNotes
from src.domain.interfaces.usage_ledger import UsageRecord
from src.infrastructure.llm.base import SettingsLoader
from src.infrastructure.llm.cost import CostCalculator
from src.infrastructure.llm.notes_base import (
    NotesGeneratorConfigError,
    NotesPromptSource,
    call_with_keys,
    check_finish_reason,
    check_input_size,
    classify_provider_exception,
    render_prompt,
    request_timeout_seconds,
    validate_notes,
)

from src.shared.api_keys import KeyPool, pool_for, read_api_keys

if TYPE_CHECKING:
    from src.domain.interfaces.usage_ledger import UsageLedgerPort

logger = logging.getLogger(__name__)

#: Default OpenAI-compatible endpoint; override with ``OPENROUTER_BASE_URL``.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

#: Model used when ``notes_model`` is empty.
#:
#: Why ``openai/gpt-5-mini``: it is the cheapest capable model on OpenRouter at
#: the time of writing, it is a first-party OpenRouter listing so availability is
#: not an upstream-quirk gamble, and -- unlike a default Gemini route -- its
#: price is already in :class:`~src.infrastructure.llm.cost.CostCalculator`'s
#: table, so switching ``notes_provider`` does not silently zero out the cost
#: column in the usage dashboard.
DEFAULT_OPENROUTER_MODEL = "openai/gpt-5-mini"

#: Recorded on every usage row, matching the Gemini adapter.
NOTES_OPERATION = "generate_notes"

#: Conservative pre-flight limit. OpenRouter proxies dozens of models with
#: different context windows, and the cheapest one is a bad guess for the
#: largest; 128k is the smallest window in common use, so this refuses early
#: rather than letting the provider reject a request we already paid for.
#: Override with ``openrouter_input_token_limit`` when you pin a bigger model.
_FALLBACK_INPUT_TOKEN_LIMIT = 128_000



class OpenRouterNotesGenerator:
    """Generate notes with ``openai.AsyncOpenAI`` pointed at OpenRouter."""

    #: Provider identity stored on ``Summary.provider`` and the usage ledger.
    provider = "openrouter"

    def __init__(
        self,
        settings_loader: SettingsLoader,
        *,
        usage_ledger: UsageLedgerPort | None = None,
        client_factory: Any | None = None,
    ) -> None:
        self._settings = settings_loader
        self._usage_ledger = usage_ledger
        self._client_factory = client_factory
        self._clients: dict[str, Any] = {}
        self._prompt = NotesPromptSource(settings_loader)

    # --- configuration ---------------------------------------------------

    @property
    def model(self) -> str:
        """``notes_model`` (NOTES_MODEL), then ``openrouter_model`` (OPENROUTER_MODEL),
        then :data:`DEFAULT_OPENROUTER_MODEL`."""
        explicit = str(self._settings("notes_model", "") or "").strip()
        specific = str(self._settings("openrouter_model", "") or "").strip()
        return explicit or specific or DEFAULT_OPENROUTER_MODEL

    @property
    def base_url(self) -> str:
        """The OpenAI-compatible endpoint (``OPENROUTER_BASE_URL``)."""
        configured = str(self._settings("openrouter_base_url", "") or "").strip()
        return configured or OPENROUTER_BASE_URL

    @property
    def input_token_limit(self) -> int:
        return int(
            self._settings("openrouter_input_token_limit", _FALLBACK_INPUT_TOKEN_LIMIT)
            or _FALLBACK_INPUT_TOKEN_LIMIT
        )

    @property
    def prompt_version(self) -> str:
        return self._prompt.version()

    def _api_key_env(self) -> str:
        """Name of the environment variable holding the key. Safe to log."""
        return str(self._settings("openrouter_api_key_env", "OPENROUTER_API_KEY"))

    def _api_keys(self) -> tuple[str, ...]:
        """Every configured key (``OPENROUTER_API_KEY`` may hold a comma-separated list, plus
        ``OPENROUTER_API_KEY_2``, ``_3`` ...). Never log or store the result."""
        return read_api_keys(self._api_key_env())

    def _api_key(self) -> str:
        """The first key, or ``""`` when unset. Never log or store the result."""
        keys = self._api_keys()
        return keys[0] if keys else ""

    def _pool(self) -> KeyPool:
        return pool_for(self._api_key_env())

    def _require_api_key(self) -> str:
        key = self._api_key()
        if not key:
            raise NotesGeneratorConfigError(
                f"{self._api_key_env()} is not set. Add your OpenRouter API key to .env as "
                f"{self._api_key_env()}=<key> to generate notes. Several keys can be given, "
                "separated by commas, to spread rate limits."
            )
        return key

    def _client_for(self, key: str) -> Any:
        client = self._clients.get(key)
        if client is None:
            from openai import AsyncOpenAI  # imported lazily: keeps module import cheap

            client = self._clients[key] = AsyncOpenAI(
                api_key=key,
                base_url=self.base_url,
                timeout=request_timeout_seconds(self._settings),
                # Atlas owns the retry policy; a second, invisible SDK retry loop
                # would double every wait and hide real failure counts.
                max_retries=0,
            )
        return client

    def _client_or_default(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        return self._client_for(self._require_api_key())

    # --- generation ------------------------------------------------------

    async def generate_notes(self, *, title: str, transcript: str) -> GeneratedNotes:
        """Render the prompt, call OpenRouter once, and validate the response."""
        prompt_text = render_prompt(self._prompt.template(), title=title, transcript=transcript)
        check_input_size(transcript, self.input_token_limit)

        model = self.model
        text, input_tokens, output_tokens, finish = await self._call_model(model, prompt_text)
        check_finish_reason(finish, provider_label="OpenRouter")
        content = validate_notes(text)
        await self._emit_usage(model=model, input_tokens=input_tokens, output_tokens=output_tokens)

        logger.info("Generated %d characters of notes with %s", len(content), model)
        return GeneratedNotes(
            content=content,
            provider=self.provider,
            model=model,
            prompt_version=self.prompt_version,
        )

    async def _call_model(
        self, model: str, prompt_text: str
    ) -> tuple[str, int, int, object | None]:
        """Single provider call; returns ``(text, input_tokens, output_tokens, finish)``."""
        if self._client_factory is not None:
            response = await self._request(self._client_factory(), model, prompt_text)
        else:
            self._require_api_key()
            response = await call_with_keys(
                self._pool(),
                lambda key: self._request(self._client_for(key), model, prompt_text),
                provider_label="OpenRouter",
            )

        choices = getattr(response, "choices", None) or []
        content = ""
        finish = None
        if choices:
            content = getattr(choices[0].message, "content", None) or ""
            finish = getattr(choices[0], "finish_reason", None)

        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        return content, input_tokens, output_tokens, finish

    async def _request(self, client: Any, model: str, prompt_text: str) -> Any:
        """One SDK call; any failure is raised already classified."""
        try:
            return await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt_text}],
            )
        except Exception as exc:
            raise self._classify(exc) from exc

    def _classify(self, exc: Exception) -> Exception:
        """Map an SDK exception onto the PRD section 8 taxonomy.

        A rejected key, unknown model or missing credits becomes a permanent
        ``NotesGeneratorConfigError``; 429/5xx/network trouble stay transient. The
        user-facing reason never contains raw provider text, and the redacted
        diagnostic is logged here because a config error carries none.
        """
        error = classify_provider_exception(
            exc,
            provider_label="OpenRouter",
            api_key_env=self._api_key_env(),
            model=self.model,
            secrets=self._api_keys(),
        )
        logger.warning(
            "OpenRouter call failed (%s): %s",
            type(exc).__name__,
            getattr(error, "diagnostic", None) or error,
        )
        return error

    async def _emit_usage(self, *, model: str, input_tokens: int, output_tokens: int) -> None:
        """Write one usage row; a ledger failure must never fail the call."""
        if self._usage_ledger is None:
            return
        try:
            await self._usage_ledger.record_usage(
                UsageRecord(
                    timestamp=datetime.now(UTC),
                    provider=self.provider,
                    operation=NOTES_OPERATION,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                    cost_usd=CostCalculator.calculate_cost(
                        self.provider, model, input_tokens, output_tokens
                    ),
                    cache_hit=False,
                )
            )
        except Exception:
            logger.warning(
                "Usage ledger write failed for %s/%s", self.provider, model, exc_info=False
            )


__all__ = [
    "DEFAULT_OPENROUTER_MODEL",
    "NOTES_OPERATION",
    "OPENROUTER_BASE_URL",
    "OpenRouterNotesGenerator",
]
