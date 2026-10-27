"""Static facts about the providers the registry knows how to talk to.

Providers are not database rows (a model row just names its provider); this table only supplies
the label, the LiteLLM prefix, the default endpoint and how a key is health-checked.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache


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


#: LiteLLM providers that sign in with OAuth or need cloud credentials rather than one API key;
#: looking them up can even start a device-login prompt, so they are never probed.
_NOT_API_KEY = frozenset({"github_copilot", "azure_ai", "chatgpt"})

_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

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


@lru_cache(maxsize=1)
def _litellm_providers() -> dict[str, ProviderInfo]:
    """Providers LiteLLM prices chat models for and reaches through an OpenAI-style API.

    These take one bearer key and expose ``GET <base>/models``, so keys, health checks and the
    live model list work without a hand-written entry. LiteLLM routes by the provider's own
    prefix (``groq/..``) and supplies the endpoint and key variable. Providers that need more
    than an API key (Bedrock, Azure, Vertex, ...) are not in LiteLLM's OpenAI-compatible set and
    stay out.
    """
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")  # never fetch prices online
    import litellm

    priced = {
        info.get("litellm_provider")
        for info in litellm.model_cost.values()
        if isinstance(info, dict) and info.get("mode") == "chat"
    }
    found: dict[str, ProviderInfo] = {}
    for name in sorted(set(litellm.constants.openai_compatible_providers) & priced):
        if name in PROVIDERS or name in _NOT_API_KEY or not _SLUG.match(name):
            continue
        try:
            _, _, _, base = litellm.get_llm_provider(model=f"{name}/model")
            missing = litellm.validate_environment(model=f"{name}/model").get("missing_keys")
        except Exception:
            continue
        if not base:
            continue
        env_key = missing[0] if missing else f"{name.upper().replace('-', '_')}_API_KEY"
        label = name.replace("_", " ").replace("-", " ").title()
        found[name] = ProviderInfo(name, label, name, base, "bearer-models", env_key)
    return found


def known_providers() -> dict[str, ProviderInfo]:
    """The curated providers first, then every other one LiteLLM can serve, by label."""
    extra = sorted(_litellm_providers().values(), key=lambda info: info.label.lower())
    return {**PROVIDERS, **{info.name: info for info in extra}}


def provider_info(name: str) -> ProviderInfo:
    """The provider's facts; an unknown name is treated as a generic OpenAI-compatible one."""
    key = name.strip().lower()
    return (
        PROVIDERS.get(key)
        or _litellm_providers().get(key)
        or ProviderInfo(
            key, key.capitalize(), "openai", None, "bearer-models", f"{key.upper()}_API_KEY"
        )
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
