from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol


class TimeRange(Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    ALL = "all"


@dataclass(frozen=True)
class UsageRecord:
    timestamp: datetime
    provider: str
    operation: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float
    cache_hit: bool
    run_id: str | None = None
    video_id: str | None = None


@dataclass(frozen=True)
class UsageAggregate:
    total_requests: int
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_cost_usd: float
    cache_hit_rate: float


@dataclass(frozen=True)
class ProviderAggregate:
    provider: str
    total_requests: int
    total_tokens: int
    total_cost_usd: float
    models_used: list[str]


@dataclass(frozen=True)
class OperationAggregate:
    operation: str
    total_requests: int
    total_tokens: int
    total_cost_usd: float
    avg_tokens_per_request: float


@dataclass(frozen=True)
class CacheAggregate:
    total_hits: int
    total_misses: int
    hit_rate: float
    by_kind: dict[str, tuple[int, int]]


class UsageLedgerPort(Protocol):
    async def record_usage(self, record: UsageRecord) -> None:
        """Record a usage event."""
        ...

    async def get_aggregate(self, time_range: TimeRange = TimeRange.ALL) -> UsageAggregate:
        """Get aggregated usage statistics."""
        ...

    async def get_by_provider(
        self, time_range: TimeRange = TimeRange.ALL
    ) -> list[ProviderAggregate]:
        """Get usage aggregated by provider."""
        ...

    async def get_by_operation(
        self, time_range: TimeRange = TimeRange.ALL
    ) -> list[OperationAggregate]:
        """Get usage aggregated by operation type."""
        ...

    async def get_cache_stats(
        self, time_range: TimeRange = TimeRange.ALL
    ) -> CacheAggregate:
        """Get cache hit/miss statistics."""
        ...
