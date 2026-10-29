from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from src.domain.interfaces.usage_ledger import UsageLedgerPort
from src.transport.http.fastapi.dependencies import get_usage_ledger
from src.transport.http.fastapi.schemas.usage import (
    ErrorCategoryCount,
    ErrorDetailRow,
    KeyAnalyticsResponse,
    KeyAnalyticsRow,
    ModelCostRow,
    UsageAggregateResponse,
)

router = APIRouter(prefix="/api", tags=["usage"])

usage_ledger_dependency = Depends(get_usage_ledger)


def _rate(successes: int, requests: int) -> float:
    return round(successes * 100.0 / requests, 1) if requests else 0.0


@router.get("/usage/keys", response_model=KeyAnalyticsResponse)
async def get_key_analytics(
    range: str = Query("7d", pattern="^(24h|7d|30d|all)$", description="Time window"),
) -> KeyAnalyticsResponse:
    """Per-key success/error/cost, cost per model and error categories from the attempt log."""
    from src.transport.http.fastapi.dependencies import get_model_registry

    stats = get_model_registry().attempt_stats(range)
    by_key = [
        KeyAnalyticsRow(
            key_id=row["key_id"],
            label=row["label"] or None,
            provider=row["provider"],
            requests=row["requests"],
            successes=row["successes"],
            errors=row["requests"] - row["successes"],
            success_rate=_rate(row["successes"], row["requests"]),
            avg_latency_ms=round(row["avg_latency_ms"]),
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cost_usd=row["cost_usd"],
        )
        for row in stats["keys"]
    ]
    total_cost = sum(row["cost_usd"] for row in stats["models"])
    by_model = [
        ModelCostRow(
            provider=row["provider"],
            model=row["model"],
            requests=row["requests"],
            successes=row["successes"],
            errors=row["requests"] - row["successes"],
            success_rate=_rate(row["successes"], row["requests"]),
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            total_tokens=row["input_tokens"] + row["output_tokens"],
            cost_usd=row["cost_usd"],
            avg_cost_usd=row["cost_usd"] / row["requests"] if row["requests"] else 0.0,
            cost_share=round(row["cost_usd"] * 100.0 / total_cost, 1) if total_cost else 0.0,
        )
        for row in stats["models"]
    ]
    details: dict[str, list[ErrorDetailRow]] = {}
    for row in stats["error_details"]:
        details.setdefault(row["category"], []).append(
            ErrorDetailRow(**{k: v for k, v in row.items() if k != "category"})
        )
    total_requests = sum(row.requests for row in by_model)
    return KeyAnalyticsResponse(
        range=range,
        total_requests=total_requests,
        total_errors=sum(row.errors for row in by_model),
        total_cost_usd=total_cost,
        by_key=by_key,
        by_model=by_model,
        errors=[
            ErrorCategoryCount(**row, details=details.get(row["category"], []))
            for row in stats["errors"]
        ],
    )


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
