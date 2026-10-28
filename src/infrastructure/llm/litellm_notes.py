"""Notes generator on top of LiteLLM: one adapter for every provider.

LiteLLM gives every provider (Gemini, OpenRouter, OpenAI, FreeLLMAPI, ...) the same
chat-completions call and the same OpenAI-style exceptions, so this module replaces the
per-provider adapters. Which models are used, in which fallback order, and with which keys is
data in the model registry (:mod:`src.infrastructure.llm.registry`), managed on the Models and
Keys pages, not code.

Per call: the registry's fallback chain is walked in order. For each model the usable keys of its
provider are rotated and, when the model is turned away entirely, the next model takes over.
Every attempt is recorded (rate-limit ledger, cooldowns, key health). The adapter makes no retry
of its own beyond that: ``ProcessVideoUseCase`` owns retry and backoff.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.domain.interfaces.notes_generator import CUSTOM_PROMPT_VERSION, GeneratedNotes
from src.domain.interfaces.usage_ledger import UsageRecord
from src.domain.models.playlist import FailureType, NotesGenerationError
from src.infrastructure.llm.base import SettingsLoader
from src.infrastructure.llm.models import ModelSpec
from src.infrastructure.llm.notes_base import (
    REGISTRY_KEY_PREFIX,
    NotesGeneratorConfigError,
    NotesPromptSource,
    _key_problem_of,
    check_finish_reason,
    check_input_size,
    classify_provider_exception,
    error_category_of,
    estimate_tokens,
    render_prompt,
    request_timeout_seconds,
    validate_notes,
)
from src.infrastructure.llm.pricing import call_cost, response_cost

if TYPE_CHECKING:
    from src.domain.interfaces.usage_ledger import UsageLedgerPort
    from src.infrastructure.llm.registry.repo import ModelRow
    from src.infrastructure.llm.registry.service import Candidate, ModelRegistry

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
        registry: ModelRegistry,
        usage_ledger: UsageLedgerPort | None = None,
        completion: CompletionFn | None = None,
    ) -> None:
        self._settings = settings_loader
        self._usage_ledger = usage_ledger
        self._completion = completion
        self._registry = registry
        self._prompt = NotesPromptSource(settings_loader)

    # --- configuration ---------------------------------------------------

    @property
    def specs(self) -> tuple[ModelSpec, ...]:
        """The models in try order, read live from the registry."""
        return tuple(_spec_from_row(c.model) for c in self._registry.candidates())

    @property
    def provider(self) -> str:
        """Provider of the primary model (the one tried first); empty with no models."""
        specs = self.specs
        return specs[0].provider if specs else ""

    @property
    def model(self) -> str:
        """The primary model's name; empty with no models."""
        specs = self.specs
        return specs[0].model if specs else ""

    @property
    def prompt_version(self) -> str:
        return self._prompt.version()

    # --- generation ------------------------------------------------------

    async def generate_notes(
        self, *, title: str, transcript: str, prompt_template: str | None = None
    ) -> GeneratedNotes:
        """Render the prompt, call the first model that accepts it, and validate the response.

        ``prompt_template`` replaces the prompt file for this call only (one-off regenerate);
        the summary is then tagged ``custom`` instead of the file's version.
        """
        custom = bool(prompt_template and prompt_template.strip())
        template = prompt_template if custom and prompt_template else self._prompt.template()
        prompt_text = render_prompt(template, title=title, transcript=transcript)

        return await self._generate_from_registry(
            transcript=transcript, prompt_text=prompt_text, custom=custom
        )

    async def _finish(
        self,
        spec: ModelSpec,
        text: str,
        input_tokens: int,
        output_tokens: int,
        finish: object | None,
        cost: float | None,
        *,
        custom: bool,
    ) -> GeneratedNotes:
        """Validate one model's answer, write the usage row and build the result."""
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
            prompt_version=CUSTOM_PROMPT_VERSION if custom else self.prompt_version,
        )

    async def _generate_from_registry(
        self, *, transcript: str, prompt_text: str, custom: bool
    ) -> GeneratedNotes:
        """Walk the registry's fallback chain; the registry decides which keys are usable."""
        registry = self._registry
        candidates = registry.candidates(estimated_tokens=estimate_tokens(prompt_text))
        if not candidates:
            raise NotesGeneratorConfigError(
                "No notes models are enabled. Add one on the Models page."
            )

        errors: list[Exception] = []
        for position, candidate in enumerate(candidates):
            spec = _spec_from_row(candidate.model)
            try:
                check_input_size(transcript, candidate.model.input_token_limit)
                text, input_tokens, output_tokens, finish, cost = await self._call_candidate(
                    candidate, prompt_text
                )
            except (NotesGenerationError, NotesGeneratorConfigError) as exc:
                errors.append(exc)
                if position + 1 < len(candidates):
                    logger.warning(
                        "%s/%s failed (%s); falling back to %s",
                        spec.provider,
                        spec.model,
                        getattr(exc, "diagnostic", None) or exc,
                        candidates[position + 1].model.litellm_model,
                    )
                continue

            return await self._finish(
                spec, text, input_tokens, output_tokens, finish, cost, custom=custom
            )

        raise _most_useful(errors)

    async def _call_candidate(
        self, candidate: Candidate, prompt_text: str
    ) -> tuple[str, int, int, object | None, float | None]:
        """One registry model, rotating its usable keys; every attempt is recorded."""
        registry = self._registry
        model = candidate.model
        base_spec = _spec_from_row(model)
        if not candidate.keys:
            raise _blocked_error(base_spec, candidate.blocked)

        last: Exception | None = None
        for key in candidate.keys:
            spec = dataclasses.replace(
                base_spec, api_key_env=f"{REGISTRY_KEY_PREFIX}{key.label}"
            )
            registry.begin_attempt(key, model)
            started = time.monotonic()
            try:
                response = await self._request(spec, key.key_value, prompt_text)
            except (NotesGenerationError, NotesGeneratorConfigError) as exc:
                problem = _key_problem_of(exc)
                registry.record_failure(key, model, problem)
                registry.log_attempt(
                    key,
                    model,
                    operation=NOTES_OPERATION,
                    success=False,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    error_category=error_category_of(exc),
                )
                if problem is None:
                    raise
                logger.warning(
                    "%s key '%s' turned away (%s); trying the next key",
                    spec.label,
                    key.label,
                    problem,
                )
                last = exc
                continue

            text, input_tokens, output_tokens, finish, cost = _parse_response(response)
            registry.record_success(key, model, input_tokens + output_tokens)
            registry.log_attempt(
                key,
                model,
                operation=NOTES_OPERATION,
                success=True,
                latency_ms=int((time.monotonic() - started) * 1000),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost or call_cost(spec.litellm_model, input_tokens, output_tokens),
            )
            return text, input_tokens, output_tokens, finish, cost

        assert last is not None  # candidate.keys is non-empty
        raise last

    async def _request(self, spec: ModelSpec, key: str, prompt_text: str) -> Any:
        """One LiteLLM call; any failure is raised already classified."""
        completion = self._completion or _litellm_completion()
        params: dict[str, Any] = {
            "model": spec.litellm_model,
            "messages": [{"role": "user", "content": prompt_text}],
            "api_key": key,
            "timeout": spec.request_timeout_seconds or request_timeout_seconds(self._settings),
            # Atlas owns the retry policy; a second, invisible retry loop would double
            # every wait and hide real failure counts.
            "num_retries": 0,
        }
        if spec.api_base:
            params["api_base"] = spec.api_base
        try:
            return await completion(**params)
        except Exception as exc:
            raise self._classify(spec, exc, key) from exc

    def _classify(self, spec: ModelSpec, exc: Exception, key: str) -> Exception:
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
            secrets=(key,),
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
                else call_cost(spec.litellm_model, input_tokens, output_tokens)
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


