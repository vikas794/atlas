from __future__ import annotations

from pydantic import BaseModel, Field


class UsageProviderAggregate(BaseModel):
    provider: str
    total_requests: int
    total_tokens: int
    total_cost_usd: float
    models_used: list[str] = Field(default_factory=list)


class UsageOperationAggregate(BaseModel):
    operation: str
    total_requests: int
    total_tokens: int
    total_cost_usd: float
    avg_tokens_per_request: float


class UsageCacheAggregate(BaseModel):
    total_hits: int
    total_misses: int
    hit_rate: float
    by_kind: dict[str, tuple[int, int]] = Field(default_factory=dict)


class UsageTimeRange(BaseModel):
    since: str | None = None
    until: str | None = None


class KeyAnalyticsRow(BaseModel):
    #: ``None`` only for attempts logged without a key.
    key_id: int | None
    #: ``None`` when the key was deleted before it was ever labelled.
    label: str | None
    provider: str
    requests: int
    successes: int
    errors: int
    success_rate: float
    avg_latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class ModelCostRow(BaseModel):
    provider: str
    model: str
    requests: int
    successes: int
    errors: int
    success_rate: float
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float
    avg_cost_usd: float
    cost_share: float


class ErrorCategoryCount(BaseModel):
    category: str
    count: int


class KeyAnalyticsResponse(BaseModel):
    range: str
    total_requests: int
    total_errors: int
    total_cost_usd: float
    by_key: list[KeyAnalyticsRow] = Field(default_factory=list)
    by_model: list[ModelCostRow] = Field(default_factory=list)
    errors: list[ErrorCategoryCount] = Field(default_factory=list)


class UsageAggregateResponse(BaseModel):
    total_requests: int
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_cost_usd: float
    cache_hit_rate: float
    by_provider: list[UsageProviderAggregate] = Field(default_factory=list)
    by_operation: list[UsageOperationAggregate] = Field(default_factory=list)
    cache_stats: UsageCacheAggregate | None = None
    time_range: UsageTimeRange | None = None
