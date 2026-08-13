"""Gemini-backed implementation of :class:`QuizGeneratorPort`.

This module wires ``google.genai`` into the Atlas domain layer, translating
``QuizContext`` / ``TranscriptRef`` inputs into Gemini API calls and surfacing
results through the ``QuizGeneratorPort`` protocol.  A bounded retry loop
mirrors the legacy exponential-backoff schedule (``wait_time = 2 ** attempt``)
and every attempt — success or failure — emits a :class:`UsageRecord` to the
optional usage ledger.

**Client strategy:** a new :class:`google.genai.Client` is instantiated per
``generate_quiz`` call.  This is the safest thread-safety posture because the
Gemini Python SDK does not publish a formal thread-safety guarantee; the
construction cost is negligible relative to the API round-trip and prevents
theoretical races in multi-threaded callers (e.g. :class:`ThreadPoolExecutor`).
"""

from __future__ import annotations

import logging
import time as _time
from datetime import UTC, datetime
from typing import Any

from google import genai

from src.domain.interfaces.quiz_generator import QuizContext, QuizGeneratorPort, QuizResult
from src.domain.interfaces.usage_ledger import UsageRecord
from src.domain.models.transcript import TranscriptContent
from src.domain.models.video import TranscriptRef, VideoId
from src.infrastructure.llm.base import (
    LLMProviderError,
    RetryPolicy,
    SettingsLoader,
    TokenUsage,
    UsageLedgerSink,
)
from src.infrastructure.llm.cost import calculate_cost
from src.utils import get_prompt_path

logger = logging.getLogger(__name__)

_PROMPT_KEY = "quiz_generator.yaml"
_DEFAULT_MODEL = "gemini-3.6-flash"
_DEFAULT_MAX_RETRIES = 3
_OPERATION = "quiz_generate"
_PROVIDER = "gemini"


def _one_zero_usage() -> TokenUsage:
    return TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)


def _extract_token_usage(response: Any) -> TokenUsage:
    prompt_tokens = 0
    candidates_tokens = 0
    total = 0

    usage_metadata = getattr(response, "usage_metadata", None)
    if usage_metadata is not None:
        prompt_tokens = int(getattr(usage_metadata, "prompt_token_count", 0) or 0)
        candidates_tokens = int(getattr(usage_metadata, "candidates_token_count", 0) or 0)
        total_raw = int(getattr(usage_metadata, "total_token_count", 0) or 0)
        if total_raw:
            total = total_raw

    if total == 0 and (prompt_tokens or candidates_tokens):
        total = prompt_tokens + candidates_tokens

    return TokenUsage(
        input_tokens=prompt_tokens,
        output_tokens=candidates_tokens,
        total_tokens=total,
    )


def _resolve_model(settings: SettingsLoader | None = None, context: QuizContext | None = None) -> str:
    if context and context.model:
        return context.model
    if settings is not None:
        resolved = settings("api.gemini.model", _DEFAULT_MODEL)
        if resolved:
            return str(resolved)
    return _DEFAULT_MODEL


def _resolve_api_key(settings: SettingsLoader | None = None, context: QuizContext | None = None) -> str | None:
    if context and context.gemini_api_key:
        return context.gemini_api_key
    if settings is not None:
        env_var = str(settings("environment.gemini_api_key_env", "GEMINI_API_KEY"))
        import os

        return os.getenv(env_var)
    return None


def _resolve_retry_policy(settings: SettingsLoader | None = None) -> RetryPolicy:
    if settings is None:
        return _default_retry_policy()
    try:
        max_retries = int(settings("playlist_quiz.max_retries", _DEFAULT_MAX_RETRIES))
    except (TypeError, ValueError):
        max_retries = _DEFAULT_MAX_RETRIES
    return RetryPolicy(
        max_retries=max_retries,
        initial_delay=1.0,
        max_delay=30.0,
        exponential_base=2.0,
        jitter=False,
    )


def _default_retry_policy() -> RetryPolicy:
    return RetryPolicy(
        max_retries=_DEFAULT_MAX_RETRIES,
        initial_delay=1.0,
        max_delay=30.0,
        exponential_base=2.0,
        jitter=False,
    )


def _load_prompt_template(prompt_name: str) -> str:
    import yaml

    path = get_prompt_path(prompt_name)
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if "prompt" not in data:
        raise KeyError(f"Prompt file {path!r} missing required 'prompt' key")
    return data["prompt"]


def _build_usage_record(
    *,
    provider: str,
    operation: str,
    model: str,
    usage: TokenUsage | None,
    success: bool,
    error_category: str | None,
    retry_count: int,
    run_id: str | None = None,
    video_id: str | None = None,
) -> UsageRecord:
    effective_usage = usage if usage is not None else _one_zero_usage()
    cost_usd = calculate_cost(
        provider=provider,
        model=model,
        input_tokens=effective_usage.input_tokens,
        output_tokens=effective_usage.output_tokens,
    )
    return UsageRecord(
        timestamp=datetime.now(UTC),
        provider=provider,
        operation=operation,
        model=model,
        input_tokens=effective_usage.input_tokens,
        output_tokens=effective_usage.output_tokens,
        total_tokens=effective_usage.total_tokens,
        cost_usd=cost_usd,
        cache_hit=False,
        run_id=run_id,
        video_id=video_id,
    )


