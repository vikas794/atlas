"""Request and response bodies of the model registry API (``/api/llm``). snake_case throughout."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_SLUG = r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderOut(BaseModel):
    name: str
    label: str
    default_api_base: str | None
    key_count: int
    usable_key_count: int


class ProvidersResponse(BaseModel):
    providers: list[ProviderOut]


class ModelOut(BaseModel):
    id: int
    provider: str
    model_id: str
    display_name: str
    litellm_model: str
    api_base: str | None
    input_token_limit: int
    request_timeout_seconds: float | None
    rpm_limit: int | None
    rpd_limit: int | None
    tpm_limit: int | None
    tpd_limit: int | None
    enabled: bool
    source: str
    priority: int | None
    chain_enabled: bool


class ModelsResponse(BaseModel):
    models: list[ModelOut]


class FallbackEntryOut(ModelOut):
    key_count: int
    #: Keys of this provider that are switched on in the Keys tab (whatever their health).
    enabled_key_count: int
    usable_key_count: int
    #: Why no key can serve this model right now (``no_keys``, ``cooling_down``, ...), else None.
    blocked: str | None


class FallbackResponse(BaseModel):
    entries: list[FallbackEntryOut]


class ModelCreate(_Strict):
    provider: str = Field(min_length=1, max_length=40, pattern=_SLUG)
    model_id: str = Field(min_length=1, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)
    api_base: str | None = Field(default=None, max_length=500)
    input_token_limit: int | None = Field(default=None, ge=1)
    request_timeout_seconds: float | None = Field(default=None, gt=0)
    rpm_limit: int | None = Field(default=None, ge=1)
    rpd_limit: int | None = Field(default=None, ge=1)
    tpm_limit: int | None = Field(default=None, ge=1)
    tpd_limit: int | None = Field(default=None, ge=1)


class ModelUpdate(_Strict):
    """Only the fields present are changed; a limit sent as null becomes unlimited."""

    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    api_base: str | None = Field(default=None, max_length=500)
    input_token_limit: int | None = Field(default=None, ge=1)
    request_timeout_seconds: float | None = Field(default=None, gt=0)
    rpm_limit: int | None = Field(default=None, ge=1)
    rpd_limit: int | None = Field(default=None, ge=1)
    tpm_limit: int | None = Field(default=None, ge=1)
    tpd_limit: int | None = Field(default=None, ge=1)
    enabled: bool | None = None


class ModelTestResponse(BaseModel):
    success: bool
    latency_ms: int | None
    error: str | None


class ChainEntryIn(_Strict):
    model_id: int
    priority: int = Field(ge=1)
    enabled: bool


class ChainUpdate(_Strict):
    entries: list[ChainEntryIn] = Field(max_length=500)


class CooldownOut(BaseModel):
    model_id: str
    expires_at: float
    source: str


class KeyOut(BaseModel):
    id: int
    provider: str
    label: str
    masked_key: str
    status: str
    enabled: bool
    consecutive_failures: int
    last_checked_at: str | None
    last_healthy_at: str | None
    last_health_error: str | None
    monthly_request_cap: int | None
    monthly_token_cap: int | None
    cooldowns: list[CooldownOut]
    #: True when repeated invalid checks switched the key off.
    auto_disabled: bool


class KeysResponse(BaseModel):
    keys: list[KeyOut]


class KeyCreate(_Strict):
    provider: str = Field(min_length=1, max_length=40, pattern=_SLUG)
    key: str = Field(min_length=1, max_length=2000)
    label: str | None = Field(default=None, max_length=100)
    monthly_request_cap: int | None = Field(default=None, ge=0)
    monthly_token_cap: int | None = Field(default=None, ge=0)


class KeyUpdate(_Strict):
    """Only the fields present are changed; a cap of 0 or null means unlimited."""

    label: str | None = Field(default=None, min_length=1, max_length=100)
    key: str | None = Field(default=None, min_length=1, max_length=2000)
    enabled: bool | None = None
    monthly_request_cap: int | None = Field(default=None, ge=0)
    monthly_token_cap: int | None = Field(default=None, ge=0)


class KeyReveal(BaseModel):
    key: str


class ClearedResponse(BaseModel):
    cleared: int


class AvailableModelsResponse(BaseModel):
    provider: str
    models: list[str]
    #: True when the provider's own list was reached (otherwise only LiteLLM's table is shown).
    live: bool = False
    #: Why the live list is missing, when a key exists but the provider could not be queried.
    warning: str | None = None
    #: Input-token limit LiteLLM knows per model id, used to prefill the form.
    input_token_limits: dict[str, int] = Field(default_factory=dict)


class ModelBulkCreate(_Strict):
    """Add many models of one provider at once, switched off so the chain is unchanged."""

    provider: str = Field(min_length=1, max_length=40, pattern=_SLUG)
    model_ids: list[str] = Field(min_length=1, max_length=500)
    api_base: str | None = Field(default=None, max_length=500)


class ModelBulkResponse(BaseModel):
    added: int
    skipped: int


class RateLimitUsageResponse(BaseModel):
    rows: list[dict[str, Any]]


__all__ = [
    "AvailableModelsResponse",
    "ChainEntryIn",
    "ChainUpdate",
    "ClearedResponse",
    "CooldownOut",
    "FallbackEntryOut",
    "FallbackResponse",
    "KeyCreate",
    "KeyOut",
    "KeyReveal",
    "KeyUpdate",
    "ModelBulkCreate",
    "ModelBulkResponse",
    "KeysResponse",
    "ModelCreate",
    "ModelOut",
    "ModelTestResponse",
    "ModelUpdate",
    "ModelsResponse",
    "ProviderOut",
    "ProvidersResponse",
    "RateLimitUsageResponse",
]
