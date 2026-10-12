from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from backend.main import app
from src.transport.http.fastapi import dependencies as deps
from tests.conftest import ApiEnv


def test_lifespan_startup_and_health(api_env: ApiEnv) -> None:
    """The app starts cleanly within its lifespan; health and usage routes return 200 OK."""
    with TestClient(app) as client:
        health_resp = client.get("/api/health")
        assert health_resp.status_code == 200
        assert health_resp.json() == {"status": "ok", "service": "atlas-api"}

        usage_resp = client.get("/api/usage")
        assert usage_resp.status_code == 200
        data = usage_resp.json()
        assert "total_requests" in data
        assert "by_provider" in data
        assert "cache_stats" in data


def test_lifespan_starts_and_stops_the_real_worker(api_env: ApiEnv) -> None:
    """The background worker (processing, retries, publishing) lives in the app process."""
    worker = deps.get_worker()
    assert worker._loop_task is None

    with TestClient(app):
        assert worker._loop_task is not None and not worker._loop_task.done()

    assert worker._loop_task is None  # stop() ran on shutdown
    assert worker._active_tasks == set() or all(t.done() for t in worker._active_tasks)


def test_startup_applies_migrations_to_a_new_database(api_env: ApiEnv) -> None:
    assert not api_env.db_path.exists()

    with TestClient(app):
        pass

    assert api_env.db_path.exists()


def test_startup_logs_are_emitted_by_a_src_logger_at_info(api_env: ApiEnv) -> None:
    """Worker startup is logged by a src.* logger (dropped before logging was configured)."""
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Capture(level=logging.INFO)
    logging.getLogger().addHandler(handler)
    try:
        with TestClient(app):
            pass
    finally:
        logging.getLogger().removeHandler(handler)

    assert any("Starting PlaylistWorker" in r.getMessage() for r in records)
    assert logging.getLogger().getEffectiveLevel() <= logging.INFO
