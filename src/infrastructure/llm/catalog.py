"""Chat models LiteLLM knows for a provider, from its bundled model table (no key, no network).

The add-model picker merges this with the provider's live list. The table is a snapshot, so it
lags behind new models and is empty for gateways it does not price (OpenRouter, NVIDIA NIM and
FreeLLMAPI return nothing here and rely on the live list).
"""

from __future__ import annotations

import os

#: Our provider name -> LiteLLM's ``litellm_provider`` where they differ (others share the name).
_LITELLM_PROVIDER = {
    "nvidia": "nvidia_nim",
}


def catalog_models(provider: str) -> dict[str, int | None]:
    """``{model id: max input tokens}`` for ``provider``'s chat models, as LiteLLM names them."""
    wanted = _LITELLM_PROVIDER.get(provider, provider)

    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")  # never fetch prices online
    import litellm

    found: dict[str, int | None] = {}
    for key, info in litellm.model_cost.items():
        if not isinstance(info, dict):
            continue
        if info.get("litellm_provider") != wanted or info.get("mode") != "chat":
            continue
        model_id = key.removeprefix(f"{wanted}/")
        limit = info.get("max_input_tokens")
        found[model_id] = limit if isinstance(limit, int) and limit > 0 else None
    return found


__all__ = ["catalog_models"]
