from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModelConfig:
    """Configuration for a single model."""
    provider: str
    model_id: str
    display_name: str
    max_tokens: int | None = None
    supports_streaming: bool = False
    pricing_input_per_1m: float = 0.0
    pricing_output_per_1m: float = 0.0
    capabilities: list[str] = field(default_factory=list)


class ModelRegistry:
    """Registry of known models and their configurations.

    Provides lookup by provider/model_id and pricing information for cost calculation.
    """

    def __init__(self) -> None:
        self._models: dict[str, dict[str, ModelConfig]] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        """Register built-in model configurations."""
        # OpenAI / OpenRouter models
        self.register(ModelConfig(
            provider="openai",
            model_id="openai/gpt-5-mini",
            display_name="GPT-5 Mini",
            max_tokens=128000,
            supports_streaming=True,
            pricing_input_per_1m=0.15,
            pricing_output_per_1m=0.60,
            capabilities=["chat", "function_calling", "vision"],
        ))
        self.register(ModelConfig(
            provider="openai",
            model_id="openai/gpt-4o-mini",
            display_name="GPT-4o Mini",
            max_tokens=128000,
            supports_streaming=True,
            pricing_input_per_1m=0.15,
            pricing_output_per_1m=0.60,
            capabilities=["chat", "function_calling", "vision"],
        ))
        self.register(ModelConfig(
            provider="openai",
            model_id="openai/gpt-4o",
            display_name="GPT-4o",
            max_tokens=128000,
            supports_streaming=True,
            pricing_input_per_1m=2.50,
            pricing_output_per_1m=10.00,
            capabilities=["chat", "function_calling", "vision"],
        ))

        # Gemini models
        self.register(ModelConfig(
            provider="gemini",
            model_id="gemini-3.6-flash",
            display_name="Gemini 3.6 Flash",
            max_tokens=1048576,
            supports_streaming=True,
            pricing_input_per_1m=0.075,
            pricing_output_per_1m=0.30,
            capabilities=["chat", "function_calling"],
        ))
        self.register(ModelConfig(
            provider="gemini",
            model_id="gemini-1.5-pro",
            display_name="Gemini 1.5 Pro",
            max_tokens=2097152,
            supports_streaming=True,
            pricing_input_per_1m=3.50,
            pricing_output_per_1m=10.50,
            capabilities=["chat", "function_calling", "long_context"],
        ))

    def register(self, config: ModelConfig) -> None:
        """Register a model configuration."""
        if config.provider not in self._models:
            self._models[config.provider] = {}
        self._models[config.provider][config.model_id] = config

    def get(self, provider: str, model_id: str) -> ModelConfig | None:
        """Get model config by provider and model_id."""
        return self._models.get(provider, {}).get(model_id)

    def get_by_full_id(self, full_id: str) -> ModelConfig | None:
        """Get model config by 'provider/model_id' string."""
        if "/" not in full_id:
            return None
        provider, model_id = full_id.split("/", 1)
        return self.get(provider, model_id)

    def list_models(self, provider: str | None = None) -> list[ModelConfig]:
        """List all registered models, optionally filtered by provider."""
        if provider:
            return list(self._models.get(provider, {}).values())
        return [m for models in self._models.values() for m in models.values()]

    def get_pricing(self, provider: str, model_id: str) -> tuple[float, float]:
        """Get (input_price_per_1m, output_price_per_1m) for a model."""
        config = self.get(provider, model_id)
        if config:
            return (config.pricing_input_per_1m, config.pricing_output_per_1m)
        return (0.0, 0.0)

    def calculate_cost(self, provider: str, model_id: str, input_tokens: int, output_tokens: int) -> float:
        """Calculate estimated cost in USD for given token counts."""
        input_price, output_price = self.get_pricing(provider, model_id)
        return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


# Global singleton instance
_default_registry: ModelRegistry | None = None


def get_model_registry() -> ModelRegistry:
    """Get the global ModelRegistry instance."""
    global _default_registry
    if _default_registry is None:
        _default_registry = ModelRegistry()
    return _default_registry


def set_model_registry(registry: ModelRegistry) -> None:
    """Set the global ModelRegistry instance (for testing)."""
    global _default_registry
    _default_registry = registry