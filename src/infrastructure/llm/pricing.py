"""LLM call cost, priced by LiteLLM's model-price table (no hand-kept rates).

Order of preference for one call: the cost LiteLLM already attached to the response, then
``litellm.cost_per_token`` for the exact model, then the same lookup with leading path parts
stripped (an OpenRouter-style ``openai/gpt-4o`` falls back to ``gpt-4o``), then the model's bare
name matched against every model LiteLLM prices. That last step prices a model served by a
reseller such as NVIDIA NIM (``deepseek-ai/deepseek-v4-pro``, ``z-ai/glm-5.2``) at the model's
published rate. A model LiteLLM has never heard of costs ``0.0`` rather than a guess.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any


def response_cost(response: Any) -> float | None:
    """LiteLLM's own price for this call, when it knows one (``None`` otherwise)."""
    hidden = getattr(response, "_hidden_params", None)
    if isinstance(hidden, dict):
        value = hidden.get("response_cost")
        if isinstance(value, int | float) and value > 0:
            return float(value)
    return None


def call_cost(
    litellm_model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    response: Any = None,
) -> float:
    """Estimated USD cost of one call; ``0.0`` when LiteLLM cannot price the model."""
    known = response_cost(response) if response is not None else None
    if known is not None:
        return known

    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")  # never fetch prices online
    import litellm

    litellm.suppress_debug_info = True
    parts = litellm_model.split("/")
    candidates = [litellm_model, *("/".join(parts[i:]) for i in range(1, len(parts)))]
    by_name = _priced_by_bare_name().get(parts[-1].lower().split(":")[0])
    for name in dict.fromkeys(candidates):
        try:
            prompt_cost, completion_cost = litellm.cost_per_token(
                model=name, prompt_tokens=input_tokens, completion_tokens=output_tokens
            )
        except Exception:
            continue
        total = float(prompt_cost) + float(completion_cost)
        if total > 0:
            return round(total, 6)

    # Last resort: the table entry itself (``cost_per_token`` needs a provider it can infer).
    entry = litellm.model_cost.get(by_name or "") or {}
    total = input_tokens * float(entry.get("input_cost_per_token") or 0) + output_tokens * float(
        entry.get("output_cost_per_token") or 0
    )
    return round(total, 6)


@lru_cache(maxsize=1)
def _priced_by_bare_name() -> dict[str, str]:
    """``{bare model name: LiteLLM key}``, preferring un-prefixed (first-party) entries."""
    import litellm

    index: dict[str, str] = {}
    for key in sorted(litellm.model_cost, key=lambda k: (k.count("/"), k)):
        index.setdefault(key.rsplit("/", 1)[-1].lower(), key)
    return index