class _GeminiProvider:
    """Internal encapsulator for Gemini API calls and session state."""

    def __init__(
        self,
        *,
        settings: SettingsLoader | None = None,
        usage_ledger: UsageLedgerSink | None = None,
        context: QuizContext | None = None,
    ) -> None:
        self._settings = settings
        self._usage_ledger = usage_ledger
        self._context = context
        self._model = _resolve_model(settings=settings, context=context)
        self._retry_policy = _resolve_retry_policy(settings=settings)
        self._prompt_template = _load_prompt_template(_PROMPT_KEY)
        self._video_id: str | None = None
        self._attempt_model: str = self._model

    def _emit_usage(
        self,
        *,
        usage: TokenUsage | None,
        success: bool,
        error_category: str | None,
        retry_count: int,
    ) -> None:
        if self._usage_ledger is None:
            return
        record = _build_usage_record(
            provider=_PROVIDER,
            operation=_OPERATION,
            model=self._attempt_model,
            usage=usage,
            success=success,
            error_category=error_category,
            retry_count=retry_count,
            video_id=self._video_id,
        )
        try:
            import asyncio

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._usage_ledger.record_usage(record))
            except RuntimeError:
                asyncio.run(self._usage_ledger.record_usage(record))
        except Exception:  # pragma: no cover
            logger.warning(
                "Usage ledger emission failed for %s/%s",
                _PROVIDER,
                self._attempt_model,
                exc_info=True,
            )

    def generate(self, transcript: TranscriptRef, title: str) -> QuizResult | None:
        """Run the Gemini call with retries. Returns QuizResult or None."""
        content = transcript.content if isinstance(transcript.content, TranscriptContent) else TranscriptContent()
        video_id_value = transcript.video_id.value if isinstance(transcript.video_id, VideoId) else str(transcript.video_id)

        transcript_text = content.cleaned_text or content.raw_srt
        if not transcript_text.strip():
            return None

        self._video_id = video_id_value
        prompt = (
            f"{self._prompt_template}\n\n"
            f"**Video Title**: {title}\n"
            f"**Transcript**:\n{transcript_text}"
        )

        max_retries = self._retry_policy.max_retries

        for attempt in range(1, max_retries + 1):
            try:
                self._attempt_model = self._model
                usage, content_text = self._call_gemini(prompt)
                if content_text and content_text.strip():
                    return QuizResult(
                        playlist_result=None,  # type: ignore[arg-type]
                        drive_folder_url=None,
                        content=content_text,
                        token_usage=usage,
                        success=True,
                    )
                logger.warning("Gemini returned an empty response for %s.", video_id_value)
                self._emit_usage(usage=usage, success=False, error_category="empty_response", retry_count=attempt - 1)
                return None
            except Exception as exc:  # noqa: BLE001
                wait_time = 2 ** attempt
                logger.warning(
                    "Error generating quiz for %s (Attempt %d/%d): %s. Retrying in %ds...",
                    video_id_value,
                    attempt,
                    max_retries,
                    exc,
                    wait_time,
                )
                if attempt < max_retries:
                    _time.sleep(wait_time)

        logger.exception("Failed to generate quiz for %s after %d attempts.", video_id_value, max_retries)
        return QuizResult(
            playlist_result=None,  # type: ignore[arg-type]
            drive_folder_url=None,
            content="",
            token_usage=None,
            success=False,
        )

    def _call_gemini(self, prompt: str) -> tuple[TokenUsage, str]:
        """Execute a single non-retrying Gemini API call.

        Returns a (TokenUsage, content_text) tuple.
        """
        api_key = _resolve_api_key(settings=self._settings, context=self._context)
        if not api_key:
            raise LLMProviderError("GEMINI_API_KEY is not set.")

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=self._model,
            contents=prompt,
        )
        usage = _extract_token_usage(response)
        content_text = getattr(response, "text", "") or ""
        return usage, content_text


class GeminiQuizProvider(QuizGeneratorPort):
    """Adapter that satisfies :class:`QuizGeneratorPort` using Gemini.

    Creates a new ``google.genai.Client`` per :meth:`generate_quiz` call for
    thread safety (no shared mutable client state), matching the thread-safety
    posture of :class:`~src.infrastructure.llm.openai.retry.OpenAIRetryableProvider`.
    """

    def __init__(
        self,
        settings: SettingsLoader,
        usage_ledger: UsageLedgerSink | None = None,
    ) -> None:
        self._settings = settings
        self._usage_ledger = usage_ledger

    async def generate_quiz(
        self,
        transcript: TranscriptRef,
        title: str,
        context: QuizContext,
    ) -> QuizResult:
        """Generate a quiz for one transcript via Gemini.

        The underlying SDK call is synchronous; the async wrapper makes this
        port drop-in compatible with callers that drive parsing via
        ``asyncio.gather`` or ``ThreadPoolExecutor`` without blocking the
        event loop.
        """
        provider = _GeminiProvider(
            settings=self._settings,
            usage_ledger=self._usage_ledger,
            context=context,
        )
        try:
            result = provider.generate(transcript=transcript, title=title)
            if result is not None:
                return result
            return QuizResult(
                playlist_result=None,  # type: ignore[arg-type]
                drive_folder_url=None,
                content="",
                token_usage=None,
                success=False,
            )
        except LLMProviderError as exc:
            logger.exception("Gemini provider error for %s: %s", transcript.video_id.value, exc)
            return QuizResult(
                playlist_result=None,  # type: ignore[arg-type]
                drive_folder_url=None,
                content="",
                token_usage=None,
                success=False,
            )
        except KeyError as exc:
            logger.exception("Prompt configuration error: %s", exc)
            return QuizResult(
                playlist_result=None,  # type: ignore[arg-type]
                drive_folder_url=None,
                content="",
                token_usage=None,
                success=False,
            )


__all__ = ["GeminiQuizProvider"]
