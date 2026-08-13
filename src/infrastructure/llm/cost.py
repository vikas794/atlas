from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class ModelPricing:
    """Pricing for a specific model (per 1M tokens)."""

    input_cost_per_million: float
    output_cost_per_million: float


class CostCalculator:
    """Calculate estimated costs for LLM API calls based on token usage.

    Pricing is based on publicly available rates (as of 2024) and should be
    updated when provider pricing changes.
    """

    OPENAI_PRICING: ClassVar[dict[str, ModelPricing]] = {
        "gpt-4o": ModelPricing(input_cost_per_million=2.50, output_cost_per_million=10.00),
        "gpt-4o-mini": ModelPricing(input_cost_per_million=0.15, output_cost_per_million=0.60),
        "gpt-4o-2024-08-06": ModelPricing(input_cost_per_million=2.50, output_cost_per_million=10.00),
        "gpt-4o-2024-05-13": ModelPricing(input_cost_per_million=5.00, output_cost_per_million=15.00),
        "gpt-4-turbo": ModelPricing(input_cost_per_million=10.00, output_cost_per_million=30.00),
        "gpt-4": ModelPricing(input_cost_per_million=30.00, output_cost_per_million=60.00),
        "gpt-3.5-turbo": ModelPricing(input_cost_per_million=0.50, output_cost_per_million=1.50),
        "gpt-5": ModelPricing(input_cost_per_million=5.00, output_cost_per_million=15.00),
        "gpt-5-mini": ModelPricing(input_cost_per_million=0.25, output_cost_per_million=1.00),
        "openai/gpt-5": ModelPricing(input_cost_per_million=5.00, output_cost_per_million=15.00),
        "openai/gpt-5-mini": ModelPricing(input_cost_per_million=0.25, output_cost_per_million=1.00),
        "openai/gpt-4o": ModelPricing(input_cost_per_million=2.50, output_cost_per_million=10.00),
        "openai/gpt-4o-mini": ModelPricing(input_cost_per_million=0.15, output_cost_per_million=0.60),
    }

    GEMINI_PRICING: ClassVar[dict[str, ModelPricing]] = {
        "gemini-1.5-pro": ModelPricing(input_cost_per_million=3.50, output_cost_per_million=10.50),
        "gemini-1.5-pro-002": ModelPricing(input_cost_per_million=3.50, output_cost_per_million=10.50),
        "gemini-1.5-flash": ModelPricing(input_cost_per_million=0.075, output_cost_per_million=0.30),
        "gemini-1.5-flash-002": ModelPricing(input_cost_per_million=0.075, output_cost_per_million=0.30),
        "gemini-1.0-pro": ModelPricing(input_cost_per_million=0.50, output_cost_per_million=1.50),
        "gemini-2.0-flash-exp": ModelPricing(input_cost_per_million=0.075, output_cost_per_million=0.30),
        "gemini-3.6-flash": ModelPricing(input_cost_per_million=0.075, output_cost_per_million=0.30),
    }

    ANTHROPIC_PRICING: ClassVar[dict[str, ModelPricing]] = {
        "claude-3-opus-20240229": ModelPricing(input_cost_per_million=15.00, output_cost_per_million=75.00),
        "claude-3-sonnet-20240229": ModelPricing(input_cost_per_million=3.00, output_cost_per_million=15.00),
        "claude-3-haiku-20240307": ModelPricing(input_cost_per_million=0.25, output_cost_per_million=1.25),
        "claude-3.5-sonnet-20241022": ModelPricing(input_cost_per_million=3.00, output_cost_per_million=15.00),
        "claude-3.5-haiku-20241022": ModelPricing(input_cost_per_million=0.80, output_cost_per_million=4.00),
    }

    @classmethod
    def calculate_cost(
        cls,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """Calculate estimated cost in USD for a single API call."""
        provider_lower = provider.lower()

        if provider_lower in ("openai", "openrouter"):
            pricing = cls._match_model(cls.OPENAI_PRICING, model)
        elif provider_lower == "gemini":
            pricing = cls._match_model(cls.GEMINI_PRICING, model)
        elif provider_lower == "anthropic":
            pricing = cls._match_model(cls.ANTHROPIC_PRICING, model)
        else:
            return 0.0

        if pricing is None:
            return 0.0

        input_cost = (input_tokens / 1_000_000) * pricing.input_cost_per_million
        output_cost = (output_tokens / 1_000_000) * pricing.output_cost_per_million
        return round(input_cost + output_cost, 6)

    @classmethod
    def _match_model(cls, pricing_dict: dict[str, ModelPricing], model: str) -> ModelPricing | None:
        """Match model name to pricing, handling variants and prefixes."""
        model_lower = model.lower()

        if model_lower in pricing_dict:
            return pricing_dict[model_lower]

        for key, pricing in pricing_dict.items():
            if model_lower.startswith(key) or key in model_lower:
                return pricing

        return None


def calculate_cost(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Convenience function for calculating cost."""
    return CostCalculator.calculate_cost(provider, model, input_tokens, output_tokens)