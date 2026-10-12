"""Gemini notes generator (Google Gen AI SDK).

One model call per video: the rendered prompt goes out, the Markdown comes back,
and a usage record is emitted either way. The SDK import is deliberately inside
:meth:`GeminiNotesGenerator._client` so that ``import src.infrastructure.llm``
works with neither the SDK installed nor a key configured.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.domain.interfaces.notes_generator import GeneratedNotes
from src.domain.interfaces.usage_ledger import UsageRecord
from src.infrastructure.llm.base import SettingsLoader
from src.infrastructure.llm.cost import CostCalculator
from src.infrastructure.llm.notes_base import (
    NotesGeneratorConfigError,
    NotesPromptSource,
    check_finish_reason,
    check_input_size,
    classify_provider_exception,
    render_prompt,
    validate_notes,
)

if TYPE_CHECKING:
    from src.domain.interfaces.usage_ledger import UsageLedgerPort

logger = logging.getLogger(__name__)

#: Recorded on every usage row so the ledger can group notes cost separately
#: from transcript fetches.
NOTES_OPERATION = "generate_notes"

#: Fallback when ``gemini_input_token_limit`` is not set. Gemini 2.5 models
#: accept 1M tokens; the configured default in AtlasSettings is the same.
_FALLBACK_INPUT_TOKEN_LIMIT = 1_000_000

#: Per-request timeout. The SDK default is *no* timeout, so one hung connection would
#: hold a worker slot (and its video in ``generating_notes``) forever. Long transcripts
#: legitimately take minutes; the OpenRouter adapter uses the same bound.
_REQUEST_TIMEOUT_SECONDS = 300.0


class GeminiNotesGenerator:
    """Generate notes with ``google.genai``.

    Args:
        settings_loader: Config accessor (``AtlasSettings.to_loader()``).
        usage_ledger: Optional sink for usage rows. A ledger failure is logged
            and swallowed: metering must never fail a generation.
        client_factory: Builds the SDK client. Tests inject a double here, so no
            test in this repo ever constructs a real client or reads a real key.
    """

    #: Provider identity stored on ``Summary.provider`` and the usage ledger.
    provider = "gemini"

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
        self._client: Any | None = None
        self._prompt = NotesPromptSource(settings_loader)

    # --- configuration ---------------------------------------------------

    @property
    def model(self) -> str:
        """``notes_model`` if set, otherwise the ``gemini_model`` default."""
        explicit = str(self._settings("notes_model", "") or "").strip()
        return explicit or str(self._settings("gemini_model", "gemini-2.5-flash"))

    @property
    def input_token_limit(self) -> int:
        return int(
            self._settings("gemini_input_token_limit", _FALLBACK_INPUT_TOKEN_LIMIT)
            or _FALLBACK_INPUT_TOKEN_LIMIT
        )

    @property
    def prompt_version(self) -> str:
        return self._prompt.version()

    def _api_key_env(self) -> str:
        """Name of the environment variable holding the key. Safe to log."""
        return str(self._settings("gemini_api_key_env", "GEMINI_API_KEY"))

    def _api_key(self) -> str:
        """The key value, or ``""`` when unset. Never log or store the result."""
        return str(os.environ.get(self._api_key_env(), "") or "").strip()

    def _require_api_key(self) -> str:
        """Same as :meth:`_api_key` but refuses to return an empty value.

        The message names the variable, never its contents: an operator has to
        be able to see what is missing from a log line.
        """
        key = self._api_key()
        if not key:
            raise NotesGeneratorConfigError(
                f"{self._api_key_env()} is not set. Add your Google AI Studio (Gemini) API key "
                f"to .env as {self._api_key_env()}=<key> to generate notes."
            )
        return key

    def _client_or_default(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        if self._client is None:
            from google import genai  # imported lazily: keeps module import cheap
            from google.genai import types

            self._client = genai.Client(
                api_key=self._require_api_key(),
                # HttpOptions.timeout is in milliseconds. No retry_options: the SDK then
                # makes exactly one attempt, and ProcessVideoUseCase owns retries.
                http_options=types.HttpOptions(timeout=int(_REQUEST_TIMEOUT_SECONDS * 1000)),
            )
        return self._client

    # --- generation ------------------------------------------------------

    async def generate_notes(self, *, title: str, transcript: str) -> GeneratedNotes:
        """Render the prompt, call Gemini once, and validate the response."""
        prompt_text = render_prompt(self._prompt.template(), title=title, transcript=transcript)
        # Checked after rendering so a missing placeholder is reported as the
        # config error it is, and before the call so an oversized transcript is
        # never sent (PRD section 18: fail with a reason, never truncate).
        check_input_size(transcript, self.input_token_limit)

        model = self.model
        text, input_tokens, output_tokens, finish = await self._call_model(model, prompt_text)
        check_finish_reason(finish, provider_label="Gemini")
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
        """Single provider call; returns ``(text, input_tokens, output_tokens, finish)``.

        ``finish`` is the first candidate's finish reason, or the prompt's block
        reason when Gemini refused the prompt outright (then there is no candidate).

        Token counts are 0 when the SDK does not report them -- an undercount in
        the ledger is better than a guess, and never a reason to fail a call.
        """
        client = self._client_or_default()
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=prompt_text,
            )
        except Exception as exc:
            raise self._classify(exc) from exc

        try:
            text = getattr(response, "text", None) or ""
        except Exception:  # the SDK raises from .text on some blocked/odd responses
            text = ""
        usage = getattr(response, "usage_metadata", None)
        input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        # Thinking tokens are billed as output but reported separately from the answer.
        output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0) + int(
            getattr(usage, "thoughts_token_count", 0) or 0
        )
        candidates = getattr(response, "candidates", None) or []
        finish = getattr(candidates[0], "finish_reason", None) if candidates else None
        if finish is None:
            feedback = getattr(response, "prompt_feedback", None)
            blocked = getattr(feedback, "block_reason", None)
            finish = "SAFETY" if blocked else None
        return text, input_tokens, output_tokens, finish

    def _classify(self, exc: Exception) -> Exception:
        """Map an SDK exception onto the PRD section 8 taxonomy.

        A rejected key, unknown model or missing credits becomes a permanent
        ``NotesGeneratorConfigError``; 429/5xx/network trouble stay transient. The
        user-facing reason never contains raw provider text, and the redacted
        diagnostic is logged here because a config error carries none.
        """
        error = classify_provider_exception(
            exc,
            provider_label="Gemini",
            api_key_env=self._api_key_env(),
            model=self.model,
            secrets=(self._api_key(),),
        )
        logger.warning(
            "Gemini call failed (%s): %s",
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
            # Log the exception *type* only. A ledger backend that echoes its
            # payload (or its own connection string) in a traceback must not be
            # able to put anything sensitive in the log.
            logger.warning(
                "Usage ledger write failed for %s/%s", self.provider, model, exc_info=False
            )


__all__ = ["NOTES_OPERATION", "GeminiNotesGenerator"]
