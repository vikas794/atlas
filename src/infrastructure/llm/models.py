"""The model a notes call is made with.

One :class:`ModelSpec` per attempt, built from a model-registry row
(:func:`src.infrastructure.llm.litellm_notes._spec_from_row`). A model is named the way LiteLLM
names it, ``<provider>/<model>``: ``gemini/gemini-3.5-flash``, ``openrouter/openai/gpt-5-mini``,
``openai/gpt-5-mini``. Any OpenAI-compatible gateway (FreeLLMAPI, a local server) is
``openai/<model>`` plus an ``api_base``. Which models exist, their order and their keys are data
(the Models and Keys pages), not code.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Pre-flight input limit (tokens) when a model names none. Deliberately the smallest window in
#: common use: a wrong guess then refuses early instead of paying for a call the provider rejects.
DEFAULT_INPUT_TOKEN_LIMIT = 128_000

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
DEFAULT_OPENROUTER_MODEL = "openai/gpt-5-mini"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_FREELLMAPI_BASE_URL = "http://localhost:3001/v1"
DEFAULT_NVIDIA_MODEL = "deepseek-ai/deepseek-v4.1-flash"
DEFAULT_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
#: NVIDIA's hosted models answer slowly; the generic notes timeout would abort them.
DEFAULT_NVIDIA_REQUEST_TIMEOUT_SECONDS = 3600.0


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """One model the notes generator may call."""

    #: LiteLLM model string, e.g. ``gemini/gemini-3.5-flash``.
    litellm_model: str
    #: ``registry:<key label>`` for a registry key (safe to log; see ``REGISTRY_KEY_PREFIX``).
    api_key_env: str
    #: Custom endpoint for OpenAI-compatible gateways; ``None`` for the provider's own.
    api_base: str | None = None
    input_token_limit: int = DEFAULT_INPUT_TOKEN_LIMIT
    #: Identity to record when it differs from the LiteLLM prefix (FreeLLMAPI is ``openai/...``).
    provider_name: str | None = None
    #: Per-call timeout (seconds) overriding ``notes_request_timeout_seconds`` for slow providers.
    request_timeout_seconds: float | None = None

    @property
    def provider(self) -> str:
        """Provider identity stored on ``Summary.provider`` and the usage ledger."""
        return self.provider_name or self.litellm_model.split("/", 1)[0].strip().lower()

    @property
    def model(self) -> str:
        """The model name without the provider prefix (what the usage ledger records)."""
        return self.litellm_model.split("/", 1)[1].strip()

    @property
    def label(self) -> str:
        """Human label for messages and logs, e.g. ``Gemini`` or ``OpenRouter``."""
        names = {
            "openrouter": "OpenRouter",
            "openai": "OpenAI",
            "gemini": "Gemini",
            "freellmapi": "FreeLLMAPI",
            "nvidia": "NVIDIA",
        }
        return names.get(self.provider, self.provider.capitalize())


__all__ = [
    "DEFAULT_FREELLMAPI_BASE_URL",
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_INPUT_TOKEN_LIMIT",
    "DEFAULT_NVIDIA_BASE_URL",
    "DEFAULT_NVIDIA_MODEL",
    "DEFAULT_NVIDIA_REQUEST_TIMEOUT_SECONDS",
    "DEFAULT_OPENROUTER_BASE_URL",
    "DEFAULT_OPENROUTER_MODEL",
    "ModelSpec",
]
