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
