"""Notes generator on top of LiteLLM: one adapter for every provider.

LiteLLM gives every provider (Gemini, OpenRouter, OpenAI, OmniRoute, ...) the same
chat-completions call and the same OpenAI-style exceptions, so this module replaces the
per-provider adapters. Which models are used, and in which fallback order, is configuration
(:mod:`src.infrastructure.llm.models`), not code.

Per call: the models are tried in order. For each one the keys of its provider are rotated
(:func:`call_with_keys`, unchanged) and, when the model is turned away entirely, the next model
takes over. The adapter still makes no retry of its own beyond that: ``ProcessVideoUseCase``
owns retry and backoff.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.domain.interfaces.notes_generator import GeneratedNotes
from src.domain.interfaces.usage_ledger import UsageRecord
from src.domain.models.playlist import FailureType, NotesGenerationError
from src.infrastructure.llm.base import SettingsLoader
from src.infrastructure.llm.cost import CostCalculator
from src.infrastructure.llm.models import ModelSpec, resolve_model_specs
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
from src.shared.api_keys import pool_for, read_api_keys

if TYPE_CHECKING:
    from src.domain.interfaces.usage_ledger import UsageLedgerPort

logger = logging.getLogger(__name__)

#: Recorded on every usage row so the ledger can group notes cost separately from transcripts.
NOTES_OPERATION = "generate_notes"

#: ``litellm.acompletion``-shaped callable. Tests inject a double here, so no test ever reaches
#: a provider or reads a real key.
CompletionFn = Callable[..., Awaitable[Any]]


def _litellm_completion() -> CompletionFn:
    """``litellm.acompletion``, imported lazily and configured not to touch the network.

    LiteLLM would otherwise download its model-price table from GitHub on import, send
    telemetry-ish callbacks and print "Give Feedback" banners. Imported on first use so
    ``import src.infrastructure.llm`` never needs the package or a key.
    """
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm

    litellm.suppress_debug_info = True
    litellm.drop_params = True  # a model without e.g. a param we send must not fail the call
    return litellm.acompletion


class LiteLLMNotesGenerator:
    """Generate notes through LiteLLM, falling back across the configured models."""

    def __init__(
        self,
        settings_loader: SettingsLoader,
        *,
        usage_ledger: UsageLedgerPort | None = None,
        completion: CompletionFn | None = None,
        specs: tuple[ModelSpec, ...] | None = None,
    ) -> None:
        self._settings = settings_loader
        self._usage_ledger = usage_ledger
        self._completion = completion
        self._specs = specs if specs is not None else resolve_model_specs(settings_loader)
        self._prompt = NotesPromptSource(settings_loader)

    # --- configuration ---------------------------------------------------

    @property
    def specs(self) -> tuple[ModelSpec, ...]:
        return self._specs

    @property
    def provider(self) -> str:
        """Provider of the primary model (the one tried first)."""
        return self._specs[0].provider

    @property
    def model(self) -> str:
        """The primary model's name."""
        return self._specs[0].model

    @property
    def prompt_version(self) -> str:
        return self._prompt.version()

    # --- generation ------------------------------------------------------

    async def generate_notes(self, *, title: str, transcript: str) -> GeneratedNotes:
        """Render the prompt, call the first model that accepts it, and validate the response."""
        prompt_text = render_prompt(self._prompt.template(), title=title, transcript=transcript)

        errors: list[Exception] = []
        for position, spec in enumerate(self._specs):
            try:
                check_input_size(transcript, spec.input_token_limit)
                text, input_tokens, output_tokens, finish, cost = await self._call_model(
                    spec, prompt_text
                )
            except (NotesGenerationError, NotesGeneratorConfigError) as exc:
                errors.append(exc)
                if position + 1 < len(self._specs):
                    logger.warning(
                        "%s/%s failed (%s); falling back to %s",
                        spec.provider,
                        spec.model,
                        getattr(exc, "diagnostic", None) or exc,
                        self._specs[position + 1].litellm_model,
                    )
                continue

            check_finish_reason(finish, provider_label=spec.label)
            content = validate_notes(text)
            await self._emit_usage(
                spec,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
            )
            logger.info("Generated %d characters of notes with %s", len(content), spec.litellm_model)
            return GeneratedNotes(
                content=content,
                provider=spec.provider,
                model=spec.model,
                prompt_version=self.prompt_version,
            )

        raise _most_useful(errors)

    async def _call_model(
        self, spec: ModelSpec, prompt_text: str
    ) -> tuple[str, int, int, object | None, float | None]:
        """One model, key rotation included: ``(text, in_tokens, out_tokens, finish, cost)``."""
        if not read_api_keys(spec.api_key_env):
            raise NotesGeneratorConfigError(
                f"{spec.api_key_env} is not set. Add your {spec.label} API key to .env as "
                f"{spec.api_key_env}=<key> to use {spec.litellm_model}. Several keys can be "
                "given, separated by commas, to spread rate limits."
            )

        response = await call_with_keys(
            pool_for(spec.api_key_env),
            lambda key: self._request(spec, key, prompt_text),
            provider_label=spec.label,
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
        return content, input_tokens, output_tokens, finish, _response_cost(response)

    async def _request(self, spec: ModelSpec, key: str, prompt_text: str) -> Any:
        """One LiteLLM call; any failure is raised already classified."""
        completion = self._completion or _litellm_completion()
        params: dict[str, Any] = {
            "model": spec.litellm_model,
            "messages": [{"role": "user", "content": prompt_text}],
            "api_key": key,
            "timeout": request_timeout_seconds(self._settings),
            # Atlas owns the retry policy; a second, invisible retry loop would double
            # every wait and hide real failure counts.
            "num_retries": 0,
        }
        if spec.api_base:
            params["api_base"] = spec.api_base
        try:
            return await completion(**params)
        except Exception as exc:
            raise self._classify(spec, exc) from exc

    def _classify(self, spec: ModelSpec, exc: Exception) -> Exception:
        """Map a LiteLLM exception onto the PRD section 8 taxonomy.

        A rejected key, unknown model or missing credits becomes a permanent
        ``NotesGeneratorConfigError``; 429/5xx/network trouble stay transient. The
        user-facing reason never contains raw provider text, and the redacted
        diagnostic is logged here because a config error carries none.
        """
        error = classify_provider_exception(
            exc,
            provider_label=spec.label,
            api_key_env=spec.api_key_env,
            model=spec.model,
            secrets=read_api_keys(spec.api_key_env),
        )
        logger.warning(
            "%s call failed (%s): %s",
            spec.label,
            type(exc).__name__,
            getattr(error, "diagnostic", None) or error,
        )
        return error

    async def _emit_usage(
        self,
        spec: ModelSpec,
        *,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float | None,
    ) -> None:
        """Write one usage row; a ledger failure must never fail the call."""
        if self._usage_ledger is None:
            return
        try:
            cost = (
                cost_usd
                if cost_usd
                else CostCalculator.calculate_cost(
                    spec.provider, spec.model, input_tokens, output_tokens
                )
            )
            await self._usage_ledger.record_usage(
                UsageRecord(
                    timestamp=datetime.now(UTC),
                    provider=spec.provider,
                    operation=NOTES_OPERATION,
                    model=spec.model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                    cost_usd=cost,
                    cache_hit=False,
                )
            )
        except Exception:
            logger.warning(
                "Usage ledger write failed for %s/%s", spec.provider, spec.model, exc_info=False
            )


def _response_cost(response: Any) -> float | None:
    """LiteLLM's own price for this call, when it knows one (``None`` otherwise)."""
    hidden = getattr(response, "_hidden_params", None)
    if isinstance(hidden, dict):
        value = hidden.get("response_cost")
        if isinstance(value, int | float) and value > 0:
            return float(value)
    return None


def _most_useful(errors: list[Exception]) -> Exception:
    """The error to raise once every model has been turned away.

    A transient failure wins over a permanent one, so the pipeline still retries when a
    fallback was merely rate-limited while another model had a bad key. With a single model
    this is exactly that model's error.
    """
    if not errors:  # pragma: no cover - the spec list is never empty
        return NotesGeneratorConfigError("No notes model is configured.")
    transient = [
        e
        for e in errors
        if isinstance(e, NotesGenerationError)
        and e.failure_type is not FailureType.TRANSCRIPT_TOO_LARGE
    ]
    if transient:
        return transient[-1]
    return errors[0]


__all__ = ["NOTES_OPERATION", "CompletionFn", "LiteLLMNotesGenerator"]
