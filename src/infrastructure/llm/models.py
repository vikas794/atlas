"""The ordered list of LLM models notes generation may use.

One :class:`ModelSpec` per model, tried in order (the first is the primary, the rest are
fallbacks). Specs come from the ``notes_models`` setting; when that is empty they are derived
from the legacy single-provider settings (``notes_provider``, ``gemini_model`` ...), so an
existing ``config.yaml`` keeps working.

A model is named the way LiteLLM names it, ``<provider>/<model>``: ``gemini/gemini-3.5-flash``,
``openrouter/openai/gpt-5-mini``, ``openai/gpt-5-mini``. Any OpenAI-compatible gateway (FreeLLMAPI,
a local server) is ``openai/<model>`` plus an ``api_base``. Supporting a new provider is
therefore a config entry, not code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from src.infrastructure.llm.base import SettingsLoader

#: Environment variable that holds the key for a provider when an entry names none.
_DEFAULT_KEY_ENVS = {
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
}

#: Pre-flight input limit (tokens) when an entry names none. Deliberately the smallest window in
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
_DEFAULT_GEMINI_INPUT_TOKEN_LIMIT = 1_000_000

#: The Gemini models offered in the UI dropdown (newest first).
GEMINI_MODEL_CHOICES = (
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3-flash-preview",
    "gemini-flash-latest",
    "gemini-3.1-pro-preview",
)


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """One model the notes generator may call."""

    #: LiteLLM model string, e.g. ``gemini/gemini-3.5-flash``.
    litellm_model: str
    #: Name of the environment variable holding the key(s). Safe to log.
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


def default_key_env(provider: str) -> str:
    """The conventional key variable for ``provider`` (``GROQ_API_KEY`` for ``groq``)."""
    key = provider.strip().lower()
    return _DEFAULT_KEY_ENVS.get(key) or f"{key.upper().replace('-', '_')}_API_KEY"


def resolve_model_specs(settings: SettingsLoader) -> tuple[ModelSpec, ...]:
    """The models to try, in order. Never empty; raises ``ValueError`` on an unusable entry."""
    configured = settings("notes_models", None) or []
    if not isinstance(configured, list):
        raise ValueError("notes_models must be a list of model mappings.")
    specs: list[ModelSpec] = []
    for index, entry in enumerate(configured, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"notes_models[{index}] must be a mapping with a 'model' key.")
        model = str(entry.get("model") or "").strip()
        provider, _, name = model.partition("/")
        if not provider or not name:
            raise ValueError(
                f"notes_models[{index}].model must look like '<provider>/<model>' (got {model!r})."
            )
        specs.append(
            ModelSpec(
                litellm_model=model,
                api_key_env=str(entry.get("api_key_env") or "").strip()
                or default_key_env(provider),
                api_base=str(entry.get("api_base") or "").strip() or None,
                input_token_limit=int(entry.get("input_token_limit") or DEFAULT_INPUT_TOKEN_LIMIT),
            )
        )
    if specs:
        return tuple(specs)
    return (_legacy_spec(settings),)


def _legacy_spec(settings: SettingsLoader) -> ModelSpec:
    """The single model named by ``notes_provider`` / ``notes_model`` (the pre-LiteLLM settings)."""
    provider = str(settings("notes_provider", "gemini") or "gemini").strip().lower()
    explicit = str(settings("notes_model", "") or "").strip()

    if provider == "gemini":
        name = explicit or str(settings("gemini_model", DEFAULT_GEMINI_MODEL) or "").strip()
        return ModelSpec(
            litellm_model=f"gemini/{name or DEFAULT_GEMINI_MODEL}",
            api_key_env=str(settings("gemini_api_key_env", "GEMINI_API_KEY")),
            input_token_limit=int(
                cast(
                    int | str,
                    settings("gemini_input_token_limit", _DEFAULT_GEMINI_INPUT_TOKEN_LIMIT),
                )
                or _DEFAULT_GEMINI_INPUT_TOKEN_LIMIT
            ),
        )
    if provider == "openrouter":
        name = explicit or str(settings("openrouter_model", "") or "").strip()
        base = str(settings("openrouter_base_url", "") or "").strip()
        return ModelSpec(
            litellm_model=f"openrouter/{name or DEFAULT_OPENROUTER_MODEL}",
            api_key_env=str(settings("openrouter_api_key_env", "OPENROUTER_API_KEY")),
            api_base=base or DEFAULT_OPENROUTER_BASE_URL,
            input_token_limit=int(
                cast(
                    int | str,
                    settings("openrouter_input_token_limit", DEFAULT_INPUT_TOKEN_LIMIT)
                )
                or DEFAULT_INPUT_TOKEN_LIMIT
            ),
        )
    if provider == "freellmapi":
        name = explicit or str(settings("freellmapi_model", "") or "").strip()
        if not name:
            raise ValueError(
                "No FreeLLMAPI model is selected. Pick one in Settings > Notes generation "
                "(or set freellmapi_model)."
            )
        base = str(settings("freellmapi_base_url", "") or "").strip()
        return ModelSpec(
            litellm_model=f"openai/{name}",
            api_key_env=str(settings("freellmapi_api_key_env", "FREELLMAPI_API_KEY")),
            api_base=base or DEFAULT_FREELLMAPI_BASE_URL,
            input_token_limit=int(
                cast(
                    int | str,
                    settings("freellmapi_input_token_limit", DEFAULT_INPUT_TOKEN_LIMIT),
                )
                or DEFAULT_INPUT_TOKEN_LIMIT
            ),
            provider_name="freellmapi",
        )
    if provider == "nvidia":
        name = explicit or str(settings("nvidia_model", "") or "").strip() or DEFAULT_NVIDIA_MODEL
        base = str(settings("nvidia_base_url", "") or "").strip()
        return ModelSpec(
            litellm_model=f"openai/{name}",
            api_key_env=str(settings("nvidia_api_key_env", "NVIDIA_API_KEY")),
            api_base=base or DEFAULT_NVIDIA_BASE_URL,
            input_token_limit=int(
                cast(int | str, settings("nvidia_input_token_limit", DEFAULT_INPUT_TOKEN_LIMIT))
                or DEFAULT_INPUT_TOKEN_LIMIT
            ),
            provider_name="nvidia",
            request_timeout_seconds=float(
                cast(
                    float | str,
                    settings(
                        "nvidia_request_timeout_seconds", DEFAULT_NVIDIA_REQUEST_TIMEOUT_SECONDS
                    ),
                )
                or DEFAULT_NVIDIA_REQUEST_TIMEOUT_SECONDS
            ),
        )
    raise ValueError(
        f"Unknown notes_provider {provider!r} (from settings 'notes_provider'); supported "
        "providers are: gemini, openrouter, freellmapi, nvidia. Or list models in 'notes_models'."
    )


__all__ = [
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_INPUT_TOKEN_LIMIT",
    "DEFAULT_OPENROUTER_MODEL",
    "DEFAULT_FREELLMAPI_BASE_URL",
    "DEFAULT_NVIDIA_BASE_URL",
    "DEFAULT_NVIDIA_MODEL",
    "GEMINI_MODEL_CHOICES",
    "ModelSpec",
    "default_key_env",
    "resolve_model_specs",
]
