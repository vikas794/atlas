"""SqlUsageLedger must neither leak connections nor block the event loop."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.domain.interfaces.usage_ledger import UsageRecord
from src.infrastructure.storage import db, sql
from src.infrastructure.storage.sql import SqlUsageLedger


def record(provider: str = "gemini", tokens: int = 10) -> UsageRecord:
    return UsageRecord(
        timestamp=datetime.now(UTC),
        provider=provider,
        operation="generate_notes",
        model="m",
        input_tokens=tokens,
        output_tokens=tokens,
        total_tokens=2 * tokens,
        cost_usd=0.01,
        cache_hit=False,
    )


@pytest.fixture
def tracked_connections(monkeypatch: pytest.MonkeyPatch):
    """Wrap storage.sql.connect so every opened connection and its thread is observable."""
    opened: list[dict] = []
    real_connect = db.connect

    def tracking_connect(path):
        conn = real_connect(path)
        entry = {"conn": conn, "closed": False, "thread": threading.get_ident()}
        original_close = conn.close

        class Proxy:
            def __getattr__(self, name):
                return getattr(conn, name)

            def close(self):
                entry["closed"] = True
                original_close()

        opened.append(entry)
        return Proxy()

    monkeypatch.setattr(sql, "connect", tracking_connect)
    return opened


async def test_every_connection_is_closed_after_each_operation(
    tmp_path: Path, tracked_connections: list[dict]
) -> None:
    ledger = SqlUsageLedger(tmp_path / "usage.sqlite3")

    await ledger.record_usage(record())
    aggregate = await ledger.get_aggregate()
    await ledger.get_by_provider()
    await ledger.get_by_operation()
    await ledger.get_cache_stats()
    await ledger.recent()

    assert aggregate.total_requests == 1 and aggregate.total_tokens == 20
    assert tracked_connections, "expected the ledger to open connections"
    assert all(entry["closed"] for entry in tracked_connections)


async def test_recording_runs_off_the_event_loop_thread(
    tmp_path: Path, tracked_connections: list[dict]
) -> None:
    ledger = SqlUsageLedger(tmp_path / "usage.sqlite3")
    loop_thread = threading.get_ident()
    tracked_connections.clear()  # ignore the table-creation connection made at construction

    await ledger.record_usage(record())

    record_threads = {entry["thread"] for entry in tracked_connections}
    assert record_threads and loop_thread not in record_threads


async def test_records_round_trip_and_the_dashboard_shape_is_kept(tmp_path: Path) -> None:
    ledger = SqlUsageLedger(tmp_path / "usage.sqlite3")
    await ledger.record_usage(record("gemini", 5))
    await ledger.record_usage(record("openrouter", 7))

    by_provider = {p.provider: p for p in await ledger.get_by_provider()}
    cache = await ledger.get_cache_stats()

    assert by_provider["gemini"].total_tokens == 10 and by_provider["openrouter"].total_tokens == 14
    # Cache statistics stay in the API shape (zeros) for the usage dashboard.
    assert (cache.total_hits, cache.hit_rate) == (0, 0.0)
