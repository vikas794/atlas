from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from backend.storage.database import connect, now_iso, transaction
from src.domain.interfaces.storage import RunRepositoryPort
from src.domain.interfaces.usage_ledger import (
    CacheAggregate,
    OperationAggregate,
    ProviderAggregate,
    TimeRange,
    UsageAggregate,
    UsageLedgerPort,
    UsageRecord,
)


class SqlRunRepository(RunRepositoryPort):
    """Compatibility adapter wrapping backend.storage.repository.RunRepository.

    Delegates all methods to the existing implementation to avoid rewriting logic.
    """

    def __init__(self, db_path: str | Path, artifact_root: str | Path) -> None:
        from backend.storage.repository import RunRepository as BackendRunRepository

        self._repo = BackendRunRepository(db_path, artifact_root)

    def _conn(self):
        return self._repo._conn()

    @contextmanager
    def _tx(self) -> Iterator:
        with self._repo._tx() as conn:
            yield conn

    async def create_run(
        self,
        run_id: str,
        cache_key: str | None,
        search_query: str,
        normalized_query: str,
        max_videos: int,
        transcript_language: str,
        is_fallback: bool,
    ) -> str:
        return self._repo.create_run(
            run_id=run_id,
            cache_key=cache_key,
            search_query=search_query,
            normalized_query=normalized_query,
            max_videos=max_videos,
            transcript_language=transcript_language,
            is_fallback=is_fallback,
        )

    async def get_run(self, run_id: str) -> dict | None:
        return self._repo.get_run(run_id)

    async def list_runs(self) -> list[dict]:
        return self._repo.list_runs()

    async def latest_run(self) -> dict | None:
        return self._repo.latest_run()

    async def set_run_status(self, run_id: str, status: str, error: str | None = None) -> None:
        self._repo.set_run_status(run_id, status, error)

    async def set_videos(self, run_id: str, videos: list[dict]) -> None:
        self._repo.set_videos(run_id, videos)

    async def get_videos(self, run_id: str) -> list[dict]:
        return self._repo.get_videos(run_id)

    async def videos_state_hash(self, run_id: str) -> str:
        return self._repo.videos_state_hash(run_id)

    async def upsert_transcripts(
        self,
        run_id: str,
        transcripts: list[dict],
        settings: dict | None = None,
    ) -> None:
        self._repo.upsert_transcripts(run_id, transcripts, settings)

    async def get_transcripts(self, run_id: str) -> list[dict]:
        return self._repo.get_transcripts(run_id)

    async def transcripts_state_hash(self, run_id: str) -> str:
        return self._repo.transcripts_state_hash(run_id)

    async def upsert_summaries(
        self,
        run_id: str,
        summaries: list[dict],
        settings: dict | None = None,
    ) -> None:
        self._repo.upsert_summaries(run_id, summaries, settings)

    async def get_summaries(self, run_id: str) -> list[dict]:
        return self._repo.get_summaries(run_id)

    async def summaries_state_hash(self, run_id: str) -> str:
        return self._repo.summaries_state_hash(run_id)

    async def set_comparison(
        self,
        run_id: str,
        rows: list[dict],
        insights_report: dict,
        recommendations: dict,
        settings: dict | None = None,
        status: str = "succeeded",
        error: str | None = None,
    ) -> None:
        payload = {
            "rows": rows,
            "insights_report": insights_report,
            "recommendations": recommendations,
        }
        self._repo.set_comparison(run_id, payload, settings, status, error)

    async def get_comparison(
        self, run_id: str
    ) -> tuple[list[dict], dict, dict] | None:
        result = self._repo.get_comparison(run_id)
        if result is None:
            return None
        return result.get("rows", []), result.get("insights_report", {}), result.get("recommendations", {})

    async def upsert_assignments(
        self,
        run_id: str,
        assignments: list[dict],
        settings: dict | None = None,
    ) -> None:
        self._repo.upsert_assignments(run_id, assignments, settings)

    async def get_assignments(self, run_id: str) -> list[dict]:
        return self._repo.get_assignments(run_id)

    async def set_quiz_result(
        self,
        run_id: str,
        result: dict,
        settings: dict | None = None,
    ) -> None:
        self._repo.set_comparison(run_id, result, settings)

    async def get_quiz_result(self, run_id: str) -> dict | None:
        return self._repo.get_comparison(run_id)

    async def recompute_run_hashes(self, run_id: str) -> None:
        self._repo.recompute_run_hashes(run_id)

    async def mark_stale_derived(self, run_id: str) -> None:
        self._repo.mark_stale_derived(run_id)

    async def purge_expired(self, retention_days: int = 90) -> dict:
        return self._repo.purge_expired(retention_days)

    async def stats(self) -> dict:
        return self._repo.stats()

    async def start_job(self, run_id: str, job_type: str, metadata: dict | None = None) -> None:
        self._repo.start_job(run_id, job_type, metadata)

    async def fail_job(self, run_id: str, job_type: str, error: str) -> None:
        self._repo.fail_job(run_id, job_type, error)

    async def finish_job(self, run_id: str, job_type: str) -> None:
        self._repo.finish_job(run_id, job_type)

    async def find_cached_run(self, cache_key: str) -> dict | None:
        return self._repo.find_cached_run(cache_key)

    async def put_cache_entry(self, cache_key: str, kind: str, run_id: str, normalized_query: str, settings: dict, ttl_days: int) -> None:
        self._repo.put_cache_entry(cache_key, kind, run_id, normalized_query, settings, ttl_days)

    async def touch_cache_hit(self, cache_key: str) -> None:
        self._repo.touch_cache_hit(cache_key)


