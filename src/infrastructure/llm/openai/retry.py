"""OpenAI-compatible retry-aware provider.

This module wraps a single ``OpenAI`` client with bounded
exponential-backoff retries. The wrapper is intentionally narrow:
:class:`RetryableProvider` owns the schedule, this subclass owns the
classification of upstream SDK exceptions.

Classification rules:
    * Retry on HTTP 429 (rate limit), 5xx (server errors) and
      transport-level timeouts.
    * Do NOT retry on other 4xx (including 401, 403, 404) — those are
      configuration bugs and retrying wastes the user's quota.
"""

from __future__ import annotations

import logging
import time as _time
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)

from src.infrastructure.llm.base import (
    LLMProviderError,
    LLMResponse,
    RetryableProvider,
    RetryPolicy,
    SettingsLoader,
    TokenUsage,
    UsageLedgerSink,
)

logger = logging.getLogger(__name__)


_RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    RateLimitError,
    APITimeoutError,
    APIConnectionError,
    InternalServerError,
)


class OpenAIRetryableProvider(RetryableProvider):
    """Retry-aware OpenAI-compatible provider.

    Uses the OpenAI Python SDK with an ``OPENROUTER_API_KEY``-style
    ``base_url`` so the same adapter can target OpenRouter, OpenAI or
    any other OpenAI-compatible endpoint by configuration.
    """

    def __init__(
        self,
        *,
        settings: SettingsLoader,
        api_key: str | None,
        base_url: str | None,
        retry_policy: RetryPolicy,
        usage_ledger: UsageLedgerSink | None = None,
        client_factory: Any | None = None,
    ) -> None:
        super().__init__(
            settings=settings,
            retry_policy=retry_policy,
            usage_ledger=usage_ledger,
        )
        self._api_key = api_key
        self._base_url = base_url
        self._client_factory = client_factory
        self._cached_client = None

    def _client(self):
        """Return (and lazily build) the underlying OpenAI client.

        Thread safety: the OpenAI SDK is documented as thread-safe for
        concurrent use, so a single shared client is fine. A custom
        ``client_factory`` can be injected by tests.
        """
        if self._client_factory is not None:
            return self._client_factory()
        if self._cached_client is None:
            from openai import OpenAI

            kwargs: dict[str, Any] = {}
            if self._api_key:
                kwargs["api_key"] = self._api_key
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._cached_client = OpenAI(**kwargs)
        return self._cached_client

    def _attempt(
        self,
        *,
        messages: list[dict],
        model: str,
        timeout: int | None,
        attempt: int,
    ) -> LLMResponse:
        """Issue a single chat-completion request.

        The attempt boundary (not the retry boundary) is where
        ``latency_ms`` is measured — this matches what most
        observability stacks report as "API latency".
        """
        client = self._client()
        start = _time.monotonic()
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                timeout=timeout,
            )
        except Exception as exc:
            elapsed_ms = int((_time.monotonic() - start) * 1000)
            logger.debug(
                "OpenAI attempt %d failed after %dms: %s",
                attempt + 1,
                elapsed_ms,
                exc,
            )
            raise
        elapsed_ms = int((_time.monotonic() - start) * 1000)

        content = ""
        if response.choices:
            message = response.choices[0].message
            content = message.content or ""

        token_usage = self._extract_usage(response)
        return LLMResponse(
            content=content,
            token_usage=token_usage,
            model=model,
            latency_ms=elapsed_ms,
        )

    def _is_retryable(self, exc: BaseException) -> bool:
        """Classify an exception as retryable.

        The OpenAI SDK raises typed exceptions; mapping them here
        keeps :class:`RetryableProvider` provider-agnostic. Unknown
        exceptions are treated as non-retryable (fail-fast) — better
        to surface a bug than to silently retry forever.
        """
        if isinstance(exc, _RETRYABLE_EXCEPTIONS):
            return True
        if isinstance(
            exc,
            AuthenticationError | BadRequestError | NotFoundError | PermissionDeniedError | ConflictError | UnprocessableEntityError,
        ):
            return False
        return False

    @staticmethod
    def _extract_usage(response: Any) -> TokenUsage | None:
        """Extract token usage from an OpenAI response.

        OpenRouter and other proxies sometimes omit ``response.usage``
        for non-streaming chat calls; we surface that as ``None`` so
        the caller can choose how to record it in the ledger.
        """
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
        if total_tokens == 0 and (input_tokens or output_tokens):
            total_tokens = input_tokens + output_tokens
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )


__all__ = ["OpenAIRetryableProvider", "LLMProviderError"]
