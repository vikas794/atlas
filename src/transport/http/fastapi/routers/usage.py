from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from src.domain.interfaces.usage_ledger import UsageLedgerPort
from src.transport.http.fastapi.schemas.usage import UsageAggregateResponse

router = APIRouter(prefix="/api", tags=["usage"])


def get_usage_ledger() -> UsageLedgerPort:
    """Dependency injection for UsageLedgerPort.

    In production, this would be wired to a concrete implementation.
    """
    from backend.storage.settings import get_settings
    from src.infrastructure.storage.sql import SqlUsageLedger

    settings = get_settings()
    return SqlUsageLedger(settings["database_path"])


usage_ledger_dependency = Depends(get_usage_ledger)


@router.get("/usage", response_model=UsageAggregateResponse)
async def get_usage_aggregate(
    provider: str | None = Query(None, description="Filter by provider (e.g., openai, anthropic)"),
    model: str | None = Query(None, description="Filter by model name"),
    operation: str | None = Query(None, description="Filter by operation type"),
    since: str | None = Query(None, description="ISO datetime string for start of range"),
    until: str | None = Query(None, description="ISO datetime string for end of range"),
    cache_status: bool | None = Query(None, description="Filter by cache hit/miss status"),
    ledger: UsageLedgerPort = usage_ledger_dependency,
) -> UsageAggregateResponse:
    """Get aggregated usage statistics with optional filters."""
    # Use the refactor outline interface method if available
    if hasattr(ledger, "aggregate"):
        aggregate = await ledger.aggregate(
            provider=provider,
            model=model,
            operation=operation,
            since=since,
            until=until,
            cache_status=cache_status,
        )
    else:
        # Fallback to domain protocol method
        aggregate = await ledger.get_aggregate()

    # Get detailed breakdowns
    by_provider = []
    by_operation = []
    cache_stats = None

    if hasattr(ledger, "get_by_provider"):
        provider_aggregates = await ledger.get_by_provider()
        by_provider = [
            {
                "provider": p.provider,
                "total_requests": p.total_requests,
                "total_tokens": p.total_tokens,
                "total_cost_usd": p.total_cost_usd,
                "models_used": p.models_used,
            }
            for p in provider_aggregates
        ]

    if hasattr(ledger, "get_by_operation"):
        operation_aggregates = await ledger.get_by_operation()
        by_operation = [
            {
                "operation": o.operation,
                "total_requests": o.total_requests,
                "total_tokens": o.total_tokens,
                "total_cost_usd": o.total_cost_usd,
                "avg_tokens_per_request": o.avg_tokens_per_request,
            }
            for o in operation_aggregates
        ]

    if hasattr(ledger, "get_cache_stats"):
        cache_agg = await ledger.get_cache_stats()
        cache_stats = {
            "total_hits": cache_agg.total_hits,
            "total_misses": cache_agg.total_misses,
            "hit_rate": cache_agg.hit_rate,
            "by_kind": cache_agg.by_kind,
        }

    return UsageAggregateResponse(
        total_requests=aggregate.total_requests,
        total_input_tokens=aggregate.total_input_tokens,
        total_output_tokens=aggregate.total_output_tokens,
        total_tokens=aggregate.total_tokens,
        total_cost_usd=aggregate.total_cost_usd,
        cache_hit_rate=aggregate.cache_hit_rate,
        by_provider=by_provider,
        by_operation=by_operation,
        cache_stats=cache_stats,
        time_range={"since": since, "until": until} if since or until else None,
    )
