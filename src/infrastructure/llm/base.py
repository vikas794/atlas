"""Base classes and types for LLM providers.

This module defines the abstract infrastructure contracts that all LLM
provider adapters must satisfy. Adapters live in
``src/infrastructure/llm/<provider>/`` and are wired into domain code
through dependency-injected ``Port`` protocols.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple, Protocol

from src.domain.interfaces.usage_ledger import UsageRecord

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class TokenUsage(NamedTuple):
    """Token accounting returned by an LLM provider.

    All counts are best-effort; providers that do not surface usage
    information fall back to zeros.
    """

    input_tokens: int
    output_tokens: int
    total_tokens: int


class LLMResponse(NamedTuple):
    """Normalized response envelope returned by :meth:`LLMProvider.complete`."""

    content: str
    token_usage: TokenUsage | None
    model: str
    latency_ms: int


class SettingsLoader(Protocol):
    """Minimal surface area that providers need from the application config.

    The default implementation is :func:`src.utils.get_config`, but
    adapters accept any object that satisfies this protocol so they
    remain trivially testable.
    """

    def __call__(self, key_path: str, default: object = None) -> object:
        ...


class UsageLedgerSink(Protocol):
    """Optional sink for :class:`UsageRecord` emission.

    Adapters call this when an ``UsageLedgerPort`` is wired in; the
    default ``None`` sink makes emission a no-op so adapters can be
    used in unit tests and lightweight scripts without a ledger.
    """

    async def record_usage(self, record: UsageRecord) -> None:
        ...


@dataclass(frozen=True)
class RetryPolicy:
    """Configuration for :class:`RetryableProvider`.

    The exponential-backoff schedule is::

        delay = min(initial_delay * exponential_base ** attempt, max_delay)

    A small ``+/- jitter`` window is added when ``jitter`` is true to
    avoid retry-storm synchronization across parallel callers.
    """

    max_retries: int
    initial_delay: float
    max_delay: float
    exponential_base: float
    jitter: bool


class LLMProvider(ABC):
    """Abstract base class for LLM provider adapters.

    Subclasses implement :meth:`complete` and optionally override
    :meth:`_emit_usage`. The base class wires a settings loader and an
    optional usage ledger into a single uniform contract.
    """

    def __init__(
        self,
        settings: SettingsLoader,
        usage_ledger: UsageLedgerSink | None = None,
    ) -> None:
        self._settings = settings
        self._usage_ledger = usage_ledger

    @abstractmethod
    def complete(
        self,
        *,
        messages: list[dict],
        model: str,
        timeout: int | None = None,
    ) -> LLMResponse:
        """Issue a chat-completion request and return a normalized response.

        Args:
            messages: OpenAI-style chat messages (``role``/``content``).
            model: Provider model identifier (e.g. ``openai/gpt-5-mini``).
            timeout: Optional per-request timeout in seconds.

        Returns:
            A populated :class:`LLMResponse`.

        Raises:
            LLMProviderError: For transport or protocol failures.
        """
        raise NotImplementedError

    def _emit_usage(self, record: UsageRecord) -> None:
        """Forward a :class:`UsageRecord` to the configured ledger.

        Subclasses call this exactly once per ``complete`` call so the
        ledger sees every request — success or failure — without having
        to be wired into every call site.

        Failures in the ledger are logged but never propagated: usage
        reporting must not affect the business outcome of the call.
        """

        if self._usage_ledger is None:
            return
        try:
            import asyncio

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._usage_ledger.record_usage(record))
            except RuntimeError:
                asyncio.run(self._usage_ledger.record_usage(record))
        except Exception:  # pragma: no cover - ledger must never break callers
            logger.warning(
                "Usage ledger emission failed for %s/%s",
                record.provider,
                record.model,
                exc_info=True,
            )


class LLMProviderError(RuntimeError):
    """Raised when an LLM provider call fails irrecoverably."""


class RetryableProvider(LLMProvider):
    """Mixin base that adds bounded exponential-backoff retry.

    Subclasses implement :meth:`_attempt` (a single non-retrying call)
    and :meth:`_is_retryable` (classify exceptions into retry/no-retry).
    This class owns the backoff schedule and the per-attempt timing
    surfaced via :class:`LLMResponse.latency_ms`.
    """

    def __init__(
        self,
        settings: SettingsLoader,
        retry_policy: RetryPolicy,
        usage_ledger: UsageLedgerSink | None = None,
    ) -> None:
        super().__init__(settings=settings, usage_ledger=usage_ledger)
        self._retry_policy = retry_policy

    def complete(
        self,
        *,
        messages: list[dict],
        model: str,
        timeout: int | None = None,
    ) -> LLMResponse:
        """Run :meth:`_attempt` with bounded exponential-backoff retries.

        Implements the canonical schedule::

            delay = min(initial_delay * base ** attempt + jitter, max_delay)

        Each attempt is timed end-to-end; on exhaustion the last
        underlying exception is re-raised wrapped in
        :class:`LLMProviderError` so callers get a stable error type.
        """
        import time as _time

        attempt = 0
        last_exc: Exception | None = None
        while attempt <= self._retry_policy.max_retries:
            try:
                return self._attempt(
                    messages=messages, model=model, timeout=timeout, attempt=attempt
                )
            except Exception as exc:  # noqa: BLE001 - we re-classify below
                if not self._is_retryable(exc) or attempt == self._retry_policy.max_retries:
                    raise LLMProviderError(str(exc)) from exc
                last_exc = exc
                delay = self._compute_delay(attempt)
                logger.info(
                    "LLM call retryable error on attempt %d/%d: %s; sleeping %.2fs",
                    attempt + 1,
                    self._retry_policy.max_retries + 1,
                    exc,
                    delay,
                )
                _time.sleep(delay)
                attempt += 1
        raise LLMProviderError(str(last_exc) if last_exc else "retry exhausted")

    def _compute_delay(self, attempt: int) -> float:
        """Return the backoff delay (seconds) for ``attempt``.

        Applies jitter when enabled; the result is always clamped to
        ``max_delay`` so a misconfigured ``exponential_base`` cannot
        push us into multi-hour sleeps.
        """
        import random

        base = self._retry_policy.initial_delay * (
            self._retry_policy.exponential_base ** attempt
        )
        delay = min(base, self._retry_policy.max_delay)
        if self._retry_policy.jitter:
            jitter_window = min(delay, self._retry_policy.initial_delay)
            delay = max(0.0, delay + random.uniform(-jitter_window, jitter_window))
        return delay

    @abstractmethod
    def _attempt(
        self,
        *,
        messages: list[dict],
        model: str,
        timeout: int | None,
        attempt: int,
    ) -> LLMResponse:
        """Run a single non-retrying chat-completion call.

        Implementations must raise :class:`LLMProviderError` on
        permanent failures and any other exception for transient
        failures that should be retried.
        """
        raise NotImplementedError

    @abstractmethod
    def _is_retryable(self, exc: BaseException) -> bool:
        """Classify an exception as retryable or permanent."""
        raise NotImplementedError
