"""Static facts about the providers the registry knows how to talk to.

Providers are not database rows (a model row just names its provider); this table only supplies
the label, the LiteLLM prefix, the default endpoint and how a key is health-checked.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    name: str
    label: str
    #: Prefix LiteLLM expects (``gemini/..``, ``openrouter/..``; OpenAI-compatible gateways: ``openai/..``).
    litellm_prefix: str
    default_api_base: str | None
    #: ``bearer-models`` (GET <base>/models with a Bearer key), ``gemini`` or ``openrouter``.
    health: str
    #: Environment variable the first-run import reads keys from.
    env_key: str
    default_timeout_seconds: float | None = None
    default_input_token_limit: int = 128_000


PROVIDERS: dict[str, ProviderInfo] = {
    "gemini": ProviderInfo(
        "gemini", "Gemini", "gemini", None, "gemini", "GEMINI_API_KEY",
        default_input_token_limit=1_000_000,
    ),
    "openrouter": ProviderInfo(
        "openrouter", "OpenRouter", "openrouter", "https://openrouter.ai/api/v1",
        "openrouter", "OPENROUTER_API_KEY",
    ),
    "freellmapi": ProviderInfo(
        "freellmapi", "FreeLLMAPI", "openai", "http://localhost:3001/v1",
        "bearer-models", "FREELLMAPI_API_KEY",
    ),
    "nvidia": ProviderInfo(
        "nvidia", "NVIDIA", "openai", "https://integrate.api.nvidia.com/v1",
        "bearer-models", "NVIDIA_API_KEY", default_timeout_seconds=3600.0,
    ),
    "openai": ProviderInfo(
        "openai", "OpenAI", "openai", None, "bearer-models", "OPENAI_API_KEY"
    ),
}


def provider_info(name: str) -> ProviderInfo:
    """The provider's facts; an unknown name is treated as a generic OpenAI-compatible one."""
    key = name.strip().lower()
    return PROVIDERS.get(key) or ProviderInfo(
        key, key.capitalize(), "openai", None, "bearer-models", f"{key.upper()}_API_KEY"
    )


def litellm_model_for(provider: str, model_id: str) -> str:
    return f"{provider_info(provider).litellm_prefix}/{model_id}"


def mask_key(value: str) -> str:
    """``abcd...wxyz``; short keys show only the last two characters."""
    if len(value) < 5:
        return "****"
    if len(value) <= 8:
        return "****" + value[-2:]
    return f"{value[:4]}...{value[-4:]}"