class SqlUsageLedger(UsageLedgerPort):
    """SQLite-backed usage ledger implementation.

    Provides both the domain protocol interface (record_usage, get_aggregate, etc.)
    and the refactor outline interface (record, aggregate, recent).
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._ensure_table()

    def _conn(self):
        return connect(self.db_path)

    def _ensure_table(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    run_id TEXT,
                    timestamp TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    estimated_cost_usd REAL NOT NULL DEFAULT 0.0,
                    currency TEXT NOT NULL DEFAULT 'USD',
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    success INTEGER NOT NULL DEFAULT 1,
                    error_category TEXT,
                    cache_hit INTEGER NOT NULL DEFAULT 0,
                    cache_key TEXT,
                    cache_namespace TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    metadata TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_timestamp ON usage_ledger(timestamp)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_provider ON usage_ledger(provider)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_model ON usage_ledger(model)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_operation ON usage_ledger(operation)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_run_id ON usage_ledger(run_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_request_id ON usage_ledger(request_id)"
            )

    # --- Refactor outline interface ---

    async def record(self, record: UsageRecord) -> None:
        """Record a usage event (refactor outline interface)."""
        request_id = str(uuid.uuid4())
        now = now_iso()
        with self._conn() as conn:
            with transaction(conn):
                conn.execute(
                    """
                    INSERT INTO usage_ledger (
                        request_id, run_id, timestamp, provider, model, operation,
                        input_tokens, output_tokens, total_tokens, estimated_cost_usd,
                        currency, latency_ms, success, error_category, cache_hit,
                        cache_key, cache_namespace, retry_count, metadata, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request_id,
                        record.run_id,
                        record.timestamp.isoformat(),
                        record.provider,
                        record.model,
                        record.operation,
                        record.input_tokens,
                        record.output_tokens,
                        record.total_tokens,
                        record.cost_usd,
                        "USD",
                        0,
                        1,
                        None,
                        1 if record.cache_hit else 0,
                        None,
                        None,
                        0,
                        None,
                        now,
                    ),
                )

    async def aggregate(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        operation: str | None = None,
        since: str | None = None,
        until: str | None = None,
        cache_status: bool | None = None,
    ) -> UsageAggregate:
        """Aggregate usage with optional filters (refactor outline interface)."""
        where_clauses = []
        params: list = []

        if provider is not None:
            where_clauses.append("provider = ?")
            params.append(provider)
        if model is not None:
            where_clauses.append("model = ?")
            params.append(model)
        if operation is not None:
            where_clauses.append("operation = ?")
            params.append(operation)
        if since is not None:
            where_clauses.append("timestamp >= ?")
            params.append(since)
        if until is not None:
            where_clauses.append("timestamp <= ?")
            params.append(until)
        if cache_status is not None:
            where_clauses.append("cache_hit = ?")
            params.append(1 if cache_status else 0)

        where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        with self._conn() as conn:
            row = conn.execute(
                f"""
                SELECT
                    COUNT(*) as total_requests,
                    SUM(input_tokens) as total_input_tokens,
                    SUM(output_tokens) as total_output_tokens,
                    SUM(total_tokens) as total_tokens,
                    SUM(estimated_cost_usd) as total_cost_usd,
                    AVG(CASE WHEN cache_hit = 1 THEN 1.0 ELSE 0.0 END) as cache_hit_rate
                FROM usage_ledger
                {where_sql}
                """,
                params,
            ).fetchone()

        return UsageAggregate(
            total_requests=row["total_requests"] or 0,
            total_input_tokens=row["total_input_tokens"] or 0,
            total_output_tokens=row["total_output_tokens"] or 0,
            total_tokens=row["total_tokens"] or 0,
            total_cost_usd=row["total_cost_usd"] or 0.0,
            cache_hit_rate=row["cache_hit_rate"] or 0.0,
        )

    async def recent(self, limit: int = 100) -> list[UsageRecord]:
        """Get recent usage records (refactor outline interface)."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT
                    timestamp, provider, model, operation,
                    input_tokens, output_tokens, total_tokens,
                    estimated_cost_usd, cache_hit, run_id
                FROM usage_ledger
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [
            UsageRecord(
                timestamp=datetime.fromisoformat(row["timestamp"]),
                provider=row["provider"],
                operation=row["operation"],
                model=row["model"],
                input_tokens=row["input_tokens"],
                output_tokens=row["output_tokens"],
                total_tokens=row["total_tokens"],
                cost_usd=row["estimated_cost_usd"],
                cache_hit=bool(row["cache_hit"]),
                run_id=row["run_id"],
                video_id=None,
            )
            for row in rows
        ]

    # --- Domain protocol interface (UsageLedgerPort) ---

    async def record_usage(self, record: UsageRecord) -> None:
        """Record a usage event (domain protocol interface)."""
        await self.record(record)

    async def get_aggregate(self, time_range: TimeRange = TimeRange.ALL) -> UsageAggregate:
        """Get aggregated usage statistics (domain protocol interface)."""
        where_clause, params = self._build_time_filter(time_range)
        with self._conn() as conn:
            row = conn.execute(
                f"""
                SELECT
                    COUNT(*) as total_requests,
                    SUM(input_tokens) as total_input_tokens,
                    SUM(output_tokens) as total_output_tokens,
                    SUM(total_tokens) as total_tokens,
                    SUM(estimated_cost_usd) as total_cost_usd,
                    AVG(CASE WHEN cache_hit = 1 THEN 1.0 ELSE 0.0 END) as cache_hit_rate
                FROM usage_ledger
                {where_clause}
                """,
                params,
            ).fetchone()

        return UsageAggregate(
            total_requests=row["total_requests"] or 0,
            total_input_tokens=row["total_input_tokens"] or 0,
            total_output_tokens=row["total_output_tokens"] or 0,
            total_tokens=row["total_tokens"] or 0,
            total_cost_usd=row["total_cost_usd"] or 0.0,
            cache_hit_rate=row["cache_hit_rate"] or 0.0,
        )

    async def get_by_provider(
        self, time_range: TimeRange = TimeRange.ALL
    ) -> list[ProviderAggregate]:
        where_clause, params = self._build_time_filter(time_range)
        with self._conn() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    provider,
                    COUNT(*) as total_requests,
                    SUM(total_tokens) as total_tokens,
                    SUM(estimated_cost_usd) as total_cost_usd,
                    GROUP_CONCAT(DISTINCT model) as models_used
                FROM usage_ledger
                {where_clause}
                GROUP BY provider
                ORDER BY total_cost_usd DESC
                """,
                params,
            ).fetchall()

        return [
            ProviderAggregate(
                provider=row["provider"],
                total_requests=row["total_requests"],
                total_tokens=row["total_tokens"] or 0,
                total_cost_usd=row["total_cost_usd"] or 0.0,
                models_used=row["models_used"].split(",") if row["models_used"] else [],
            )
            for row in rows
        ]

    async def get_by_operation(
        self, time_range: TimeRange = TimeRange.ALL
    ) -> list[OperationAggregate]:
        where_clause, params = self._build_time_filter(time_range)
        with self._conn() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    operation,
                    COUNT(*) as total_requests,
                    SUM(total_tokens) as total_tokens,
                    SUM(estimated_cost_usd) as total_cost_usd,
                    AVG(total_tokens) as avg_tokens_per_request
                FROM usage_ledger
                {where_clause}
                GROUP BY operation
                ORDER BY total_cost_usd DESC
                """,
                params,
            ).fetchall()

        return [
            OperationAggregate(
                operation=row["operation"],
                total_requests=row["total_requests"],
                total_tokens=row["total_tokens"] or 0,
                total_cost_usd=row["total_cost_usd"] or 0.0,
                avg_tokens_per_request=row["avg_tokens_per_request"] or 0.0,
            )
            for row in rows
        ]

    async def get_cache_stats(
        self, time_range: TimeRange = TimeRange.ALL
    ) -> CacheAggregate:
        where_clause, params = self._build_time_filter(time_range)
        with self._conn() as conn:
            row = conn.execute(
                f"""
                SELECT
                    SUM(CASE WHEN cache_hit = 1 THEN 1 ELSE 0 END) as total_hits,
                    SUM(CASE WHEN cache_hit = 0 THEN 1 ELSE 0 END) as total_misses
                FROM usage_ledger
                {where_clause}
                """,
                params,
            ).fetchone()

            total_hits = row["total_hits"] or 0
            total_misses = row["total_misses"] or 0
            total = total_hits + total_misses
            hit_rate = total_hits / total if total > 0 else 0.0

            by_kind_rows = conn.execute(
                f"""
                SELECT
                    operation,
                    SUM(CASE WHEN cache_hit = 1 THEN 1 ELSE 0 END) as hits,
                    SUM(CASE WHEN cache_hit = 0 THEN 1 ELSE 0 END) as misses
                FROM usage_ledger
                {where_clause}
                GROUP BY operation
                """,
                params,
            ).fetchall()

            by_kind = {
                r["operation"]: (r["hits"] or 0, r["misses"] or 0) for r in by_kind_rows
            }

        return CacheAggregate(
            total_hits=total_hits,
            total_misses=total_misses,
            hit_rate=hit_rate,
            by_kind=by_kind,
        )

    def _build_time_filter(self, time_range: TimeRange) -> tuple[str, list]:
        if time_range == TimeRange.ALL:
            return "", []

        now = datetime.now(UTC)
        if time_range == TimeRange.DAILY:
            since = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif time_range == TimeRange.WEEKLY:
            since = now.replace(hour=0, minute=0, second=0, microsecond=0)
            since = since.replace(day=since.day - 6)
        elif time_range == TimeRange.MONTHLY:
            since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            return "", []

        since_iso = since.isoformat(timespec="seconds")
        return "WHERE timestamp >= ?", [since_iso]