def _spec_from_row(model: ModelRow) -> ModelSpec:
    """The :class:`ModelSpec` for a registry model (the key is attached per attempt)."""
    litellm_model = model.litellm_model
    if "/" not in litellm_model:
        litellm_model = f"{model.provider}/{litellm_model}"
    return ModelSpec(
        litellm_model=litellm_model,
        api_key_env=REGISTRY_KEY_PREFIX,
        api_base=model.api_base,
        input_token_limit=model.input_token_limit,
        provider_name=model.provider,
        request_timeout_seconds=model.request_timeout_seconds,
    )


def _blocked_error(spec: ModelSpec, blocked: str | None) -> Exception:
    """Why a registry model with no usable key could not be called."""
    if blocked in {"cooling_down", "rate_limited", "monthly_cap"}:
        reasons = {
            "cooling_down": "cooling down after errors",
            "rate_limited": "at its rate limit",
            "monthly_cap": "at its monthly cap",
        }
        return NotesGenerationError(
            FailureType.RATE_LIMITED,
            f"Every {spec.label} API key for {spec.model} is {reasons[blocked]} right now.",
        )
    return NotesGeneratorConfigError(
        f"No usable {spec.label} API key for {spec.model}. Add or enable one on the Keys page."
    )


def _parse_response(response: Any) -> tuple[str, int, int, object | None, float | None]:
    """``(text, in_tokens, out_tokens, finish_reason, cost)`` from a LiteLLM response."""
    choices = getattr(response, "choices", None) or []
    content = ""
    finish = None
    if choices:
        content = getattr(choices[0].message, "content", None) or ""
        finish = getattr(choices[0], "finish_reason", None)

    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    return content, input_tokens, output_tokens, finish, response_cost(response)


def _most_useful(errors: list[Exception]) -> Exception:
    """The error to raise once every model has been turned away.

    A transient failure wins over a permanent one, so the pipeline still retries when a
    fallback was merely rate-limited while another model had a bad key. With a single model
    this is exactly that model's error.
    """
    if not errors:  # pragma: no cover - the candidate list is never empty here
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
